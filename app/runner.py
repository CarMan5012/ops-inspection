import logging
import json
import time
import traceback
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo
from typing import Any

from app.settings import settings
from app.job_execution import JobAlreadyRunning, JobLease, acquire_job_lease

from app.mailer import send_report_mail
from app.monitoring import summarize_monitoring
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
from app.screenshot import BrowserSession, browser_window_size, capture_item, chrome_launch_args
from playwright.sync_api import sync_playwright

logger = logging.getLogger("app.runner")
_real_browser_lock = Lock()


def run_job(
    job_id: int, run_id: int | None = None, schedule_type: str | None = None,
    *, execution_lease: JobLease | None = None,
) -> int:
    try:
        # Scheduled execution waits for the manual run rather than losing its scheduled slot.
        lease = execution_lease or acquire_job_lease(job_id, blocking=run_id is None)
    except JobAlreadyRunning:
        if run_id is None:
            logger.info("任务 #%s 已在运行，跳过本次重复调度", job_id)
            return 0
        update_run(run_id, status="failed", error_summary="任务已在运行，重复执行已取消",
                   finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"))
        return run_id
    try:
        return _execute_job(job_id, run_id, schedule_type)
    finally:
        lease.close()


def _execute_job(job_id: int, run_id: int | None = None, schedule_type: str | None = None) -> int:
    is_scheduled = (run_id is None)
    job = get_job(job_id)
    if not job:
        logger.error(f"任务不存在: {job_id}")
        raise RuntimeError(f"任务不存在：{job_id}")

    # 如果是简单巡检定时模式，且是自动定时触发的，增加“同天同时间段只成功执行一次”的判定，
    # 防止由于执行完洗牌重新调度、手动保存任务配置或升级重启容器重新洗牌导致的重复执行。
    if is_scheduled and job.get("schedule_mode") == "simple" and schedule_type in ("morning", "afternoon"):
        from app.repository import connect
        
        local_tz = ZoneInfo(settings.default_timezone)
        now_dt = datetime.now(local_tz)
        today_str = now_dt.strftime("%Y-%m-%d")
        
        with connect() as conn:
            has_run = conn.execute(
                """
                SELECT 1 FROM run_records
                WHERE job_id = ? AND schedule_slot = ?
                  AND substr(started_at, 1, 10) = ?
                  AND status IN ('success', 'partial_success')
                LIMIT 1
                """,
                (job_id, schedule_type, today_str),
            ).fetchone()
        if has_run:
            logger.info("任务 %s 今天的 %s 时段已成功执行，跳过重复触发。", job_id, schedule_type)
            return 0

    job_start_time = time.monotonic()
    job_name = str(job.get("name") or f"任务-{job_id}")
    schedule_desc = schedule_type or ("自动定时" if is_scheduled else "手动执行")

    if run_id is None:
        run_id = create_run(job_id, job_name, schedule_type or "")

    try:
        items = [item for item in list_screenshot_items(job_id) if int(item.get("enabled") or 0)]
        logger.info("=" * 68)
        logger.info(f"巡检开始：{job_name}（任务 #{job_id}，记录 #{run_id}，{schedule_desc}）")
        logger.info(f"待截图：{len(items)} 项")
        
        if not items:
            err_msg = "无可执行巡检项 (未配置任何截图项，或所有截图项已被禁用)"
            update_run(
                run_id,
                status="failed",
                error_summary=err_msg,
                success_count=0,
                failed_count=0,
            )
            run = get_run(run_id)
            results = []
            report_path = generate_docx(job, run or {}, results)
            mail_status = "skipped"
            if int(job.get("send_mail_on_error") if job.get("send_mail_on_error") is not None else 1):
                try:
                    mail_profile = get_mail_profile(job.get("mail_profile_id"))
                    mail_status = send_report_mail(mail_profile or {}, job, report_path, error_msg=err_msg)
                except Exception as exc:
                    mail_status = f"failed: {type(exc).__name__}: {exc}"
                    logger.error("无可执行项时发送报告失败：%s", mail_status, exc_info=True)
            update_run(
                run_id,
                report_path=report_path,
                mail_status=mail_status,
                finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
            )
            try:
                _send_dingtalk_notification(job, run_id, status="failed", success_count=0,
                                           failed_count=0, error_summary=err_msg, mail_status=mail_status)
            except Exception as exc:
                logger.exception("无可执行项时发送钉钉通知失败")
                update_run(run_id, dingtalk_status=f"failed: {type(exc).__name__}: {exc}")
            logger.error("└─ ✖ 任务终止: 无可执行巡检项，已生成空报告并退出")
            logger.info("=" * 68)
            return run_id

        success_count = 0
        failed_count = 0

        any_real_capture = any(bool(item.get("real_browser_capture") if "real_browser_capture" in item else 0) for item in items)
        headless_val = False if any_real_capture else True
        scale_factor = float(job.get("browser_scale_factor") if job.get("browser_scale_factor") is not None else 1.5)
        first_w = first_h = 0
        if any_real_capture:
            first_real_item = next(item for item in items if bool(item.get("real_browser_capture") if "real_browser_capture" in item else 0))
            first_w, first_h = browser_window_size(first_real_item, job, run_id)
        launch_kwargs = {
            "headless": headless_val,
            "channel": "chrome",
            "ignore_default_args": ["--enable-automation", "--disable-sync"],
            "args": chrome_launch_args(scale_factor, any_real_capture, first_w, first_h),
        }

        logger.debug(f"浏览器参数：Chromium，headless={headless_val}，scale={scale_factor}")
        logger.info("=" * 68)

        if any_real_capture:
            logger.info("真实窗口截图：等待独占浏览器窗口")
        with (_real_browser_lock if any_real_capture else nullcontext()):
            with sync_playwright() as p:
                browser_session = BrowserSession.open(lambda: p.chromium.launch(**launch_kwargs))
                total_items = len(items)
                try:
                    for idx, item in enumerate(items, 1):
                        result = capture_item(
                            item,
                            job,
                            run_id,
                            browser_session=browser_session,
                            item_index=idx,
                            total_items=total_items,
                        )
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
                                "duration_seconds": result.duration_seconds,
                                "content_status": result.content_status,
                                "content_summary": result.content_summary,
                                "monitor_status": (
                                    result.monitor_data.get("status", "unknown") if result.monitor_data
                                    else "unknown" if "/d/" in str(item.get("url") or "").lower()
                                    or "/d-solo/" in str(item.get("url") or "").lower()
                                    else "not_applicable"
                                ),
                                "monitor_data": json.dumps(result.monitor_data or {}, ensure_ascii=False),
                            }
                        )
                finally:
                    browser_session.close()

        status = "success" if failed_count == 0 else "failed" if success_count == 0 else "partial_success"
        update_run(
            run_id,
            status=status,
            success_count=success_count,
            failed_count=failed_count,
            error_summary=(f"全部 {failed_count} 个截图项失败，请查看截图结果。" if success_count == 0 else
                           f"{failed_count} 个截图项失败，请查看截图结果。" if failed_count else ""),
        )

        run = get_run(run_id)
        if run is None:
            raise RuntimeError("执行记录丢失")
        results = list_screenshot_results(run_id)
        monitor = summarize_monitoring(results)
        update_run(run_id, monitor_status=monitor["status"], monitor_summary=monitor["text"])
        run = get_run(run_id) or run
        
        logger.info("正在生成 Word 报告…")
        report_path = generate_docx(job, run, results)
        logger.info(f"报告已生成：{Path(report_path).name}")
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
        if status == "success" and monitor["status"] not in {"alert", "unknown", "unconfigured"}:
            if int(send_complete) == 1:
                if is_scheduled:
                    started_at = run.get("started_at") or ""
                    if job.get("schedule_mode") == "calendar":
                        from app.date_schedule import from_job, upcoming
                        config = from_job(job)
                        start_dt = datetime.fromisoformat(started_at).replace(tzinfo=ZoneInfo(settings.default_timezone))
                        later = upcoming(config, start_dt, 1, job_id=job_id) if config else []
                        has_later_today = bool(later and later[0].date() == start_dt.date())
                    else:
                        has_later_today = False
                    if has_later_today:
                        logger.info("▶ 检测到今天后续还有定时巡检任务，且本次运行成功，跳过发送邮件")
                        mail_status = "skipped"
                    else:
                        should_send = True
                else:
                    should_send = True
        else:
            should_send = int(send_error) == 1

        if should_send:
            logger.info("▶ 正在准备发送巡检结果邮件...")
            try:
                mail_profile = get_mail_profile(job.get("mail_profile_id"))
                notices = [str(run.get("error_summary") or "").strip()]
                if monitor["status"] in {"alert", "unknown", "unconfigured"}:
                    notices.append(monitor["text"])
                mail_status = send_report_mail(
                    mail_profile or {}, job, report_path,
                    error_msg="\n".join(part for part in notices if part),
                )
                logger.info(f"  └─ ✔ 邮件发送完成 (状态: {mail_status})")
            except Exception as exc:  # noqa: BLE001 - saved to run record.
                mail_status = f"failed: {type(exc).__name__}: {exc}"
                logger.error(f"  └─ ✖ 发送邮件异常: {mail_status}", exc_info=True)
                
        update_run(
            run_id,
            mail_status=mail_status,
            finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
        )
        try:
            notify_status = (
                f"monitor_{monitor['status']}"
                if status == "success" and monitor["status"] in {"alert", "unknown", "unconfigured"}
                else status
            )
            _send_dingtalk_notification(
                job,
                run_id,
                status=notify_status,
                success_count=success_count,
                failed_count=failed_count,
                error_summary="\n".join(part for part in (
                    str(run.get("error_summary") or "").strip(),
                    monitor["text"] if monitor["status"] in {"alert", "unknown", "unconfigured"} else "",
                ) if part),
                mail_status=mail_status,
            )
        except Exception as exc:
            logger.exception("钉钉通知失败，巡检结果保持为 %s", status)
            update_run(run_id, dingtalk_status=f"failed: {type(exc).__name__}: {exc}")

        total_duration = round(time.monotonic() - job_start_time, 2)
        logger.info("=" * 68)
        mail_label = "未发送" if mail_status.startswith("skipped") else "发送失败" if mail_status.startswith("failed:") else "已发送"
        logger.info(f"巡检结束：{job_name}（记录 #{run_id}）")
        logger.info(f"结果：成功 {success_count} 项，失败 {failed_count} 项；报告：{Path(report_path).name if report_path else '未生成'}；邮件：{mail_label}；耗时 {total_duration} 秒")
        logger.info("=" * 68)
    except Exception as exc:  # noqa: BLE001 - run should keep failure details.
        total_duration = round(time.monotonic() - job_start_time, 2)
        err_summary = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"
        logger.error("=" * 68)
        logger.error(f"💥 [巡检任务异常终止] '{job_name}' (ID: {job_id}, 运行 ID: {run_id}, 耗时: {total_duration}s)")
        logger.error(f"└─ 异常详情: {err_summary.splitlines()[0] if err_summary else ''}", exc_info=True)
        logger.error("=" * 68)
        
        send_error = job.get("send_mail_on_error")
        if send_error is None:
            send_error = 1
            
        mail_status = "skipped"
        if int(send_error) == 1:
            try:
                logger.info("▶ 满足巡检异常发信策略，正在发送错误通知邮件...")
                mail_profile = get_mail_profile(job.get("mail_profile_id"))
                saved_run = get_run(run_id) or {}
                mail_status = send_report_mail(
                    mail_profile or {}, job, report_path=saved_run.get("report_path") or None,
                    error_msg=err_summary,
                )
                logger.info(f"  └─ ✔ 异常通知邮件发送完成: {mail_status}")
            except Exception as mail_exc:
                mail_status = f"failed: {type(mail_exc).__name__}: {mail_exc}"
                logger.error(f"  └─ ✖ 发送错误通知邮件失败: {mail_status}", exc_info=True)

        update_run(
            run_id,
            status="failed",
            error_summary=err_summary,
            mail_status=mail_status,
            finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
        )
        try:
            _send_dingtalk_notification(
                job,
                run_id,
                status="failed",
                success_count=0,
                failed_count=0,
                error_summary=err_summary,
                mail_status=mail_status,
            )
        except Exception as notify_exc:
            logger.exception("发送巡检失败通知时出错")
            update_run(run_id, dingtalk_status=f"failed: {type(notify_exc).__name__}: {notify_exc}")
        
    if is_scheduled:
        try:
            from app.scheduler import reload_jobs
            reload_jobs()
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

    test_start = time.monotonic()
    logger.info("-" * 68)
    logger.info(f"🔍 [单项测试截图] 截图项: '{item['name']}' (ID: {item_id}, 所属任务: '{job['name']}')")
    logger.info("-" * 68)

    run_id = create_run(job["id"], f"测试截图 - {item['name']}")
    is_real_capture = bool(item.get("real_browser_capture") if "real_browser_capture" in item else 0)
    try:
        with (_real_browser_lock if is_real_capture else nullcontext()):
            result = capture_item(item, job, run_id, suffix="_test", item_index=1, total_items=1)
        add_screenshot_result(
            {
                "run_id": run_id,
                "item_id": item["id"],
                "item_name": item["name"],
                "section": item["section"],
                "status": result.status,
                "file_path": result.file_path,
                "error_message": result.error_message,
                "duration_seconds": result.duration_seconds,
                "content_status": result.content_status,
                "content_summary": result.content_summary,
            }
        )
        update_run(
            run_id,
            status=result.status,
            success_count=1 if result.status == "success" else 0,
            failed_count=0 if result.status == "success" else 1,
            error_summary=result.error_message if result.status != "success" else "",
            finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
        )
    except Exception as exc:
        logger.exception("单项截图测试异常：%s", item["name"])
        update_run(
            run_id,
            status="failed",
            failed_count=1,
            error_summary=f"{type(exc).__name__}: {exc}",
            finished_at=datetime.now(ZoneInfo(settings.default_timezone)).isoformat(timespec="seconds"),
        )
        return run_id
    test_dur = round(time.monotonic() - test_start, 2)
    res_str = "✔ 截图成功" if result.status == "success" else f"✖ 截图失败 ({result.error_message})"
    logger.info("-" * 68)
    logger.info(f"🔍 [单项测试结束] 截图项: '{item['name']}' -> {res_str} (总耗时: {test_dur}s)")
    logger.info("-" * 68)
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

    send_complete = job.get("send_dingtalk_on_complete", 1)
    if send_complete is None:
        send_complete = 1
    send_error = job.get("send_dingtalk_on_error", 1)
    if send_error is None:
        send_error = 1

    if status == "success" and not int(send_complete):
        logger.info("▶ 任务运行成功，但未勾选[完成后发送钉钉通知]，跳过钉钉推送")
        return
    if status != "success" and not int(send_error):
        logger.info("▶ 任务运行异常，但未勾选[异常时发送钉钉通知]，跳过钉钉推送")
        return

    from app.utils import decrypt_secret
    from app.repository import get_system_setting

    from app.repository import get_dingtalk_profile, list_dingtalk_profiles
    bot_profile = None
    if job.get("dingtalk_profile_id"):
        bot_profile = get_dingtalk_profile(job.get("dingtalk_profile_id"))
    if not bot_profile:
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
        webhook = db_webhook or settings.dingtalk_webhook
        db_secret = get_system_setting("dingtalk_secret", "")
        if db_secret:
            try:
                db_secret = decrypt_secret(db_secret)
            except Exception:
                pass
        secret = db_secret or settings.dingtalk_secret
        keyword = get_system_setting("dingtalk_keyword", "") or settings.dingtalk_keyword
        emoji_enabled = get_system_setting("dingtalk_emoji_enabled", "0") == "1"

    if not webhook:
        logger.warning(f"任务 {job.get('id')} 启用了钉钉推送，但未配置 Webhook，跳过推送。")
        return

    # 是否生成了 Word 报告
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
