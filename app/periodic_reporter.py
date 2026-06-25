from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.settings import settings
from app.db import connect
from app.storage_paths import resolve_artifact_path
from app.repository import (
    get_periodic_report_setting,
    get_periodic_report_run,
    save_periodic_report_run,
    get_mail_profile,
    list_jobs,
)
from app.mailer import send_periodic_report_mail
from app.utils import safe_name

logger = logging.getLogger("app.periodic_reporter")


def is_last_day_of_month(dt: datetime) -> bool:
    """判断给定日期是否是当月的最后一天"""
    tomorrow = dt + timedelta(days=1)
    return tomorrow.day == 1


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
            "SELECT COUNT(*) FROM run_records WHERE status = 'running' AND started_at >= ?",
            (limit_start,)
        ).fetchone()[0]
        if running_count > 0:
            return False, ["当前有正在执行中的巡检截图任务"]

    # 2. 找出所有已启用的任务，并判断今天它们是否应跑且是否已经跑过
    jobs = [j for j in list_jobs() if j.get("enabled")]
    unfinished = []
    
    for job in jobs:
        should_run_today = False
        schedule_mode = job.get("schedule_mode") or "cron"
        
        if schedule_mode == "simple" and job.get("schedule_config"):
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
            except Exception as e:
                logger.error(f"解析任务 '{job['name']}' schedule_config 发生异常: {e}")
                should_run_today = True
        else:
            # 3. 对高级 Cron 任务：利用 CronTrigger 判定今天是否有任何触发火化时间点，避免每天都卡在等待列表中
            cron_parts = [p.strip() for p in (job.get("cron_expression") or "").split(";") if p.strip()]
            should_run_today = False
            
            # 今天 00:00 之后，一天内的最晚可能触发时间是今天 23:59:59
            today_start = datetime.combine(now_dt.date(), time.min).replace(tzinfo=local_tz)
            from apscheduler.triggers.cron import CronTrigger
            
            for cron_str in cron_parts:
                cron_str_test = cron_str.replace("L", "*")
                parts = cron_str_test.split()
                if len(parts) == 6:
                    cron_str_test = " ".join(parts[1:])
                try:
                    trigger = CronTrigger.from_crontab(cron_str_test, timezone=local_tz)
                    next_fire = trigger.get_next_fire_time(None, today_start)
                    if next_fire and next_fire.date() == now_dt.date():
                        should_run_today = True
                        break
                except Exception as e:
                    logger.error(f"预测高级 Cron '{cron_str}' 在今天触发时间出错: {e}")
                    should_run_today = True # 降级容错
                    break

        if should_run_today:
            # 查询该任务今天是否有已运行完成的历史记录
            with connect() as conn:
                run = conn.execute(
                    """
                    SELECT id, status FROM run_records 
                    WHERE job_id = ? AND started_at LIKE ? 
                    ORDER BY id DESC LIMIT 1
                    """,
                    (job["id"], f"{today_str}%")
                ).fetchone()
                
                if not run:
                    unfinished.append(f"任务 '{job['name']}' 今日尚未触发执行")
                elif run["status"] == "running":
                    unfinished.append(f"任务 '{job['name']}' 正在截图中")
                    
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
    
    # 1. 读取页面配置重试截止时间（如 23:50）与检测重试间隔
    retry_until_str = str(setting.get("retry_until_time") or "23:50").strip()
    retry_interval = int(setting.get("retry_interval_minutes") or 10)
    send_on_timeout = int(setting.get("send_on_timeout") if setting.get("send_on_timeout") is not None else 1)
    
    try:
        limit_h, limit_m = map(int, retry_until_str.split(":"))
        limit_time = time(limit_h, limit_m)
    except Exception:
        limit_time = time(23, 50)
        
    current_time = now_dt.time()
    completed, reasons = check_today_jobs_completed(now_dt)
    
    if completed:
        logger.info("重试检测通过：本日所有巡检任务已完成，开始生成并发送月报！")
        run["status"] = "running"
        save_periodic_report_run(run, run_id)
        execute_periodic_report_flow(run_id)
    else:
        if current_time >= limit_time:
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
                run["mail_status"] = "failed: 巡检任务超时未完成且配置不发信"
                run["finished_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
                save_periodic_report_run(run, run_id)
        else:
            logger.info(f"月报仍需等待，部分任务未完成。原因: {reasons}。{retry_interval} 分钟后重试。")
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


def trigger_periodic_report(report_type: str, is_manual: bool = False) -> int | None:
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

    # 2. 计算周期报告时间范围（起止日期）
    if report_type == "weekly":
        today_weekday = now_dt.weekday()
        # 上周一 00:00:00 至上周日 23:59:59
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


def execute_periodic_report_flow(run_id: int, force_warning: bool = False, unfinished_reasons: list[str] | None = None) -> None:
    """正式执行周期报告汇总打包及发信逻辑"""
    run = get_periodic_report_run(run_id)
    if not run:
        return
        
    report_type = run["report_type"]
    period_start = run["period_start"]
    period_end = run["period_end"]

    setting = get_periodic_report_setting(report_type)
    if not setting:
        logger.error(f"无法为周期报告运行 ID {run_id} 找到配置设定。")
        return

    logger.info(f"正在为周期报告 '{setting['name']}' 运行打包发信流程 (ID: {run_id})")

    # 1. 查询此时间段内的所有巡检记录，拿到各个任务的最新成功生成的 word 文档
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, job_id, job_name, report_path, started_at
            FROM run_records
            WHERE started_at >= ? AND started_at <= ? AND report_path IS NOT NULL AND report_path != ''
            ORDER BY id ASC
            """,
            (period_start, period_end),
        ).fetchall()
        
    runs_in_period = [dict(r) for r in rows]

    # 按 job_id 分组，并只保留最新的一笔
    latest_run_by_job: dict[int, dict[str, Any]] = {}
    for r in runs_in_period:
        job_id = r["job_id"]
        if job_id:
            latest_run_by_job[job_id] = r

    # 2. 如果没有任何报告，且配置为不发送空报告
    if not latest_run_by_job and not setting["send_empty_report"]:
        run["status"] = "success"
        run["mail_status"] = "skipped: 没有可汇总的巡检报告"
        run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_periodic_report_run(run, run_id)
        logger.info(f"周期报告运行 ID {run_id} 已跳过，因为没有可汇总的报告文件。")
        return

    # 3. 开始打包 Word 文档与截图，使用中文友好命名格式
    if report_type == "weekly":
        start_date = period_start[:10]
        end_date = period_end[:10]
        zip_filename = f"周报_{start_date}_至_{end_date}.zip"
    else:
        year_month = period_start[:7]
        zip_filename = f"月报_{year_month}.zip"

    zip_dir = settings.data_dir / "periodic-reports"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_filepath = zip_dir / zip_filename

    temp_dir = Path(tempfile.mkdtemp(prefix=f"report_temp_{run_id}_"))

    try:
        manifest_data = {
            "report_type": report_type,
            "period_start": period_start,
            "period_end": period_end,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "jobs": []
        }

        # 拷贝文档和截图
        include_screenshots = int(setting.get("include_screenshots") or 0)
        
        for job_id, r in latest_run_by_job.items():
            job_folder_name = safe_name(r["job_name"], "job_" + str(job_id))
            job_temp_dir = temp_dir / job_folder_name
            job_temp_dir.mkdir(parents=True, exist_ok=True)
            
            report_copied_path = ""
            src_report = resolve_artifact_path(r["report_path"])
            if src_report.exists():
                shutil.copy2(src_report, job_temp_dir / src_report.name)
                report_copied_path = f"{job_folder_name}/{src_report.name}"
                logger.info(f"拷贝文档 {src_report.name} 到打包临时文件夹 '{job_folder_name}'")
                
            # 打包成功的截图
            screenshot_count = 0
            with connect() as conn:
                results_rows = conn.execute(
                    "SELECT item_name, file_path, status FROM screenshot_results WHERE run_id = ? AND status = 'success'",
                    (r["id"],)
                ).fetchall()
                
            if include_screenshots:
                screenshots_temp_dir = job_temp_dir / "screenshots"
                screenshots_temp_dir.mkdir(parents=True, exist_ok=True)
                for res in results_rows:
                    if res["file_path"]:
                        img_path = resolve_artifact_path(res["file_path"])
                        if img_path.exists():
                            shutil.copy2(img_path, screenshots_temp_dir / img_path.name)
                            screenshot_count += 1
            else:
                for res in results_rows:
                    if res["file_path"] and resolve_artifact_path(res["file_path"]).exists():
                        screenshot_count += 1

            manifest_data["jobs"].append({
                "job_name": r["job_name"],
                "run_id": r["id"],
                "run_time": r["started_at"],
                "status": r.get("status") or "success",
                "report_file": report_copied_path,
                "screenshot_count": screenshot_count
            })

        # 写入 manifest.json 记录运行时间、状态、报告路径及截图数量
        manifest_path = temp_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)

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

    # 4. 获取邮件配置发信
    mail_profile = get_mail_profile(setting["mail_profile_id"])
    if not mail_profile:
        with connect() as conn:
            first_mail = conn.execute("SELECT * FROM mail_profiles ORDER BY id LIMIT 1").fetchone()
            mail_profile = dict(first_mail) if first_mail else None

    if not mail_profile:
        run["status"] = "success"
        run["mail_status"] = "skipped: 系统中没有可用的发信配置"
        run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_periodic_report_run(run, run_id)
        logger.warning("发信失败：系统没有任何邮件配置。")
        return

    # 5. 构建邮件主题和正文
    report_type_cn = "周报" if report_type == "weekly" else "月报"
    subject = f"【{report_type_cn}】{setting['name']}汇总报告 ({period_start[:10]} 至 {period_end[:10]})"
    
    if force_warning:
        subject = f"⚠️【{report_type_cn}告警】{setting['name']}汇总报告 (数据不完整) - {period_start[:10]}"
        body = (
            f"您好，这是一封自动发送的{report_type_cn}汇总邮件。\n\n"
            f"注意：由于已达到重试的最晚时间上限({setting.get('retry_until_time', '23:50')})，部分定时巡检任务仍未完成，本期汇总报告已强行生成打包。以下任务在截止前未完成:\n"
            f"{'; '.join(unfinished_reasons or [])}\n\n"
            f"汇总时间范围: {period_start} 至 {period_end}\n"
            f"本期汇总 Word 报告文件已作为附件随信发送，请查收。\n"
        )
    else:
        body = (
            f"您好，这是一封系统自动发送的{report_type_cn}汇总邮件。\n\n"
            f"本期周期: {period_start} 至 {period_end}\n"
            f"本期汇总的巡检 Word 报告文档及截图已压缩打包并作为附件随信发送，请查收。\n"
        )

    # 6. 发信并更新状态
    try:
        recipients_override = setting["recipients_override"].strip() if setting.get("recipients_override") else None
        mail_status = send_periodic_report_mail(
            mail_profile=mail_profile,
            subject=subject,
            body=body,
            attachment_path=str(zip_filepath),
            recipients_override=recipients_override
        )
        run["mail_status"] = mail_status
        run["status"] = "success" if not force_warning else "partial_success"
    except Exception as exc:
        err_msg = f"发送周期报告邮件出错: {exc}"
        logger.error(err_msg, exc_info=True)
        run["mail_status"] = f"failed: {exc}"
        run["status"] = "partial_success"
        run["error_summary"] = err_msg

    run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_periodic_report_run(run, run_id)
    logger.info(f"周期报告 ID {run_id} 流程执行完毕。状态: {run['status']}，邮件状态: {run['mail_status']}")
