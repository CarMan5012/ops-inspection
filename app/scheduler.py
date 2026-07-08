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
    
    # 1. 移除原有的截图定时任务、周期报告定时任务和自动清理定时任务
    for job in scheduler.get_jobs():
        if job.id.startswith("report-job-") or job.id.startswith("periodic-report-") or job.id == "storage-cleanup-task":
            scheduler.remove_job(job.id)

    # 2. 重新加载巡检截图任务
    for item in list_jobs():
        if not int(item.get("enabled") or 0):
            continue
            
        schedule_mode = item.get("schedule_mode") or "cron"
        load_error = ""
        
        if schedule_mode == "simple":
            config_str = item.get("schedule_config") or ""
            try:
                import json
                from app.schedule_utils import build_inspection_triggers
                
                if not config_str:
                    raise ValueError("简单时间计划参数为空")
                
                cfg = json.loads(config_str)
                triggers = build_inspection_triggers(cfg, ZoneInfo(settings.default_timezone))
                
                if not triggers:
                    raise ValueError("上午与下午巡检时间点均未启用")
                
                for idx, trigger in enumerate(triggers):
                    suffix = "morning" if (idx == 0 and cfg.get("morning_enabled")) else "afternoon"
                    job_id_str = f"report-job-{item['id']}-{suffix}"
                    
                    scheduler.add_job(
                        run_job,
                        trigger=trigger,
                        args=[int(item["id"])],
                        id=job_id_str,
                        replace_existing=True,
                        max_instances=1,
                        coalesce=True,
                    )

                # 同步回写实际生成的 Cron 表达式到数据库的 cron_expression 字段
                try:
                    cron_parts = []
                    for t in triggers:
                        m = str(t.fields[6])
                        h = str(t.fields[5])
                        dom = str(t.fields[2])
                        mon = str(t.fields[1])
                        dow = str(t.fields[4])
                        if dow == "mon-fri":
                            dow = "1-5"
                        cron_parts.append(f"{m} {h} {dom} {mon} {dow}")
                    actual_cron = "; ".join(cron_parts)
                    with connect() as conn:
                        conn.execute("UPDATE report_jobs SET cron_expression = ? WHERE id = ?", (actual_cron, item["id"]))
                except Exception as db_err:
                    logger.error(f"同步写回任务 {item['id']} 的实际 Cron 表达式到数据库失败: {db_err}")

            except Exception as e:
                load_error = f"简易定时配置解析失败: {e}"
                logger.error(f"加载任务 {item['id']} ('{item['name']}') 失败: {load_error}")
        else:
            cron_expression = str(item.get("cron_expression") or "").strip()
            if not cron_expression:
                continue
                
            cron_parts = [p.strip() for p in cron_expression.split(";") if p.strip()]
            
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

    # 4. 重新加载存储自动清理任务
    cleanup_setting = None
    try:
        with connect() as conn:
            row = conn.execute("SELECT enabled, cleanup_schedule_mode, cleanup_schedule_config FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                cleanup_setting = dict(row)
    except Exception as e:
        logger.error(f"读取存储自动清理配置失败: {e}")

    if cleanup_setting and int(cleanup_setting.get("enabled") or 0):
        mode = cleanup_setting.get("cleanup_schedule_mode", "daily")
        config_str = cleanup_setting.get("cleanup_schedule_config") or ""
        
        hour = 2
        minute = 30
        day_of_week = None
        day = None
        
        if config_str:
            try:
                import json
                cfg = json.loads(config_str)
                time_str = cfg.get("time") or "02:30"
                parts = time_str.split(":")
                if len(parts) == 2:
                    hour = int(parts[0])
                    minute = int(parts[1])
                
                if mode == "weekly":
                    dow_val = str(cfg.get("day_of_week") or "1")
                    mapping = {"1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat", "7": "sun", "0": "sun"}
                    day_of_week = mapping.get(dow_val, "mon")
                elif mode == "monthly":
                    day = int(cfg.get("day_of_month") or 1)
            except Exception as ex:
                logger.error(f"解析存储清理计划配置 JSON 失败: {ex}")

        try:
            trigger_args: dict = {
                "hour": hour,
                "minute": minute,
                "timezone": ZoneInfo(settings.default_timezone)
            }
            if mode == "weekly" and day_of_week:
                trigger_args["day_of_week"] = day_of_week
            elif mode == "monthly" and day is not None:
                trigger_args["day"] = day
                
            trigger = CronTrigger(**trigger_args)
            scheduler.add_job(
                run_storage_cleanup_job,
                trigger=trigger,
                id="storage-cleanup-task",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info(f"成功加载存储自动清理任务：模式={mode}, 时间={hour:02d}:{minute:02d}, 周几={day_of_week}, 几号={day}")
        except Exception as e:
            logger.error(f"注册存储自动清理任务失败: {e}")


def run_storage_cleanup_job() -> None:
    import logging
    logger = logging.getLogger("app.scheduler")
    logger.info("开始执行自动存储清理定时任务")
    try:
        from app.cleanup import run_cleanup
        res = run_cleanup(mode="auto", dry_run=False)
        logger.info(f"自动存储清理完成: {res}")
    except Exception as e:
        logger.error(f"自动存储清理发生错误: {e}")

    try:
        logger.info("自动存储清理完成，正在重新加载巡检任务计划以刷新每日随机分钟点...")
        reload_jobs()
    except Exception as e:
        logger.error(f"每日例行计划重载发生异常: {e}")

