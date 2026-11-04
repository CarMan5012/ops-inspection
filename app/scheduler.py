from __future__ import annotations

from zoneinfo import ZoneInfo
from datetime import datetime, time, timedelta
from threading import Event, Thread

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from app.repository import get_job, get_periodic_report_setting, list_jobs, list_periodic_report_settings
from app.runner import run_job
from app.job_execution import recover_interrupted_runs
from app.settings import settings
from app.periodic_reporter import monthly_report_retry_check, trigger_periodic_report


scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.default_timezone))
_last_synced_signature: str | None = None
_has_initial_synced: bool = False
_scheduler_lock = None
_standby_stop = Event()
_standby_thread: Thread | None = None


def _acquire_scheduler_lock() -> bool:
    global _scheduler_lock
    if _scheduler_lock is not None:
        return True
    lock_path = settings.data_dir / "scheduler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if __import__("os").name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return False
    _scheduler_lock = handle
    return True


def _upsert_job(job_id: str, func, trigger: DateTrigger, *, args=(), kwargs=None) -> bool:
    current = scheduler.get_job(job_id)
    if current and str(current.trigger) == str(trigger) and tuple(current.args) == tuple(args) and current.kwargs == (kwargs or {}):
        return False
    scheduler.add_job(func, trigger=trigger, args=args, kwargs=kwargs or {}, id=job_id,
                      replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
    return True


def _start_owned_scheduler() -> None:
    from app.schedule_migration import migrate_legacy_schedules
    migrate_legacy_schedules()
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(reload_jobs, "interval", seconds=15, id="scheduler-config-sync",
                      replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=60)
    scheduler.add_job(recover_interrupted_runs, "interval", minutes=1, id="inspection-run-recovery",
                      replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=60)
    reload_jobs()
    _restore_waiting_monthly_reports()
    Thread(target=_refresh_holiday_calendar, daemon=True).start()


def _restore_waiting_monthly_reports() -> None:
    """Restore one-shot retries that the in-memory scheduler lost on restart."""
    import logging
    from app.db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM periodic_report_runs WHERE report_type = 'monthly' AND status = 'waiting'"
        ).fetchall()
    run_date = datetime.now(ZoneInfo(settings.default_timezone)) + timedelta(seconds=1)
    for row in rows:
        run_id = int(row["id"])
        scheduler.add_job(
            monthly_report_retry_check, DateTrigger(run_date=run_date),
            args=[run_id], id=f"monthly-retry-{run_id}", replace_existing=True,
            max_instances=1, coalesce=True, misfire_grace_time=3600,
        )
    if rows:
        logging.getLogger("app.scheduler").info("已恢复 %s 条等待中的月报重试", len(rows))


def _refresh_holiday_calendar() -> None:
    from app.holiday_calendar import refresh_current_year
    try:
        refresh_current_year()
    finally:
        global _last_synced_signature
        now = datetime.now(ZoneInfo(settings.default_timezone))
        next_check = datetime.combine(now.date(), time(0, 5), tzinfo=now.tzinfo)
        if next_check <= now:
            next_check += timedelta(days=1)
        if scheduler.running:
            scheduler.add_job(_refresh_holiday_calendar, DateTrigger(run_date=next_check),
                              id="holiday-calendar-refresh", replace_existing=True,
                              max_instances=1, coalesce=True, misfire_grace_time=3600)
        _last_synced_signature = None
        reload_jobs()


def _run_calendar_job(job_id: int, slot: str) -> None:
    from app.date_schedule import from_job, local_now, matches, upcoming
    import logging
    job = get_job(job_id)
    if not job or not job.get("enabled"):
        return
    try:
        config = from_job(job)
        if not config or slot not in config["times"]:
            return
        timezone = ZoneInfo(settings.default_timezone)
        next_config = {**config, "times": [slot]}
        next_run = upcoming(next_config, local_now(settings.default_timezone) + timedelta(seconds=1), 1, job_id=job_id)
        if next_run:
            scheduler.add_job(_run_calendar_job, DateTrigger(run_date=next_run[0], timezone=timezone),
                              args=[job_id, slot], id=f"report-job-{job_id}-calendar-{slot.replace(':', '')}",
                              replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
        if matches(local_now(settings.default_timezone).date(), config) is not True:
            logging.getLogger(__name__).warning("任务 %s 的日期类型无法确认，跳过本次执行", job_id)
            return
        run_job(job_id, schedule_type=f"calendar-{slot.replace(':', '')}")
    except Exception:
        logging.getLogger(__name__).exception("日历计划执行失败：任务 %s", job_id)


def _periodic_next(setting: dict, slot: dict, after) -> object | None:
    import calendar
    import json
    from datetime import datetime, time, timedelta
    config = json.loads(setting.get("schedule_config") or "{}")
    hour = int(slot["hour"]) % 12 + (12 if slot["ampm"] == "pm" else 0)
    minute = int(slot["minute"])
    for offset in range(370):
        day = after.date() + timedelta(days=offset)
        if setting["report_type"] == "weekly" and day.isoweekday() != int(config.get("day_of_week") or 1):
            continue
        if setting["report_type"] == "monthly" and day.day != calendar.monthrange(day.year, day.month)[1]:
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=after.tzinfo)
        if candidate > after:
            return candidate
    return None


