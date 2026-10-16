from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app import date_schedule, holiday_calendar


def test_calendar_schedule_weekdays_and_selected_days():
    after = datetime(2026, 10, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # Friday
    weekdays = date_schedule.normalize_schedule({"frequency": "weekdays", "times": ["09:00"]})
    weekly = date_schedule.normalize_schedule({"frequency": "weekly", "days_of_week": [7, 2], "times": ["09:00"]})
    assert date_schedule.upcoming(weekdays, after, 1)[0].date() == date(2026, 10, 12)
    assert date_schedule.upcoming(weekly, after, 2)[0].date() == date(2026, 10, 11)
    assert date_schedule.upcoming(weekly, after, 2)[1].date() == date(2026, 10, 13)


def test_api_calendar_payload_does_not_create_cron():
    from app.main import api_job_payload
    payload = api_job_payload({"name": "test", "schedule_mode": "calendar", "frequency": "weekly", "days_of_week": [2, 5], "times": ["09:05", "17:30"]})
    assert payload["cron_expression"] == ""
    assert "周二" in payload["schedule_label"]


def test_calendar_schedule_uses_makeup_day_and_pauses_unknown_date(monkeypatch):
    days = {"2026-10-10": True, "2026-10-12": False}
    monkeypatch.setattr(date_schedule, "is_workday", lambda day: days.get(day.isoformat()))
    workday = date_schedule.normalize_schedule({"frequency": "workday", "times": ["09:00"]})
    rest = date_schedule.normalize_schedule({"frequency": "non_workday", "times": ["09:00"]})
    assert date_schedule.matches(date(2026, 10, 10), workday) is True
    assert date_schedule.matches(date(2026, 10, 12), rest) is True
    assert date_schedule.matches(date(2026, 10, 13), rest) is None


def test_monthly_multiple_days_skip_missing_dates():
    config = date_schedule.normalize_schedule({"frequency": "monthly", "days_of_month": [28, 30, 31], "times": ["09:00"]})
    assert config["days_of_month"] == [28, 30, 31]
    after = datetime(2027, 1, 31, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    runs = date_schedule.upcoming(config, after, 3)
    assert [value.date() for value in runs] == [date(2027, 2, 28), date(2027, 3, 28), date(2027, 3, 30)]
    with pytest.raises(ValueError):
        date_schedule.normalize_schedule({"frequency": "monthly", "days_of_month": [], "times": ["09:00"]})


def test_random_minutes_are_stable_for_preview_and_scheduler():
    config = date_schedule.normalize_schedule({"frequency": "daily", "times": ["09:R"]})
    after = datetime(2026, 10, 9, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    first = date_schedule.upcoming(config, after, 3, job_id=42)
    assert first == date_schedule.upcoming(config, after, 3, job_id=42)
    assert len(first) == 3
    assert all(value.hour == 9 and 0 <= value.minute <= 59 for value in first)
    assert len({value.date() for value in first}) == 3
    assert first[0] == date_schedule.upcoming(config, after, 1, job_id=42)[0]
    assert "随机分钟" in date_schedule.label(config)
    with pytest.raises(ValueError):
        date_schedule.normalize_schedule({"frequency": "daily", "times": ["24:R"]})


def test_holiday_sources_validate_year_and_override_weekend():
    year = 2026
    start = date(year, 1, 1)
    from datetime import timedelta
    rows = [{"date": (start + timedelta(days=i)).isoformat(), "rest": 1 if (start + timedelta(days=i)).isoweekday() > 5 else 0} for i in range(365)]
    rows[2]["rest"] = 0  # Saturday makeup workday
    assert holiday_calendar._primary_days(year, {"code": 0, "data": rows})["2026-01-03"] is True
    with pytest.raises(ValueError):
        holiday_calendar._primary_days(year, {"code": 0, "data": rows[:3]})


def test_backup_source_uses_weekend_default_and_makeup_override():
    data = {"code": 0, "holiday": {"01-01": {"holiday": True}, "01-04": {"holiday": False}, "02-14": {"holiday": False}, "02-15": {"holiday": True}, "05-01": {"holiday": True}}}
    days = holiday_calendar._backup_days(2026, data)
    assert days["2026-01-04"] is True  # Sunday makeup day
    assert days["2026-01-03"] is False  # ordinary Saturday
    assert days["2026-01-01"] is False  # weekday holiday


def test_holiday_source_falls_back_and_caches_year(monkeypatch, tmp_path):
    from datetime import timedelta
    start = date(2026, 1, 1)
    rows = [{"date": (start + timedelta(days=i)).isoformat(), "rest": 0} for i in range(365)]
    monkeypatch.setattr(holiday_calendar.settings, "data_dir", tmp_path)
    monkeypatch.setattr(holiday_calendar, "_years", {})

    def fetch(url):
        if "timor.tech" in url:
            raise TimeoutError("主接口超时")
        return {"code": 0, "data": rows}

    monkeypatch.setattr(holiday_calendar, "_fetch_json", fetch)
    assert holiday_calendar.refresh_year(2026)
    assert holiday_calendar.status()["source"] == "备用接口"
    assert holiday_calendar._cache_path(2026).is_file()


def test_periodic_calendar_next_month_end():
    from app.scheduler import _periodic_next
    import json
    setting = {"report_type": "monthly", "schedule_config": json.dumps({"frequency": "monthly_last", "times": [{"ampm": "am", "hour": 9, "minute": 5}]})}
    after = datetime(2026, 2, 1, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert _periodic_next(setting, {"ampm": "am", "hour": 9, "minute": 5}, after) == datetime(2026, 2, 28, 9, 5, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_periodic_api_saves_calendar_without_cron(monkeypatch):
    from app.main import api_update_periodic_report
    from app import repository, scheduler
    saved = {}
    monkeypatch.setattr(repository, "get_periodic_report_setting", lambda kind: {"report_type": kind, "name": "周报", "enabled": 1})
    monkeypatch.setattr(repository, "save_periodic_report_setting", lambda kind, data: saved.update(data))
    monkeypatch.setattr(scheduler, "reload_jobs", lambda: None)
    result = api_update_periodic_report("weekly", {"name": "周报", "schedule_mode": "calendar", "day_of_week": "3", "times": [{"ampm": "am", "hour": 9, "minute": 5}]})
    assert result == {"ok": True}
    assert saved["cron_expression"] == ""
    assert saved["schedule_mode"] == "calendar"


def test_simple_schedules_migrate_and_legacy_expression_pauses(monkeypatch, tmp_path):
    import sqlite3
    from contextlib import contextmanager
    from app import db, schedule_migration
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE report_jobs (id INTEGER PRIMARY KEY, schedule_mode TEXT, schedule_config TEXT, cron_expression TEXT, schedule_label TEXT, load_error TEXT)")
    conn.execute("CREATE TABLE periodic_report_settings (report_type TEXT PRIMARY KEY, schedule_mode TEXT, schedule_config TEXT, cron_expression TEXT, load_error TEXT)")
    conn.execute("INSERT INTO report_jobs VALUES (1, 'simple', ?, '5 9 * * *', '', '')", ('{"frequency":"daily","morning_enabled":true,"morning_time":"09:05","afternoon_enabled":false}',))
    conn.execute("INSERT INTO report_jobs VALUES (2, 'cron', '', '5 9 * * *', '', '')")
    conn.execute("INSERT INTO periodic_report_settings VALUES ('monthly', 'simple', ?, '0 0 18 * * *', '')", ('{"frequency":"monthly_last","times":[{"ampm":"pm","hour":6,"minute":0}]}',))

    @contextmanager
    def connect():
        yield conn

    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(schedule_migration, "connect", connect, raising=False)
    monkeypatch.setattr(schedule_migration, "logger", type("Logger", (), {"warning": lambda *args: None})())
    monkeypatch.setattr(schedule_migration, "json", __import__("json"))
    monkeypatch.setattr(__import__("app.settings", fromlist=["settings"]).settings, "data_dir", tmp_path)
    schedule_migration.migrate_legacy_schedules()
    assert conn.execute("SELECT schedule_mode, cron_expression FROM report_jobs WHERE id = 1").fetchone()[:] == ("calendar", "")
    assert conn.execute("SELECT schedule_mode FROM report_jobs WHERE id = 2").fetchone()[0] == "cron"
    assert conn.execute("SELECT schedule_mode, cron_expression FROM periodic_report_settings WHERE report_type = 'monthly'").fetchone()[:] == ("calendar", "")
    assert (tmp_path / "schedule-migration-backup.json").is_file()
