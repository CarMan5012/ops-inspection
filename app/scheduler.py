from __future__ import annotations

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.repository import list_jobs, list_periodic_report_settings
from app.runner import run_job
from app.settings import settings
from app.periodic_reporter import trigger_periodic_report


scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.default_timezone))


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()
    reload_jobs()


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def reload_jobs() -> None:
    import logging
    logger = logging.getLogger("app.scheduler")
    from app.db import connect
    
    # 1. 移除原有的截图定时任务和周期报告定时任务
    for job in scheduler.get_jobs():
        if job.id.startswith("report-job-") or job.id.startswith("periodic-report-"):
            scheduler.remove_job(job.id)

    # 2. 重新加载巡检截图任务
    for item in list_jobs():
        if not int(item.get("enabled") or 0):
            continue
        cron_expression = str(item.get("cron_expression") or "").strip()
        if not cron_expression:
            continue
            
        cron_parts = [p.strip() for p in cron_expression.split(";") if p.strip()]
        load_error = ""
        
        for idx, cron_str in enumerate(cron_parts):
            cron_str_test = cron_str.replace("L", "*")
            parts = cron_str_test.split()
            if len(parts) == 6:
                cron_str_test = " ".join(parts[1:])
            elif len(parts) != 5:
                load_error = f"定时表达式 '{cron_str}' 格式错误：必须为 5 段"
                logger.error(f"加载任务 {item['id']} 失败: {load_error}")
                break
                
            try:
                trigger = CronTrigger.from_crontab(cron_str_test, timezone=ZoneInfo(settings.default_timezone))
                scheduler.add_job(
                    run_job,
                    trigger=trigger,
                    args=[int(item["id"])],
                    id=f"report-job-{item['id']}-{idx}",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
            except Exception as e:
                load_error = f"定时表达式 '{cron_str}' 无效: {e}"
                logger.error(f"加载任务 {item['id']} ('{item['name']}') 失败: {load_error}")
                break
                
        # 更新数据库中的 load_error 状态
        with connect() as conn:
            conn.execute("UPDATE report_jobs SET load_error = ? WHERE id = ?", (load_error, item["id"]))

    # 3. 重新加载周期报告自动定时任务
    for setting in list_periodic_report_settings():
        if not int(setting.get("enabled") or 0):
            continue
        cron_str = str(setting.get("cron_expression") or "").strip()
        if not cron_str:
            continue
            
        cron_str_for_scheduler = cron_str.replace(" L ", " * ").replace("L", "*")
        parts = cron_str_for_scheduler.split()
        if len(parts) == 6:
            cron_str_for_scheduler = " ".join(parts[1:])
            
        load_error = ""
        try:
            trigger = CronTrigger.from_crontab(cron_str_for_scheduler, timezone=ZoneInfo(settings.default_timezone))
            scheduler.add_job(
                trigger_periodic_report,
                trigger=trigger,
                args=[setting["report_type"]],
                id=f"periodic-report-{setting['report_type']}",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        except Exception as e:
            load_error = f"周期定时表达式 '{cron_str}' 无效: {e}"
            logger.error(f"加载周期报告定时任务 '{setting['report_type']}' 失败: {load_error}")
            
        with connect() as conn:
            conn.execute("UPDATE periodic_report_settings SET load_error = ? WHERE report_type = ?", (load_error, setting["report_type"]))

