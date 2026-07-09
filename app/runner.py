import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.settings import settings

from app.mailer import send_report_mail
from app.report import generate_docx
from app.repository import (
    add_screenshot_result,
    create_run,
    get_job,
    get_mail_profile,
    get_run,
    list_screenshot_items,
    list_screenshot_results,
    update_run,
)
from app.screenshot import capture_item
from playwright.sync_api import sync_playwright

logger = logging.getLogger("app.runner")


def run_job(job_id: int, run_id: int | None = None, schedule_type: str | None = None) -> int:
    is_scheduled = (run_id is None)
    job = get_job(job_id)
    if not job:
        logger.error(f"任务不存在: {job_id}")
        raise RuntimeError(f"任务不存在：{job_id}")

    # 如果是简单巡检定时模式，且是自动定时触发的，增加“同天同时间段只成功执行一次”的判定，
    # 防止由于执行完洗牌重新调度、手动保存任务配置或升级重启容器重新洗牌导致的重复执行。
    if is_scheduled and job.get("schedule_mode") == "simple" and schedule_type in ("morning", "afternoon"):
        import json
        from app.schedule_utils import parse_time_parts
        from app.repository import connect
        
        local_tz = ZoneInfo(settings.default_timezone)
        now_dt = datetime.now(local_tz)
        today_str = now_dt.strftime("%Y-%m-%d")
        
        # 1. 取得该时段所配置的小时数
        config_str = job.get("schedule_config") or ""
        target_hour = None
        if config_str:
            try:
                cfg = json.loads(config_str)
                time_str = cfg.get("morning_time", "09:05") if schedule_type == "morning" else cfg.get("afternoon_time", "17:05")
                target_hour, _ = parse_time_parts(time_str, force_fixed=True)
            except Exception as e:
                logger.error(f"解析简单定时配置小时点失败: {e}")
                
        # 2. 如果成功取得配置的小时数，查数据库今天是否有该小时内且成功的运行记录
        if target_hour is not None:
            with connect() as conn:
                rows = conn.execute(
                    """
                    SELECT started_at FROM run_records 
                    WHERE job_id = ? AND status IN ('success', 'partial_success')
                    """,
                    (job_id,)
                ).fetchall()
                
            has_run = False
            for row in rows:
                started_at_str = row[0]
                if not started_at_str:
                    continue
                try:
                    if "T" in started_at_str:
                        dt = datetime.fromisoformat(started_at_str)
                    else:
                        dt = datetime.strptime(started_at_str, "%Y-%m-%d %H:%M:%S")
                    
                    local_dt = dt.replace(tzinfo=local_tz) if dt.tzinfo is None else dt.astimezone(local_tz)
                    # 匹配日期是今天，且小时数与设定的 target_hour 相同
                    if local_dt.strftime("%Y-%m-%d") == today_str and local_dt.hour == target_hour:
                        has_run = True
                        break
                except Exception as ex:
                    logger.error(f"解析运行记录时间 {started_at_str} 失败: {ex}")
                    
            if has_run:
                period_name = "上午" if schedule_type == "morning" else "下午"
                logger.info(f"检测到任务 '{job['name']}' (ID: {job_id}) 今天{period_name}巡检 (小时点: {target_hour}) 已成功执行过，跳过本次自动调度运行。")
                return 0

    cron_expr = job.get("cron_expression") or ""
    if is_scheduled and "L" in cron_expr:
        from app.periodic_reporter import is_last_day_of_month
        from app.utils import now_local
        if not is_last_day_of_month(now_local()):
            logger.info(f"任务 '{job['name']}' (ID: {job_id}) 配置为月末最后一天执行，今天不是当月最后一天，跳过自动调度运行。")
            return 0

    logger.info(f"======> 启动巡检任务: '{job['name']}' (ID: {job_id}) <======")
    if run_id is None:
        run_id = create_run(job_id, str(job["name"]))
        logger.info(f"创建运行记录. 运行 ID: {run_id}")
    else:
        logger.info(f"复用已创建的运行记录. 运行 ID: {run_id}")
    try:
        items = [item for item in list_screenshot_items(job_id) if int(item.get("enabled") or 0)]
        logger.info(f"待处理启用状态的截图项共 {len(items)} 个")
        
        if not items:
            err_msg = "无可执行巡检项 (未配置任何截图项，或所有截图项已被禁用)"
            update_run(
                run_id,
                status="failed",
                error_summary=err_msg,
                success_count=0,
                failed_count=0,
            )
            # 同样生成一份带有提示的空报告
            run = get_run(run_id)
            results = []
            report_path = generate_docx(job, run or {}, results)
            update_run(
                run_id,
                report_path=report_path,
                mail_status="skipped",
                finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
            )
            logger.info("无可执行巡检项，任务标记为失败并已返回。")
            return run_id

        success_count = 0
        failed_count = 0

        any_real_capture = any(bool(item.get("real_browser_capture") if "real_browser_capture" in item else 1) for item in items)
        headless_val = False if any_real_capture else True
        scale_factor = float(job.get("browser_scale_factor") if job.get("browser_scale_factor") is not None else 1.5)
        launch_kwargs = {
            "headless": headless_val,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                f"--force-device-scale-factor={scale_factor}",
                "--high-dpi-support=1",
                "--lang=zh-CN"
            ]
        }
        
        if any_real_capture:
            first_w = int(items[0].get("browser_width") or job.get("browser_width") or 3840)
            first_h = int(items[0].get("browser_height") or job.get("browser_height") or 2160)
            launch_kwargs["args"].extend([
                f"--window-size={first_w},{first_h}",
                "--start-maximized"
            ])

        logger.info(f"启动批处理 Chromium 浏览器 (headless={launch_kwargs['headless']})...")
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            contexts = {}
            try:
                for item in items:
                    result = capture_item(item, job, run_id, browser=browser, contexts=contexts)
                    if result.status == "success":
                        success_count += 1
                    else:
                        failed_count += 1
                    add_screenshot_result(
                        {
                            "run_id": run_id,
                            "item_id": item["id"],
                            "item_name": item["name"],
                            "section": item["section"],
                            "status": result.status,
                            "file_path": result.file_path,
                            "error_message": result.error_message,
                        }
                    )
            finally:
                logger.info("正在关闭所有浏览器 context 及进程...")
                for ctx in list(contexts.values()):
                    try:
                        ctx.close()
                    except Exception:
                        pass
                browser.close()

        status = "success" if failed_count == 0 else "partial_success"
        logger.info(f"截图环节结束. 状态: {status}, 成功数: {success_count}, 失败数: {failed_count}")
        update_run(
            run_id,
            status=status,
            success_count=success_count,
            failed_count=failed_count,
        )

        run = get_run(run_id)
        if run is None:
            raise RuntimeError("执行记录丢失")
        results = list_screenshot_results(run_id)
        
        logger.info("开始生成 Word 报告...")
        report_path = generate_docx(job, run, results)
        logger.info(f"报告生成成功! 存储路径: {report_path}")
        update_run(run_id, report_path=report_path)

        # 判定邮件发信条件
        send_complete = job.get("send_mail_on_complete")
        if send_complete is None:
            send_complete = job.get("send_mail", 0)
            
        send_error = job.get("send_mail_on_error")
        if send_error is None:
            send_error = 1

        mail_status = "skipped"
        should_send = False
        if status == "success":
            if int(send_complete) == 1:
                if is_scheduled:
                    from app.utils import has_more_scheduled_runs_today
                    started_at = run.get("started_at") or ""
                    if has_more_scheduled_runs_today(job.get("cron_expression") or "", started_at, settings.default_timezone):
                        logger.info("检测到今天后续还有定时巡检任务，且本次运行成功，跳过发送邮件。")
                        mail_status = "skipped"
                    else:
                        should_send = True
                else:
                    should_send = True
        else:
            should_send = int(send_error) == 1

        if should_send:
            logger.info(f"触发邮件发信：状态是 {status}，开始准备发信...")
            try:
                mail_profile = get_mail_profile(job.get("mail_profile_id"))
                error_summary = run.get("error_summary") or ""
                if not error_summary and failed_count > 0:
                    error_summary = f"本次巡检有 {failed_count} 个截图项抓取失败，请查看附件报告获取详细错误。"
                mail_status = send_report_mail(mail_profile or {}, job, report_path, error_msg=error_summary)
                logger.info(f"邮件发送完成，状态: {mail_status}")
            except Exception as exc:  # noqa: BLE001 - saved to run record.
                mail_status = f"failed: {type(exc).__name__}: {exc}"
                logger.error(f"发送邮件异常: {mail_status}", exc_info=True)
                
        update_run(
            run_id,
            mail_status=mail_status,
            finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
        )
        _send_dingtalk_notification(
            job,
            run_id,
            status=status,
            success_count=success_count,
            failed_count=failed_count,
            error_summary=run.get("error_summary") or ("" if failed_count == 0 else f"本次巡检有 {failed_count} 个截图项抓取失败。"),
            mail_status=mail_status,
        )
        logger.info(f"======> 巡检任务 '{job['name']}' (ID: {job_id}, 运行 ID: {run_id}) 执行完毕 <======")
    except Exception as exc:  # noqa: BLE001 - run should keep failure details.
        err_summary = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"
        logger.error(f"======> 巡检任务 '{job['name']}' 运行中发生未捕获异常: {err_summary}", exc_info=True)
        
        send_error = job.get("send_mail_on_error")
        if send_error is None:
            send_error = 1
            
        mail_status = "skipped"
        if int(send_error) == 1:
            try:
                logger.info("满足巡检异常发信策略，正在发送错误通知邮件...")
                mail_profile = get_mail_profile(job.get("mail_profile_id"))
                mail_status = send_report_mail(mail_profile or {}, job, report_path=None, error_msg=err_summary)
            except Exception as mail_exc:
                mail_status = f"failed: {type(mail_exc).__name__}: {mail_exc}"
                logger.error(f"发送错误通知邮件失败: {mail_status}", exc_info=True)

        update_run(
            run_id,
            status="failed",
            error_summary=err_summary,
            mail_status=mail_status,
            finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
        )
        _send_dingtalk_notification(
            job,
            run_id,
            status="failed",
            success_count=0,
            failed_count=0,
            error_summary=err_summary,
            mail_status=mail_status,
        )
        
    if is_scheduled:
        try:
            from app.scheduler import reload_jobs
            reload_jobs()
            logger.info(f"定时任务 {job_id} 执行完成，已刷新洗牌随机时间配置。")
        except Exception as e:
            logger.error(f"定时任务 {job_id} 自动重载刷新随机时间失败: {e}", exc_info=True)
            
    return run_id


