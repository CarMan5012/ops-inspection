from datetime import datetime
import sqlite3
import sys
import types
from zoneinfo import ZoneInfo

import pytest


def _periodic_reporter():
    # The production database uses SQLCipher; these checks only need SQLite SQL semantics.
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import periodic_reporter

    return periodic_reporter


def test_monthly_waits_for_both_simple_schedule_slots(monkeypatch):
    periodic = _periodic_reporter()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER, status TEXT, started_at TEXT, schedule_slot TEXT)")
    monkeypatch.setattr(periodic, "connect", lambda: conn)
    monkeypatch.setattr(periodic, "list_jobs", lambda: [{
        "id": 1, "name": "inspection", "enabled": 1, "schedule_mode": "simple",
        "schedule_config": '{"frequency":"daily","morning_enabled":true,"afternoon_enabled":true}',
    }])
    today = datetime.now(ZoneInfo(periodic.settings.default_timezone)).replace(hour=18, minute=0)
    stamp = today.strftime("%Y-%m-%d")
    conn.execute("INSERT INTO run_records (job_id, status, started_at, schedule_slot) VALUES (1, 'success', ?, 'morning')", (stamp + "T09:05:00",))

    completed, reasons = periodic.check_today_jobs_completed(today)
    assert not completed
    assert any("afternoon" in reason for reason in reasons)

    conn.execute("INSERT INTO run_records (job_id, status, started_at, schedule_slot) VALUES (1, 'success', ?, 'afternoon')", (stamp + "T17:05:00",))
    assert periodic.check_today_jobs_completed(today) == (True, [])


def test_monthly_ignores_paused_legacy_expression(monkeypatch):
    periodic = _periodic_reporter()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER, status TEXT, started_at TEXT, schedule_slot TEXT)")
    monkeypatch.setattr(periodic, "connect", lambda: conn)
    monkeypatch.setattr(periodic, "list_jobs", lambda: [{
        "id": 1, "name": "inspection", "enabled": 1, "schedule_mode": "cron",
        "cron_expression": "5 9 * * *; 5 17 * * *",
    }])
    today = datetime.now(ZoneInfo(periodic.settings.default_timezone)).replace(hour=18, minute=0)
    stamp = today.strftime("%Y-%m-%d")
    assert periodic.check_today_jobs_completed(today) == (True, [])


def test_success_in_same_hour_does_not_skip_other_slot(monkeypatch, tmp_path):
    _periodic_reporter()
    from app import repository, runner

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE run_records (job_id INTEGER, status TEXT, started_at TEXT, schedule_slot TEXT)")
    today = datetime.now(ZoneInfo(runner.settings.default_timezone)).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO run_records VALUES (1, 'success', ?, 'morning')",
        (today + "T09:05:00",),
    )
    monkeypatch.setattr(repository, "connect", lambda: conn)
    monkeypatch.setattr(runner.settings, "data_dir", tmp_path)
    monkeypatch.setattr(runner, "get_job", lambda _id: {
        "id": 1, "name": "inspection", "schedule_mode": "simple", "cron_expression": "5 9 * * *",
    })

    class NewRunStarted(Exception):
        pass

    updates = {}
    monkeypatch.setattr(runner, "create_run", lambda *_, **kwargs: 101)
    monkeypatch.setattr(runner, "update_run", lambda _id, **fields: updates.update(fields))
    monkeypatch.setattr(runner, "skip_queued_run", lambda _id, reason: updates.update(status="skipped", finished_at="2026-10-28T10:00:00+08:00") or True)
    assert runner.run_job(1, schedule_type="morning") == 0
    assert updates["status"] == "skipped" and updates["finished_at"]

    def create_run(*_args):
        raise NewRunStarted

    monkeypatch.setattr(runner, "create_run", create_run)
    with pytest.raises(NewRunStarted):
        runner._execute_job(1, schedule_type="afternoon")