def _run_calendar_periodic(report_type: str, index: int) -> None:
    import json
    import logging
    setting = get_periodic_report_setting(report_type)
    if not setting or not setting.get("enabled") or setting.get("schedule_mode") != "calendar":
        return
    try:
        config = json.loads(setting.get("schedule_config") or "{}")
        slot = config["times"][index]
        next_run = _periodic_next(setting, slot, datetime.now(ZoneInfo(settings.default_timezone)) + timedelta(seconds=1))
        if next_run:
            scheduler.add_job(_run_calendar_periodic, DateTrigger(run_date=next_run, timezone=ZoneInfo(settings.default_timezone)),
                              args=[report_type, index], id=f"periodic-report-{report_type}-{index}",
                              replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
        trigger_periodic_report(report_type)
    except Exception:
        logging.getLogger(__name__).exception("周期报告日期计划执行失败：%s", report_type)


def _cleanup_next(setting: dict, after: datetime) -> datetime:
    import json
    config = json.loads(setting.get("cleanup_schedule_config") or "{}")
    mode = setting.get("cleanup_schedule_mode") or "daily"
    hour, minute = map(int, str(config.get("time") or "02:30").split(":"))
    if mode not in ("daily", "weekly", "monthly") or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("自动清理时间配置无效")
    for offset in range(370):
        day = after.date() + timedelta(days=offset)
        weekday = int(config.get("day_of_week") or 1)
        if weekday == 0:
            weekday = 7
        if mode == "weekly" and day.isoweekday() != weekday:
            continue
        if mode == "monthly" and day.day != int(config.get("day_of_month") or 1):
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=after.tzinfo)
        if candidate > after:
            return candidate
    raise ValueError("无法计算下次自动清理时间")