def test_capture_item(item_id: int) -> int:
    from app.repository import get_screenshot_item

    item = get_screenshot_item(item_id)
    if not item:
        raise RuntimeError(f"截图项不存在：{item_id}")
    job = get_job(int(item["job_id"]))
    if not job:
        raise RuntimeError(f"任务不存在：{item['job_id']}")

    run_id = create_run(job["id"], f"测试截图 - {item['name']}")
    result = capture_item(item, job, run_id, suffix="_test")
    add_screenshot_result(
        {
            "run_id": run_id,
            "item_id": item["id"],
            "item_name": item["name"],
            "section": item["section"],
            "status": result.status,
            "file_path": result.file_path,
            "error_message": result.error_message,
        }
    )
    update_run(
        run_id,
        status=result.status,
        success_count=1 if result.status == "success" else 0,
        failed_count=0 if result.status == "success" else 1,
        finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
    )
    return run_id


def _send_dingtalk_notification(
    job: dict[str, Any],
    run_id: int,
    status: str,
    success_count: int,
    failed_count: int,
    error_summary: str,
    mail_status: str,
) -> None:
    if job.get("dingtalk_enabled") != 1:
        return

    from app.utils import decrypt_secret
    from app.repository import get_system_setting

    db_webhook = get_system_setting("dingtalk_webhook", "")
    if db_webhook:
        try:
            db_webhook = decrypt_secret(db_webhook)
        except Exception:
            pass
    webhook = db_webhook or settings.dingtalk_webhook

    if not webhook:
        logger.warning(f"任务 {job.get('id')} 启用了钉钉推送，但未配置 Webhook，跳过推送。")
        return

    db_secret = get_system_setting("dingtalk_secret", "")
    if db_secret:
        try:
            db_secret = decrypt_secret(db_secret)
        except Exception:
            pass
    secret  = db_secret or settings.dingtalk_secret
    keyword = get_system_setting("dingtalk_keyword", "") or settings.dingtalk_keyword

    # 是否生成了 Word 报告
    has_word_report = False
    if status == "success":
        from app.repository import get_run
        import os
        run_record = get_run(run_id)
        has_word_report = bool(
            run_record and run_record.get("report_path")
            and os.path.exists(str(run_record.get("report_path") or ""))
        )

    # 邮件状态 HTML
    from app.dingtalk_builder import build_inspection_report_message, build_mail_status_html
    mail_status_html = ""
    if job.get("mail_profile_id") and mail_status and "skipped" not in mail_status.lower() \
            and "failed: 系统没有任何邮件配置" not in mail_status:
        mail_status_html = build_mail_status_html(mail_status)

    # Emoji 开关（从全局配置读取）
    emoji_enabled = get_system_setting("dingtalk_emoji_enabled", "0") == "1"

    # 构建消息
    title, text = build_inspection_report_message(
        job_name=str(job.get("name", "")),
        environment=str(job.get("environment", "")),
        status=status,
        success_count=success_count,
        failed_count=failed_count,
        has_word_report=has_word_report,
        error_summary=error_summary,
        mail_status_html=mail_status_html,
        enable_emoji=emoji_enabled,
    )

    from app.dingtalk import send_dingtalk_msg
    logger.info("正在发送钉钉 Webhook 推送...")
    dt_status = send_dingtalk_msg(webhook, secret, keyword, title, text)
    update_run(run_id, dingtalk_status=dt_status)
