from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.settings import settings
from app.db import connect
from app.storage_paths import resolve_artifact_path, unique_artifact_path
from app.repository import (
    get_periodic_report_setting,
    get_periodic_report_run,
    save_periodic_report_run,
    list_jobs,
)
from app.utils import safe_name

logger = logging.getLogger("app.periodic_reporter")


def is_last_day_of_month(dt: datetime) -> bool:
    """判断给定日期是否是当月的最后一天"""
    tomorrow = dt + timedelta(days=1)
    return tomorrow.day == 1


def _monthly_retry_deadline(target_date: date, setting: dict[str, Any]) -> datetime:
    raw_time = str(setting.get("retry_until_time") or "23:50").strip()
    try:
        hour, minute = map(int, raw_time.split(":"))
        limit_time = time(hour, minute)
    except (TypeError, ValueError):
        limit_time = time(23, 50)
    return datetime.combine(target_date, limit_time, tzinfo=ZoneInfo(settings.default_timezone))


def check_today_jobs_completed(now_dt: datetime) -> tuple[bool, list[str]]:
    """
    检查当天启用的所有定时巡检定时任务是否执行完毕。
    返回: (是否完成, 未完成的原因/任务列表)
    """
    today_str = now_dt.strftime("%Y-%m-%d")
    local_tz = ZoneInfo(settings.default_timezone)
    
    # 1. 检查最近 12 小时内是否有正在运行中的巡检任务（规避僵尸进程无限期等待的问题）
    limit_start = (datetime.now(local_tz) - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        running_count = conn.execute(
            """SELECT COUNT(*) FROM run_records
               WHERE status IN ('queued', 'running')
                 AND substr(started_at, 1, 10) = ? AND started_at >= ?""",
            (today_str, limit_start),
        ).fetchone()[0]
        if running_count > 0:
            return False, ["当前有正在执行中的巡检截图任务"]

    # 2. 找出所有已启用的任务，并判断今天它们是否应跑且是否已经跑过
    jobs = [j for j in list_jobs() if j.get("enabled")]
    unfinished = []
    
    for job in jobs:
        should_run_today = False
        expected_slots: dict[str, datetime | None] = {}
        schedule_mode = job.get("schedule_mode") or "cron"
        
        if schedule_mode == "calendar" and job.get("schedule_config"):
            try:
                from app.date_schedule import from_job, matches, resolved_time
                cfg = from_job(job)
                should_run_today = matches(now_dt.date(), cfg) is True if cfg else False
                if should_run_today:
                    for value in cfg["times"]:
                        expected_slots[f"calendar-{value.replace(':', '')}"] = datetime.combine(
                            now_dt.date(), resolved_time(value, now_dt.date(), int(job["id"])), tzinfo=local_tz,
                        )
            except Exception as e:
                logger.error("解析任务 '%s' 日期计划失败: %s", job["name"], e)
        elif schedule_mode == "simple" and job.get("schedule_config"):
            try:
                cfg = json.loads(job["schedule_config"])
                freq = cfg.get("frequency")
                if freq == "daily":
                    should_run_today = True
                elif freq == "workday":
                    if now_dt.isoweekday() <= 5: # 周一至周五
                        should_run_today = True
                elif freq == "weekly":
                    cfg_w = int(cfg.get("day_of_week") or 1)
                    if cfg_w == now_dt.isoweekday():
                        should_run_today = True
                elif freq == "monthly":
                    cfg_m = int(cfg.get("day_of_month") or 1)
                    if cfg_m == now_dt.day:
                        should_run_today = True
                elif freq == "monthly_last":
                    # 判断今天是否为当月最后一天
                    import calendar
                    last_day = calendar.monthrange(now_dt.year, now_dt.month)[1]
                    if now_dt.day == last_day:
                        should_run_today = True
                if should_run_today:
                    for slot in ("morning", "afternoon"):
                        if cfg.get(f"{slot}_enabled"):
                            expected_slots[slot] = None
            except Exception as e:
                logger.error(f"解析任务 '{job['name']}' schedule_config 发生异常: {e}")
                should_run_today = True
        else:
            # 旧表达式计划已暂停，不作为当天应执行任务。
            continue

        if should_run_today:
            # 每个计划时段都要有完成记录；手动运行不能替代计划运行。
            with connect() as conn:
                runs = conn.execute(
                    """
                    SELECT schedule_slot, status, started_at FROM run_records
                    WHERE job_id = ? AND substr(started_at, 1, 10) = ?
                    ORDER BY id DESC
                    """,
                    (job["id"], today_str),
                ).fetchall()

            for slot, latest_fire in expected_slots.items():
                from app.report import _run_period, is_daily_schedule_slot
                expected_period = _run_period({"schedule_slot": slot, "started_at": now_dt.isoformat()}, local_tz)
                matching = []
                for run in runs:
                    if not is_daily_schedule_slot(run["schedule_slot"]):
                        continue
                    if _run_period(dict(run), local_tz) == expected_period:
                        matching.append(run)
                if not matching:
                    unfinished.append(f"任务 '{job['name']}' 的 {slot} 时段今日尚未执行")
                elif matching[0]["status"] == "running":
                    unfinished.append(f"任务 '{job['name']}' 的 {slot} 时段正在截图中")
            if not expected_slots:
                unfinished.append(f"任务 '{job['name']}' 今日计划无法确认")
                    
    if unfinished:
        return False, unfinished
    return True, []


def monthly_report_retry_check(run_id: int) -> None:
    """月报的后台重试检测函数，完全基于配置参数执行"""
    run = get_periodic_report_run(run_id)
    if not run or run["status"] not in ("waiting", "running"):
        logger.info(f"月报重试任务被忽略，因为运行历史 ID {run_id} 状态已不再是 waiting/running")
        return

    setting = get_periodic_report_setting(run["report_type"])
    if not setting:
        logger.error(f"月报重试任务无法加载周期报告配置: {run['report_type']}")
        return

    local_tz = ZoneInfo(settings.default_timezone)
    now_dt = datetime.now(local_tz)
    try:
        target_date = date.fromisoformat(str(run["period_end"])[:10])
    except (TypeError, ValueError):
        run["status"] = "failed"
        run["error_summary"] = "月报目标日期无效，无法安全完成等待检查"
        run["finished_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        save_periodic_report_run(run, run_id)
        logger.error("月报重试任务 %s 的 period_end 无效", run_id)
        return
    
    # 1. 读取页面配置重试截止时间（如 23:50）与检测重试间隔
    retry_until_str = str(setting.get("retry_until_time") or "23:50").strip()
    retry_interval = int(setting.get("retry_interval_minutes") or 10)
    send_on_timeout = int(setting.get("send_on_timeout") if setting.get("send_on_timeout") is not None else 1)
    
    retry_deadline = _monthly_retry_deadline(target_date, setting)
    limit_time = retry_deadline.time()
    target_check_dt = datetime.combine(target_date, time.max, tzinfo=local_tz)
    completed, reasons = check_today_jobs_completed(target_check_dt)
    
    if completed:
        logger.info("重试检测通过：目标日 %s 的巡检任务已完成，开始生成并发送月报！", target_date)
        run["status"] = "running"
        save_periodic_report_run(run, run_id)
        execute_periodic_report_flow(run_id)
    else:
        if now_dt >= retry_deadline:
            if send_on_timeout == 1:
                logger.warning(f"已达到重试时间上限 {limit_time}，部分任务仍未完成，根据配置[强行发送已完成部分]。")
                run["status"] = "running"
                run["error_summary"] = f"月报未完整生成：在 {retry_until_str} 截止前以下任务未完成: " + "; ".join(reasons)
                save_periodic_report_run(run, run_id)
                execute_periodic_report_flow(run_id, force_warning=True, unfinished_reasons=reasons)
            else:
                logger.warning(f"已达到重试时间上限 {limit_time}，部分任务仍未完成，根据配置[直接标记失败且不发送]。")
                run["status"] = "failed"
                run["error_summary"] = f"月报生成失败：在 {retry_until_str} 截止前以下任务未完成: " + "; ".join(reasons)
                run["finished_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
                save_periodic_report_run(run, run_id)
        else:
            logger.info(f"月报仍需等待，部分任务未完成。原因: {reasons}。{retry_interval} 分钟后重试。")
            from app.scheduler import scheduler
            run_time = now_dt + timedelta(minutes=retry_interval)
            scheduler.add_job(
                monthly_report_retry_check,
                "date",
                run_date=run_time,
                args=[run_id],
                id=f"monthly-retry-{run_id}",
                replace_existing=True
            )


def trigger_periodic_report(report_type: str, is_manual: bool = False) -> int | None:
    from app.job_execution import JobAlreadyRunning, acquire_job_lease

    if report_type not in ("weekly", "monthly"):
        return None
    try:
        lease = acquire_job_lease(-1 if report_type == "weekly" else -2)
    except JobAlreadyRunning:
        logger.info("%s 报告正在生成，跳过重复触发", report_type)
        return None
    try:
        return _trigger_periodic_report_unlocked(report_type, is_manual)
    finally:
        lease.close()


def _trigger_periodic_report_unlocked(report_type: str, is_manual: bool = False) -> int | None:
    """
    触发周期报告的处理流。
    返回: 报告运行的 ID
    """
    setting = get_periodic_report_setting(report_type)
    if not setting:
        logger.warning(f"周期报告配置不存在: {report_type}")
        return None
        
    if not setting["enabled"] and not is_manual:
        logger.info(f"周期报告 '{setting['name']}' 未启用，跳过自动执行。")
        return None

    local_tz = ZoneInfo(settings.default_timezone)
    now_dt = datetime.now(local_tz)

    # 1. 如果是月报且是自动触发，检查是否是当月最后一天
    if report_type == "monthly" and not is_manual:
        if not is_last_day_of_month(now_dt):
            logger.info("今天不是当月的最后一天，跳过月报触发。")
            return None

    if report_type == "weekly":
        today_weekday = now_dt.weekday()
        if is_manual:
            # 手动立即生成：如果本周已有巡检则汇总本周至今，否则汇总上一完整周
            this_week_start = now_dt - timedelta(days=today_weekday)
            cand_start = datetime.combine(this_week_start.date(), time.min).replace(tzinfo=local_tz)
            cand_end = datetime.combine(now_dt.date(), time.max).replace(tzinfo=local_tz)
            with connect() as conn:
                count_this_week = conn.execute(
                    "SELECT COUNT(DISTINCT coalesce(cast(job_id as text), job_name) || ':' || substr(started_at, 1, 10)) FROM run_records WHERE substr(replace(started_at, 'T', ' '), 1, 19) >= ? AND report_path IS NOT NULL AND report_path != ''",
                    (cand_start.strftime("%Y-%m-%d %H:%M:%S"),)
                ).fetchone()[0]
            if count_this_week > 0:
                start_dt = cand_start
                end_dt = cand_end
            else:
                last_week_start = now_dt - timedelta(days=today_weekday + 7)
                last_week_end = now_dt - timedelta(days=today_weekday + 1)
                start_dt = datetime.combine(last_week_start.date(), time.min).replace(tzinfo=local_tz)
                end_dt = datetime.combine(last_week_end.date(), time.max).replace(tzinfo=local_tz)
        else:
            # 计划定时触发：汇总上一完整周（上周一 00:00:00 至上周日 23:59:59）
            last_week_start = now_dt - timedelta(days=today_weekday + 7)
            last_week_end = now_dt - timedelta(days=today_weekday + 1)
            start_dt = datetime.combine(last_week_start.date(), time.min).replace(tzinfo=local_tz)
            end_dt = datetime.combine(last_week_end.date(), time.max).replace(tzinfo=local_tz)
    else:
        # monthly
        # 本月1号 00:00:00 至本月最后一天 23:59:59
        # 如果是手动触发且今天不是当月最后一天，截止时间设为当天 23:59:59
        first_day = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if is_manual and not is_last_day_of_month(now_dt):
            last_day = now_dt.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            import calendar
            last_day_num = calendar.monthrange(now_dt.year, now_dt.month)[1]
            last_day = now_dt.replace(day=last_day_num, hour=23, minute=59, second=59, microsecond=0)
        
        start_dt = first_day
        end_dt = last_day

    period_start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    period_end = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 3. 创建周期报告运行历史记录
    run_data = {
        "report_type": report_type,
        "period_start": period_start,
        "period_end": period_end,
        "status": "running",
        "zip_path": "",
        "mail_status": "",
        "error_summary": "",
        "finished_at": None,
    }
    
    # 4. 月报等待当天巡检任务完成逻辑
    if report_type == "monthly" and setting["wait_for_daily_jobs"] and not is_manual:
        completed, reasons = check_today_jobs_completed(now_dt)
        if not completed:
            run_data["status"] = "waiting"
            run_data["error_summary"] = "正在等待以下巡检完成: " + "; ".join(reasons)
            run_id = save_periodic_report_run(run_data)
            
            retry_interval = int(setting.get("retry_interval_minutes") or 10)
            if now_dt >= _monthly_retry_deadline(end_dt.date(), setting):
                logger.info("月报目标日已过重试截止时间，立即检查并结束等待：%s", reasons)
                monthly_report_retry_check(run_id)
                return run_id
            logger.info(f"月报已挂起，状态为 waiting。未完成原因: {reasons}。{retry_interval} 分钟后重试。")
            
            from app.scheduler import scheduler
            run_time = datetime.now() + timedelta(minutes=retry_interval)
            scheduler.add_job(
                monthly_report_retry_check,
                "date",
                run_date=run_time,
                args=[run_id],
                id=f"monthly-retry-{run_id}",
                replace_existing=True
            )
            return run_id

    run_id = save_periodic_report_run(run_data)
    execute_periodic_report_flow(run_id)
    return run_id


def _run_local_date(run: dict[str, Any]) -> str:
    started_at = str(run.get("started_at") or "")
    if started_at:
        try:
            if "T" in started_at:
                dt = datetime.fromisoformat(started_at)
            else:
                dt = datetime.strptime(started_at[:19], "%Y-%m-%d %H:%M:%S")
            if dt.tzinfo is not None:
                dt = dt.astimezone(ZoneInfo(settings.default_timezone))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return str(run.get("started_at") or "")[:10] or "unknown-date"


def _unique_zip_filename(src_report: Path, run: dict[str, Any], used_names: set[str]) -> str:
    base_name = safe_name(src_report.stem, "report")
    suffix = src_report.suffix or ".docx"
    candidate = f"{base_name}{suffix}"
    counter = 2
    while candidate in used_names:
        candidate = f"{base_name}-{counter}{suffix}"
        counter += 1
    used_names.add(candidate)
    return candidate


def execute_periodic_report_flow(run_id: int, force_warning: bool = False, unfinished_reasons: list[str] | None = None) -> None:
    """正式执行周期报告汇总打包及通知逻辑"""
    run = get_periodic_report_run(run_id)
    if not run:
        return
        
    report_type = run["report_type"]
    period_start = run["period_start"]
    period_end = run["period_end"]

    setting = get_periodic_report_setting(report_type)
    if not setting:
        logger.error(f"无法为周期报告运行 ID {run_id} 找到配置设定。")
        run["status"] = "failed"
        run["error_summary"] = "周期报告配置不存在"
        run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_periodic_report_run(run, run_id)
        return

    logger.info(f"正在为周期报告 '{setting['name']}' 运行打包归档流程 (ID: {run_id})")

    # 1. One final Word per task and day, manual or scheduled.
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, job_id, job_name, status, report_path, started_at, finished_at, success_count, failed_count
            FROM run_records
            WHERE substr(replace(started_at, 'T', ' '), 1, 19) >= ?
              AND substr(replace(started_at, 'T', ' '), 1, 19) <= ?
              AND report_path IS NOT NULL
              AND report_path != ''
            ORDER BY substr(replace(started_at, 'T', ' '), 1, 19) ASC, id ASC
            """,
            (period_start, period_end),
        ).fetchall()

    from app.report import _run_period

    latest_run_by_job_date: dict[tuple[Any, str], dict[str, Any]] = {}
    latest_run_by_period: dict[tuple[Any, str, str], dict[str, Any]] = {}
    local_tz = ZoneInfo(settings.default_timezone)
    for row in rows:
        run_item = dict(row)
        src_report = resolve_artifact_path(run_item.get("report_path") or "")
        if not src_report.exists():
            logger.warning(
                "周期报告跳过运行 ID %s：数据库记录的 Word 文档不存在: %s",
                run_item.get("id"),
                run_item.get("report_path"),
            )
            continue
        run_item["_resolved_report_path"] = str(src_report)
        group_key = (run_item.get("job_id") or run_item.get("job_name") or run_item.get("id"), _run_local_date(run_item))
        latest_run_by_job_date[group_key] = run_item
        latest_run_by_period[(*group_key, _run_period(run_item, local_tz))] = run_item

    runs_in_period = list(latest_run_by_job_date.values())

    # 2. 如果没有任何报告，且配置为不发送空报告
    if not runs_in_period and not setting["send_empty_report"]:
        run["status"] = "success"
        run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_periodic_report_run(run, run_id)
        logger.info(f"周期报告运行 ID {run_id} 已跳过，因为没有可汇总的报告文件。")
        return

    # 3. 开始打包 Word 文档与截图，使用中文友好命名格式
    if report_type == "weekly":
        start_date = period_start[:10]
        end_date = period_end[:10]
        zip_stem = f"周报_{start_date}_至_{end_date}"
    else:
        year_month = period_start[:7]
        zip_stem = f"月报_{year_month}"

    generated_at = datetime.now(ZoneInfo(settings.default_timezone))
    zip_dir = settings.report_dir / generated_at.strftime("%Y") / generated_at.strftime("%m")
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_filepath = unique_artifact_path(zip_dir, zip_stem, ".zip")

    temp_dir = Path(tempfile.mkdtemp(prefix=f"report_temp_{run_id}_"))

    try:
        # 拷贝周期内每日最终 Word 文档，平铺放入 ZIP 根目录
        include_docx = int(setting.get("include_docx") or 0)
        include_screenshots = int(setting.get("include_screenshots") or 0)
        used_report_names: set[str] = set()
        
        for r in runs_in_period:
            src_report = Path(r["_resolved_report_path"])
            report_name = _unique_zip_filename(src_report, r, used_report_names)
            if include_docx:
                report_target = temp_dir / report_name
                shutil.copy2(src_report, report_target)
                logger.info(
                    "拷贝运行 ID %s 的每日最终文档 %s 到周期压缩包根目录: %s",
                    r.get("id"),
                    src_report.name,
                    report_name,
                )
                
            if include_screenshots:
                screenshots_temp_dir = temp_dir / "screenshots" / Path(report_name).stem
                screenshots_temp_dir.mkdir(parents=True, exist_ok=True)
                group_key = (r.get("job_id") or r.get("job_name") or r.get("id"), _run_local_date(r))
                for period in ("上午", "下午"):
                    source_run = latest_run_by_period.get((*group_key, period))
                    if not source_run or source_run["_resolved_report_path"] != r["_resolved_report_path"]:
                        continue
                    with connect() as conn:
                        results_rows = conn.execute(
                            "SELECT item_name, file_path, status FROM screenshot_results WHERE run_id = ? AND status = 'success'",
                            (source_run["id"],)
                        ).fetchall()
                    for res in results_rows:
                        if res["file_path"]:
                            img_path = resolve_artifact_path(res["file_path"])
                            if img_path.exists():
                                shutil.copy2(img_path, screenshots_temp_dir / img_path.name)

        # 压缩临时文件夹
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zip_file.write(file_path, arcname)

        run["zip_path"] = str(zip_filepath)
        logger.info(f"周期报告压缩包成功创建: {zip_filepath}")

    except Exception as exc:
        err_msg = f"打包压缩周期报告发生异常: {exc}"
        logger.error(err_msg, exc_info=True)
        run["status"] = "failed"
        run["error_summary"] = err_msg
        run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_periodic_report_run(run, run_id)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        return
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    run["status"] = "success" if not force_warning else "partial_success"

    # 先保存归档结果，通知异常不能让已完成的报告一直显示运行中。
    run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_periodic_report_run(run, run_id)

    # 4. 钉钉通知推送 (当配置了 dingtalk_enabled == 1 时进行推送)
    if setting.get("dingtalk_enabled") == 1:
        from app.utils import decrypt_secret
        from app.dingtalk import send_dingtalk_msg
        from app.repository import get_system_setting

        from app.repository import get_dingtalk_profile, list_dingtalk_profiles
        bot_profile = None
        selected_bot_id = setting.get("dingtalk_profile_id")
        if selected_bot_id:
            bot_profile = get_dingtalk_profile(selected_bot_id)
            if not bot_profile:
                run["dingtalk_status"] = f"failed: 指定的钉钉机器人 #{selected_bot_id} 不存在，未发送通知"
                logger.error("周期报告 %s 的%s", run_id, run["dingtalk_status"])
                save_periodic_report_run(run, run_id)
                return
        else:
            all_bots = list_dingtalk_profiles()
            if all_bots:
                bot_profile = all_bots[0]

        if bot_profile:
            webhook = decrypt_secret(bot_profile.get("webhook", "")) if bot_profile.get("webhook") else ""
            secret = decrypt_secret(bot_profile.get("secret", "")) if bot_profile.get("secret") else ""
            keyword = bot_profile.get("keyword", "") or ""
            emoji_enabled = bool(bot_profile.get("emoji_enabled", 1))
        else:
            db_webhook = get_system_setting("dingtalk_webhook", "")
            if db_webhook:
                try:
                    db_webhook = decrypt_secret(db_webhook)
                except Exception:
                    pass
            webhook = db_webhook
            db_secret = get_system_setting("dingtalk_secret", "")
            if db_secret:
                try:
                    db_secret = decrypt_secret(db_secret)
                except Exception:
                    pass
            secret = db_secret
            keyword = get_system_setting("dingtalk_keyword", "")
            emoji_enabled = get_system_setting("dingtalk_emoji_enabled", "0") == "1"

        if webhook:
            # 归档包大小
            size_str = "-"
            try:
                if zip_filepath.exists():
                    size_bytes = zip_filepath.stat().st_size
                    size_str = (
                        f"{size_bytes / 1024:.2f} KB"
                        if size_bytes < 1024 * 1024
                        else f"{size_bytes / (1024 * 1024):.2f} MB"
                    )
            except Exception:
                pass

            from app.dingtalk_builder import build_periodic_report_message

            # 构建消息
            title, text = build_periodic_report_message(
                setting_name=setting["name"],
                report_type=report_type,
                period_start=period_start,
                period_end=period_end,
                status=run["status"],
                run_count=len(runs_in_period),
                include_docx=bool(setting.get("include_docx", 1) == 1),
                zip_size_str=size_str,
                error_summary=run.get("error_summary") or "",
                unfinished_reasons=unfinished_reasons if force_warning else [],
                enable_emoji=emoji_enabled,
            )

            logger.info("正在发送周期报告钉钉 Webhook 推送...")
            dt_status = send_dingtalk_msg(webhook, secret, keyword, title, text)
            run["dingtalk_status"] = dt_status
        else:
            logger.warning(f"周期报告配置 '{setting['name']}' 启用了钉钉推送但 Webhook 为空，跳过。")
            run["dingtalk_status"] = "skipped: Webhook 为空"
    else:
        run["dingtalk_status"] = "skipped: 未启用钉钉通知"

    run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_periodic_report_run(run, run_id)
    logger.info(f"周期报告 ID {run_id} 流程执行完毕。状态: {run['status']}，钉钉状态: {run['dingtalk_status']}")
