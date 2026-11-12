"""Human-readable inspection schedules without cron expressions."""

from __future__ import annotations

import json
import hashlib
import hmac
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.holiday_calendar import is_workday

FREQUENCIES = {"daily", "weekdays", "weekly", "monthly", "workday", "non_workday"}
HOLIDAY_FREQUENCIES = {"workday", "non_workday"}


def normalize_schedule(payload: dict, *, enforce_daily_slots: bool = False) -> dict:
    frequency = str(payload.get("frequency") or "daily")
    if frequency not in FREQUENCIES:
        raise ValueError("请选择有效的巡检日期")
    raw_times = payload.get("times")
    if not isinstance(raw_times, list) or not 1 <= len(raw_times) <= 8:
        raise ValueError("请设置 1 到 8 个巡检时间")
    times = []
    for item in raw_times:
        if not isinstance(item, str):
            raise ValueError("巡检时间格式错误")
        if item.endswith(":R"):
            try:
                hour = int(item[:2])
            except ValueError as exc:
                raise ValueError("随机分钟时间格式错误") from exc
            if len(item) != 4 or not 0 <= hour <= 23 or item[:2] != f"{hour:02d}":
                raise ValueError("随机分钟时间格式错误")
            times.append(item)
            continue
        try:
            parsed = time.fromisoformat(item)
        except ValueError as exc:
            raise ValueError("巡检时间格式错误") from exc
        if parsed.second or parsed.microsecond:
            raise ValueError("巡检时间精确到分钟即可")
        times.append(parsed.strftime("%H:%M"))
    if enforce_daily_slots:
        if len(times) > 2 or len(set(times)) != len(times):
            raise ValueError("上午和下午各最多设置一个巡检时间")
        periods = [int(value[:2]) < 12 for value in times]
        if len(set(periods)) != len(periods):
            raise ValueError("上午和下午各最多设置一个巡检时间")
    days = payload.get("days_of_week") or []
    if frequency == "weekly":
        if not isinstance(days, list) or not days or any(type(day) is not int or day not in range(1, 8) for day in days):
            raise ValueError("请至少选择一个星期")
    month_days = payload.get("days_of_month") or []
    if frequency == "monthly":
        if not isinstance(month_days, list) or not month_days or any(type(day) is not int or day not in range(1, 32) for day in month_days):
            raise ValueError("请至少选择一个有效的月份日期")
    return {"frequency": frequency, "days_of_week": sorted(set(days)) if frequency == "weekly" else [],
            "days_of_month": sorted(set(month_days)) if frequency == "monthly" else [], "times": sorted(set(times))}


def label(config: dict) -> str:
    frequency = config["frequency"]
    names = {"daily": "每天", "weekdays": "周一至周五", "workday": "工作日", "non_workday": "非工作日"}
    if frequency == "monthly":
        prefix = "每月" + "、".join(str(day) for day in config["days_of_month"]) + "号"
    else:
        prefix = names.get(frequency) or "、".join(f"周{'一二三四五六日'[day - 1]}" for day in config["days_of_week"])
    return f"{prefix} {'、'.join(value[:2] + '点随机分钟' if value.endswith(':R') else value for value in config['times'])}"


def resolved_time(slot: str, day: date, job_id: int) -> time:
    hour = int(slot[:2])
    if slot.endswith(":R"):
        from app.settings import settings

        message = f"inspection-minute:{job_id}:{day.isoformat()}:{slot}".encode()
        digest = hmac.new(settings.secret_key.encode(), message, hashlib.sha256).digest()
        minute = int.from_bytes(digest[:8], "big") % 60
    else:
        minute = int(slot[3:])
    return time(hour, minute)


def matches(day: date, config: dict) -> bool | None:
    frequency = config["frequency"]
    if frequency == "daily":
        return True
    if frequency == "weekdays":
        return day.isoweekday() <= 5
    if frequency == "weekly":
        return day.isoweekday() in config["days_of_week"]
    if frequency == "monthly":
        return day.day in config["days_of_month"]
    workday = is_workday(day)
    if workday is None:
        return None
    return workday if frequency == "workday" else not workday


def upcoming(config: dict, after: datetime, limit: int = 3, *, job_id: int = 0) -> list[datetime]:
    if after.tzinfo is None:
        raise ValueError("计算执行时间时必须指定时区")
    found = []
    for offset in range(370):
        day = after.date() + timedelta(days=offset)
        matched = matches(day, config)
        if matched is None:
            # Never silently treat unknown holiday data as a normal weekday.
            if config["frequency"] in HOLIDAY_FREQUENCIES:
                break
            continue
        if not matched:
            continue
        candidates = sorted(datetime.combine(day, resolved_time(value, day, job_id), tzinfo=after.tzinfo)
                            for value in config["times"])
        for candidate in candidates:
            if candidate > after:
                found.append(candidate)
                if len(found) >= limit:
                    return found
    return found


def from_job(job: dict) -> dict | None:
    if job.get("schedule_mode") != "calendar":
        return None
    config = json.loads(job.get("schedule_config") or "{}")
    return normalize_schedule(config)


def local_now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))
