"""Regressions for monthly retries and notification target selection."""

import sqlite3
import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import db, dingtalk, periodic_reporter, repository, runner, scheduler, utils


def test_monthly_retry_uses_original_month_end_after_midnight(monkeypatch):
    local_tz = ZoneInfo(periodic_reporter.settings.default_timezone)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 11, 1, 0, 5, tzinfo=local_tz)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    run = {
        "id": 7, "report_type": "monthly", "status": "waiting",
        "period_end": "2026-10-31 23:59:59", "error_summary": "",
    }
    setting = {"retry_until_time": "23:50", "retry_interval_minutes": 10, "send_on_timeout": 0}
    checked_dates = []
    monkeypatch.setattr(periodic_reporter, "datetime", FrozenDatetime)
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_run", lambda _id: dict(run))
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_setting", lambda _type: setting)
    monkeypatch.setattr(periodic_reporter, "check_today_jobs_completed", lambda when: (checked_dates.append(when.date()) or False, ["未完成"]))
    monkeypatch.setattr(periodic_reporter, "save_periodic_report_run", lambda data, _id: run.update(data))

    periodic_reporter.monthly_report_retry_check(7)

    assert checked_dates == [datetime(2026, 10, 31).date()]
    assert run["status"] == "failed"
    assert "未完成" in run["error_summary"]


def test_monthly_trigger_checks_immediately_when_cutoff_already_passed(monkeypatch):
    local_tz = ZoneInfo(periodic_reporter.settings.default_timezone)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 10, 31, 23, 55, tzinfo=local_tz)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    setting = {
        "enabled": 1, "name": "月报", "wait_for_daily_jobs": 1,
        "retry_until_time": "23:50", "retry_interval_minutes": 10,
    }
    created = []
    retried = []
    monkeypatch.setattr(periodic_reporter, "datetime", FrozenDatetime)
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_setting", lambda _type: setting)
    monkeypatch.setattr(periodic_reporter, "check_today_jobs_completed", lambda _when: (False, ["未完成"]))
    monkeypatch.setattr(periodic_reporter, "save_periodic_report_run", lambda data: created.append(dict(data)) or 7)
    monkeypatch.setattr(periodic_reporter, "monthly_report_retry_check", lambda run_id: retried.append(run_id))

    assert periodic_reporter.trigger_periodic_report("monthly") == 7
    assert len(created) == 1 and created[0]["status"] == "waiting"
    assert retried == [7]


def test_scheduler_restores_waiting_monthly_runs(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE periodic_report_runs (id INTEGER, report_type TEXT, status TEXT)")
    conn.executemany("INSERT INTO periodic_report_runs VALUES (?, ?, ?)", [
        (4, "monthly", "waiting"), (5, "weekly", "waiting"), (6, "monthly", "success"),
    ])
    registrations = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            registrations.append((func, trigger, kwargs))

    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(scheduler, "scheduler", FakeScheduler())
    try:
        scheduler._restore_waiting_monthly_reports()
    finally:
        conn.close()

    assert len(registrations) == 1
    assert registrations[0][0] is periodic_reporter.monthly_report_retry_check
    assert registrations[0][2]["id"] == "monthly-retry-4"
    assert registrations[0][2]["args"] == [4]


def test_missing_selected_bot_never_falls_back_for_inspection(monkeypatch):
    updates = []
    monkeypatch.setattr(repository, "get_dingtalk_profile", lambda _id: None)
    monkeypatch.setattr(repository, "list_dingtalk_profiles", lambda: pytest.fail("must not use another bot"))
    monkeypatch.setattr(runner, "update_run", lambda _id, **fields: updates.append(fields))
    monkeypatch.setattr(dingtalk, "send_dingtalk_msg", lambda *_: pytest.fail("must not send"))

    runner._send_dingtalk_notification(
        {"id": 3, "dingtalk_enabled": 1, "dingtalk_profile_id": 99},
        10, "success", 1, 0, "",
    )

    assert updates and updates[0]["dingtalk_status"].startswith("failed:")
    assert "#99" in updates[0]["dingtalk_status"]


def _periodic_archive_fixture(monkeypatch, tmp_path, *, selected_bot_id):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE run_records (id INTEGER, job_id INTEGER, job_name TEXT, status TEXT, "
        "report_path TEXT, started_at TEXT, finished_at TEXT, success_count INTEGER, failed_count INTEGER, schedule_slot TEXT)"
    )
    run = {
        "id": 9, "report_type": "weekly", "period_start": "2026-10-01 00:00:00",
        "period_end": "2026-10-07 23:59:59", "status": "running",
        "mail_status": "", "error_summary": "",
    }
    setting = {
        "name": "周报", "send_empty_report": 1, "include_docx": 0,
        "include_screenshots": 0, "dingtalk_enabled": 1,
        "dingtalk_profile_id": selected_bot_id,
    }
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setattr(periodic_reporter, "connect", lambda: conn)
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_run", lambda _id: dict(run))
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_setting", lambda _type: setting)
    monkeypatch.setattr(periodic_reporter, "save_periodic_report_run", lambda data, _id: run.update(data))
    monkeypatch.setattr(periodic_reporter.settings, "data_dir", tmp_path)
    monkeypatch.setattr(periodic_reporter.settings, "report_dir", tmp_path / "reports")
    monkeypatch.setattr(periodic_reporter.tempfile, "mkdtemp", lambda **_: str(stage))
    return conn, run


def test_missing_selected_bot_never_falls_back_for_periodic(monkeypatch, tmp_path):
    conn, run = _periodic_archive_fixture(monkeypatch, tmp_path, selected_bot_id=99)
    monkeypatch.setattr(repository, "get_dingtalk_profile", lambda _id: None)
    monkeypatch.setattr(repository, "list_dingtalk_profiles", lambda: pytest.fail("must not use another bot"))
    monkeypatch.setattr(dingtalk, "send_dingtalk_msg", lambda *_: pytest.fail("must not send"))
    try:
        periodic_reporter.execute_periodic_report_flow(9)
    finally:
        conn.close()

    assert run["dingtalk_status"].startswith("failed:")
    assert "#99" in run["dingtalk_status"]