def _run_storage_cleanup_job() -> None:
    from app.db import connect
    with connect() as conn:
        row = conn.execute("SELECT * FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
    if not row or not int(row["enabled"] or 0):
        return
    try:
        next_run = _cleanup_next(dict(row), datetime.now(ZoneInfo(settings.default_timezone)) + timedelta(seconds=1))
        _upsert_job("storage-cleanup-task", _run_storage_cleanup_job, DateTrigger(run_date=next_run))
        run_storage_cleanup_job()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("自动存储清理计划执行失败")


def _standby_for_scheduler() -> None:
    while not _standby_stop.wait(15):
        if _acquire_scheduler_lock():
            _start_owned_scheduler()
            return


def start_scheduler() -> None:
    global _standby_thread
    if not _acquire_scheduler_lock():
        import logging
        logging.getLogger("app.scheduler").info("另一个进程正在运行调度器，本进程等待接管")
        _standby_stop.clear()
        _standby_thread = Thread(target=_standby_for_scheduler, daemon=True)
        _standby_thread.start()
        return
    _start_owned_scheduler()


def shutdown_scheduler() -> None:
    global _scheduler_lock
    _standby_stop.set()
    if scheduler.running:
        scheduler.shutdown(wait=False)
    if _scheduler_lock is not None:
        _scheduler_lock.close()
        _scheduler_lock = None


def reload_jobs(*, force_random: bool = False) -> None:
    global _last_synced_signature, _has_initial_synced
    import logging
    logger = logging.getLogger("app.scheduler")
    from app.db import connect
    
    # 1. 采集当前配置数据以计算签名，若配置无变化且非强制洗牌，直接静默返回避免无意义轮询刷屏
    all_jobs = list_jobs()
    all_periodic = list_periodic_report_settings()
    cleanup_setting = None
    try:
        with connect() as conn:
            row = conn.execute("SELECT enabled, cleanup_schedule_mode, cleanup_schedule_config FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                cleanup_setting = dict(row)
    except Exception as e:
        logger.error(f"读取存储自动清理配置失败: {e}")

    # 日期配置是唯一调度依据；旧表达式仅保留在数据库中供迁移查看。
    jobs_sig = tuple(
        (
            j.get("id"),
            j.get("enabled"),
            j.get("schedule_mode"),
            j.get("schedule_config"),
        )
        for j in all_jobs
    )
    periodic_sig = tuple((p.get("report_type"), p.get("enabled"), p.get("schedule_mode"), p.get("schedule_config")) for p in all_periodic)
    cleanup_sig = (
        cleanup_setting.get("enabled") if cleanup_setting else 0,
        cleanup_setting.get("cleanup_schedule_mode") if cleanup_setting else "",
        cleanup_setting.get("cleanup_schedule_config") if cleanup_setting else "",
    )
    current_signature = str((jobs_sig, periodic_sig, cleanup_sig))

    if not force_random and _last_synced_signature == current_signature:
        return

    desired_ids: set[str] = set()
    updated_count = 0

    # 2. 重新加载巡检截图任务
    for item in all_jobs:
        if not int(item.get("enabled") or 0):
            continue
            
        schedule_mode = item.get("schedule_mode") or "cron"
        load_error = ""
        
        if schedule_mode == "calendar":
            try:
                from app.date_schedule import from_job, local_now, upcoming
                config = from_job(item)
                if config is None:
                    raise ValueError("日期计划缺失")
                for slot in config["times"]:
                    next_run = upcoming({**config, "times": [slot]}, local_now(settings.default_timezone), 1, job_id=int(item["id"]))
                    if not next_run:
                        load_error = "节假日数据暂不可用" if config["frequency"] in ("workday", "non_workday") else "无法计算下次执行时间"
                        continue
                    job_id_str = f"report-job-{item['id']}-calendar-{slot.replace(':', '')}"
                    desired_ids.add(job_id_str)
                    if _upsert_job(job_id_str, _run_calendar_job, DateTrigger(run_date=next_run[0], timezone=ZoneInfo(settings.default_timezone)), args=[int(item["id"]), slot]):
                        updated_count += 1
            except Exception as e:
                load_error = f"日期计划解析失败: {e}"
                logger.error("加载任务 %s 失败: %s", item["id"], load_error)
        else:
            load_error = "旧定时规则已暂停，请编辑任务并保存为日期计划"
        # 更新数据库中的 load_error 状态
        if load_error != (item.get("load_error") or ""):
            with connect() as conn:
                conn.execute("UPDATE report_jobs SET load_error = ? WHERE id = ?", (load_error, item["id"]))

    # 3. 重新加载周期报告自动定时任务
    for setting in all_periodic:
        if not int(setting.get("enabled") or 0):
            continue
        if setting.get("schedule_mode") == "calendar":
            try:
                import json
                config = json.loads(setting.get("schedule_config") or "{}")
                for index, slot in enumerate(config["times"]):
                    next_run = _periodic_next(setting, slot, datetime.now(ZoneInfo(settings.default_timezone)))
                    if next_run is None:
                        raise ValueError("无法计算下次生成时间")
                    job_id_str = f"periodic-report-{setting['report_type']}-{index}"
                    desired_ids.add(job_id_str)
                    if _upsert_job(job_id_str, _run_calendar_periodic, DateTrigger(run_date=next_run, timezone=ZoneInfo(settings.default_timezone)), args=[setting["report_type"], index]):
                        updated_count += 1
                load_error = ""
            except Exception as e:
                load_error = f"日期计划无效: {e}"
                logger.error("加载周期报告 %s 失败: %s", setting["report_type"], load_error)
            if load_error != (setting.get("load_error") or ""):
                with connect() as conn:
                    conn.execute("UPDATE periodic_report_settings SET load_error = ? WHERE report_type = ?", (load_error, setting["report_type"]))
            continue
        load_error = "旧定时规则已暂停，请编辑周期报告并保存为日期计划"
        if load_error != (setting.get("load_error") or ""):
            with connect() as conn:
                conn.execute("UPDATE periodic_report_settings SET load_error = ? WHERE report_type = ?", (load_error, setting["report_type"]))

    # 4. 存储清理使用单次日期触发器
    cleanup_desc = "未启用"
    if cleanup_setting and int(cleanup_setting.get("enabled") or 0):
        try:
            next_run = _cleanup_next(cleanup_setting, datetime.now(ZoneInfo(settings.default_timezone)))
            desired_ids.add("storage-cleanup-task")
            if _upsert_job("storage-cleanup-task", _run_storage_cleanup_job, DateTrigger(run_date=next_run)):
                updated_count += 1
            cleanup_desc = next_run.strftime("下次 %Y-%m-%d %H:%M")
        except Exception as e:
            logger.error("注册存储清理日期计划失败: %s", e)

    for job in scheduler.get_jobs():
        if (job.id.startswith("report-job-") or job.id.startswith("periodic-report-") or job.id == "storage-cleanup-task") and job.id not in desired_ids:
            scheduler.remove_job(job.id)
            updated_count += 1

    _last_synced_signature = current_signature
    should_log = (not _has_initial_synced) or (updated_count > 0) or force_random
    _has_initial_synced = True

    if should_log:
        active_report_count = sum(1 for j in all_jobs if int(j.get("enabled") or 0))
        active_periodic_count = sum(1 for p in all_periodic if int(p.get("enabled") or 0))
        logger.info(
            f"计划调度器配置已同步: 巡检任务 {active_report_count} 项, 周期报告 {active_periodic_count} 项, 存储自动清理: {cleanup_desc}"
        )


def run_storage_cleanup_job() -> None:
    import logging
    logger = logging.getLogger("app.scheduler")
    logger.info("开始执行自动存储清理定时任务")
    try:
        from app.cleanup import run_cleanup
        res = run_cleanup(mode="auto", dry_run=False)
        if res.get("status") == "failed":
            logger.error("自动存储清理失败: %s", res.get("error_summary") or res)
        elif res.get("status") == "partial_success":
            logger.warning("自动存储清理部分失败: %s", res.get("error_summary") or res)
        else:
            logger.info("自动存储清理完成: %s", res)
    except Exception as e:
        logger.error(f"自动存储清理发生错误: {e}")
