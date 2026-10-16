"""One-time conversion of legacy simple schedules to calendar rules."""

from __future__ import annotations

import json
import logging
import os

from app.date_schedule import label, normalize_schedule

logger = logging.getLogger(__name__)


def _job_config(value: str) -> dict:
    old = json.loads(value or "{}")
    frequency = {"daily": "daily", "workday": "weekdays", "weekly": "weekly", "monthly": "monthly"}.get(old.get("frequency"))
    if frequency is None:
        raise ValueError("旧日期规则需要重新设置")
    times = []
    for slot in ("morning", "afternoon"):
        if old.get(f"{slot}_enabled"):
            value = str(old.get(f"{slot}_time") or ("09:05" if slot == "morning" else "17:05"))
            value = value.upper()
            times.append(value)
    return normalize_schedule({
        "frequency": frequency,
        "days_of_week": [int(old.get("day_of_week") or 1)] if frequency == "weekly" else [],
        "days_of_month": [int(old.get("day_of_month") or 1)] if frequency == "monthly" else [],
        "times": times,
    })


def _periodic_config(value: str, report_type: str) -> dict:
    old = json.loads(value or "{}")
    if report_type == "weekly" and old.get("frequency") != "weekly":
        raise ValueError("周报日期规则需要重新设置")
    if report_type == "monthly" and old.get("frequency") != "monthly_last":
        raise ValueError("月报日期规则需要重新设置")
    times = old.get("times")
    if not isinstance(times, list) or not times:
        raise ValueError("生成时间为空")
    for slot in times:
        if not isinstance(slot, dict) or slot.get("ampm") not in ("am", "pm") or not 1 <= int(slot.get("hour", 0)) <= 12 or not 0 <= int(slot.get("minute", -1)) <= 59:
            raise ValueError("生成时间格式错误")
    return {"frequency": old["frequency"], "day_of_week": str(old.get("day_of_week") or "1"), "times": times}


def migrate_legacy_schedules() -> None:
    from app.db import connect
    from app.settings import settings
    with connect() as conn:
        backup_path = settings.data_dir / "schedule-migration-backup.json"
        if not backup_path.exists():
            snapshot = {
                "jobs": [dict(row) for row in conn.execute("SELECT id, schedule_mode, schedule_config, cron_expression FROM report_jobs WHERE schedule_mode != 'calendar'")],
                "periodic_reports": [dict(row) for row in conn.execute("SELECT report_type, schedule_mode, schedule_config, cron_expression FROM periodic_report_settings WHERE schedule_mode != 'calendar'")],
            }
            if snapshot["jobs"] or snapshot["periodic_reports"]:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                with open(backup_path, "x", encoding="utf-8") as handle:
                    json.dump(snapshot, handle, ensure_ascii=False, indent=2)
                try:
                    os.chmod(backup_path, 0o600)
                except OSError:
                    pass
        jobs = conn.execute("SELECT id, schedule_mode, schedule_config FROM report_jobs WHERE schedule_mode = 'simple'").fetchall()
        for row in jobs:
            try:
                config = _job_config(row["schedule_config"])
                conn.execute("UPDATE report_jobs SET schedule_mode = 'calendar', schedule_config = ?, schedule_label = ?, cron_expression = '', load_error = '' WHERE id = ?",
                             (json.dumps(config, ensure_ascii=False), label(config), row["id"]))
            except (ValueError, TypeError, KeyError) as exc:
                conn.execute("UPDATE report_jobs SET load_error = ? WHERE id = ?", (f"旧计划已暂停：{exc}", row["id"]))
                logger.warning("任务 %s 的旧计划未转换：%s", row["id"], exc)
        settings = conn.execute("SELECT report_type, schedule_mode, schedule_config FROM periodic_report_settings WHERE schedule_mode = 'simple'").fetchall()
        for row in settings:
            try:
                config = _periodic_config(row["schedule_config"], row["report_type"])
                conn.execute("UPDATE periodic_report_settings SET schedule_mode = 'calendar', schedule_config = ?, cron_expression = '', load_error = '' WHERE report_type = ?",
                             (json.dumps(config, ensure_ascii=False), row["report_type"]))
            except (ValueError, TypeError, KeyError) as exc:
                conn.execute("UPDATE periodic_report_settings SET load_error = ? WHERE report_type = ?", (f"旧计划已暂停：{exc}", row["report_type"]))
                logger.warning("%s 报告的旧计划未转换：%s", row["report_type"], exc)
