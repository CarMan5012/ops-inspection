from contextlib import nullcontext
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import sys
import types

import pytest


def _cleanup_module():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import cleanup

    return cleanup


class _Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, row):
        self.row = row

    def execute(self, _query, _params=()):
        if "SELECT COUNT(*) FROM periodic_report_runs" in _query:
            return _Rows((0,))
        return _Rows(self.row)


def test_manual_cleanup_ignores_deprecated_toggle(monkeypatch):
    cleanup = _cleanup_module()
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(_Connection({"allow_manual_cleanup": 0})))
    for name in (
        "_cleanup_periodic_archives", "_cleanup_screenshot_artifacts",
        "_cleanup_report_artifacts", "_cleanup_logs", "_cleanup_browser_state",
        "_cleanup_orphan_files", "_cleanup_old_run_records",
    ):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)

    result = cleanup.run_cleanup(mode="manual")

    assert result["status"] == "success"


@pytest.mark.parametrize("month", ["2026-00", "2026-13", "2026-%", "2026-10_extra", "October"])
def test_month_cleanup_rejects_invalid_month(month):
    cleanup = _cleanup_module()

    with pytest.raises(ValueError, match="YYYY-MM"):
        cleanup.run_cleanup(mode="dry_run", dry_run=True, target_month=month)


def test_month_cleanup_rejects_current_and_future_months():
    cleanup = _cleanup_module()
    from zoneinfo import ZoneInfo

    current_month = datetime.now(ZoneInfo(cleanup.settings.default_timezone)).strftime("%Y-%m")
    for month in (current_month, "9999-12"):
        with pytest.raises(ValueError, match="已结束的月份"):
            cleanup.estimate_cleanup(month)


def test_month_cleanup_does_not_touch_logs_or_login_state(monkeypatch):
    cleanup = _cleanup_module()
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(_Connection({"protect_recent_days": 3})))
    touched = []
    for name in (
        "_cleanup_periodic_archives",
        "_cleanup_screenshot_artifacts",
        "_cleanup_report_artifacts",
        "_cleanup_logs",
        "_cleanup_browser_state",
        "_cleanup_orphan_files",
        "_cleanup_old_run_records",
    ):
        monkeypatch.setattr(cleanup, name, lambda *args, _name=name, **kwargs: touched.append(_name))

    result = cleanup.run_cleanup(mode="dry_run", dry_run=True, target_month="2020-10")

    assert result["status"] == "success"
    assert "_cleanup_logs" not in touched
    assert "_cleanup_browser_state" not in touched
    assert "_cleanup_orphan_files" not in touched
    assert "_cleanup_screenshot_artifacts" in touched


def test_month_estimate_includes_periodic_run_records(monkeypatch):
    cleanup = _cleanup_module()
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)

    class Connection(_Connection):
        def execute(self, query, params=()):
            if "SELECT * FROM periodic_report_runs" in query:
                assert params == ("2020-10%",)
                class Rows:
                    def fetchall(self):
                        return [
                            {"id": i, "started_at": "2020-10-01 10:00:00", "finished_at": "2020-10-01 10:01:00"}
                            for i in range(1, 4)
                        ]
                return Rows()
            return super().execute(query, params)

    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(Connection({"protect_recent_days": 3})))
    for name in (
        "_cleanup_periodic_archives", "_cleanup_screenshot_artifacts",
        "_cleanup_report_artifacts", "_cleanup_old_run_records",
    ):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)

    result = cleanup.estimate_cleanup("2020-10")

    assert result["status"] == "success"
    assert result["deleted_periodic_run_records_count"] == 3


def test_month_cleanup_counts_deleted_periodic_run_records(monkeypatch):
    cleanup = _cleanup_module()
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE storage_cleanup_settings (id INTEGER PRIMARY KEY, protect_recent_days INTEGER);
        INSERT INTO storage_cleanup_settings VALUES (1, 1);
        CREATE TABLE periodic_report_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT);
        INSERT INTO periodic_report_runs VALUES (1, '2020-10-01 10:00:00', '2020-10-01 10:01:00');
        INSERT INTO periodic_report_runs VALUES (2, '2020-10-02 10:00:00', '2020-10-02 10:01:00');
        INSERT INTO periodic_report_runs VALUES (3, '2020-11-01 10:00:00', '2020-11-01 10:01:00');
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    monkeypatch.setattr(cleanup, "connect", lambda: connection)
    for name in (
        "_cleanup_periodic_archives", "_cleanup_screenshot_artifacts",
        "_cleanup_report_artifacts", "_cleanup_old_run_records",
    ):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)

    try:
        result = cleanup.run_cleanup(mode="manual", target_month="2020-10")
        assert result["status"] == "success"
        assert result["deleted_periodic_run_records_count"] == 2
        assert connection.execute("SELECT COUNT(*) FROM periodic_report_runs").fetchone()[0] == 1
    finally:
        connection.close()


def test_selected_month_preview_and_cleanup_all_scopes(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    from app import storage_paths

    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "october.log"
    log_file.write_text("old log", encoding="utf-8")
    timestamp = datetime(2020, 10, 15, 12).timestamp()
    os.utime(log_file, (timestamp, timestamp))

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE storage_cleanup_settings (id INTEGER PRIMARY KEY, protect_recent_days INTEGER);
        INSERT INTO storage_cleanup_settings VALUES (1, 1);
        CREATE TABLE run_records (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report_path TEXT);
        INSERT INTO run_records VALUES (1, '2020-10-12 09:00:00', '2020-10-12 09:01:00', '');
        CREATE TABLE screenshot_results (id INTEGER PRIMARY KEY, run_id INTEGER, file_path TEXT);
        CREATE TABLE periodic_report_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, zip_path TEXT, mail_status TEXT);
        INSERT INTO periodic_report_runs VALUES (2, '2020-10-13 09:00:00', '2020-10-13 09:01:00', '', 'success');
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "report_dir", data_dir / "reports")
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: connection)
    monkeypatch.setattr(storage_paths, "get_run_date", lambda run_id: ("2020", "10", "12"))

    try:
        for scope, kind in (("inspection", "run_record"), ("periodic", "periodic_record"), ("logs", "log")):
            preview = cleanup.preview_cleanup(scopes=[scope], target_month="2020-10")
            assert preview["status"] == "success"
            assert [item["kind"] for item in preview["items"]] == [kind]
        assert log_file.exists()
        assert connection.execute("SELECT COUNT(*) FROM run_records").fetchone()[0] == 1

        result = cleanup.run_cleanup(mode="manual", scopes=["inspection", "periodic", "logs"], target_month="2020-10")
        assert result["status"] == "success"
        assert result["deleted_logs_count"] == 1
        assert result["deleted_run_records_count"] == 1
        assert result["deleted_periodic_run_records_count"] == 1
        assert not log_file.exists()
        assert connection.execute("SELECT COUNT(*) FROM run_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM periodic_report_runs").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("scopes,days", [([], 30), (["unknown"], 30), (["logs"], 1), (["logs"], "30")])
def test_selected_cleanup_rejects_invalid_filters(scopes, days):
    cleanup = _cleanup_module()
    with pytest.raises(ValueError):
        cleanup.run_cleanup(mode="manual", scopes=scopes, older_than_days=days)


def test_selected_log_cleanup_does_not_touch_other_data(monkeypatch):
    cleanup = _cleanup_module()
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(_Connection({"protect_recent_days": 90})))
    touched = []
    for name in (
        "_cleanup_periodic_archives", "_cleanup_screenshot_artifacts", "_cleanup_report_artifacts",
        "_cleanup_logs", "_cleanup_browser_state", "_cleanup_orphan_files", "_cleanup_old_run_records",
        "_cleanup_old_periodic_run_records",
    ):
        monkeypatch.setattr(cleanup, name, lambda *args, _name=name, **kwargs: touched.append((_name, args)))

    result = cleanup.run_cleanup(mode="manual", scopes=["logs"], older_than_days=30)

    assert result["status"] == "success"
    assert [name for name, _ in touched] == ["_cleanup_logs"]
    assert touched[0][1][0] == 30
    assert timedelta(hours=20) < datetime.now() - touched[0][1][1] < timedelta(days=2)


def test_selected_log_cleanup_uses_chosen_age(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    old_log = log_dir / "old.log"
    recent_log = log_dir / "recent.log"
    old_log.write_text("old", encoding="utf-8")
    recent_log.write_text("recent", encoding="utf-8")
    for path, days in ((old_log, 40), (recent_log, 3)):
        timestamp = (datetime.now() - timedelta(days=days)).timestamp()
        os.utime(path, (timestamp, timestamp))
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(_Connection({"protect_recent_days": 90})))

    result = cleanup.run_cleanup(mode="manual", scopes=["logs"], older_than_days=30)

    assert result["status"] == "success"
    assert result["deleted_logs_count"] == 1
    assert not old_log.exists()
    assert recent_log.exists()


def test_cleanup_preview_lists_exact_files_without_deleting(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    old = log_dir / "old.log"
    recent = log_dir / "recent.log"
    old.write_text("old", encoding="utf-8")
    recent.write_text("recent", encoding="utf-8")
    for path, days in ((old, 40), (recent, 3)):
        timestamp = (datetime.now() - timedelta(days=days)).timestamp()
        os.utime(path, (timestamp, timestamp))
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(_Connection({"protect_recent_days": 90})))

    preview = cleanup.preview_cleanup(scopes=["logs"], older_than_days=30)

    assert preview["total_items"] == 1
    assert preview["items"][0]["name"] == "old.log"
    assert preview["items"][0]["path"] == "logs/old.log"
    assert preview["items"][0]["size"] == 3
    assert old.exists() and recent.exists()

    old.write_text("changed", encoding="utf-8")
    changed = cleanup.preview_cleanup(scopes=["logs"], older_than_days=30)
    assert changed["fingerprint"] != preview["fingerprint"]


def test_cleanup_preview_paginates_without_changing_fingerprint(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    for number in range(3):
        path = log_dir / f"{number}.log"
        path.write_text(str(number), encoding="utf-8")
        timestamp = (datetime.now() - timedelta(days=40)).timestamp()
        os.utime(path, (timestamp, timestamp))
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(_Connection({"protect_recent_days": 90})))

    first = cleanup.preview_cleanup(scopes=["logs"], older_than_days=30, page=1, page_size=2)
    second = cleanup.preview_cleanup(scopes=["logs"], older_than_days=30, page=2, page_size=2)
    assert first["total_items"] == second["total_items"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert first["fingerprint"] == second["fingerprint"]
    assert {item["name"] for item in first["items"]}.isdisjoint({item["name"] for item in second["items"]})


def test_confirmed_cleanup_does_not_delete_file_added_after_preview(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    original = log_dir / "original.log"
    original.write_text("original", encoding="utf-8")
    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(original, (old_time, old_time))
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(_Connection({"protect_recent_days": 3})))

    preview = cleanup.preview_cleanup(scopes=["logs"], older_than_days=30, include_all_items=True)
    added = log_dir / "added.log"
    added.write_text("added", encoding="utf-8")
    os.utime(added, (old_time, old_time))
    with cleanup.confirmed_cleanup(preview["_all_items"]):
        result = cleanup.run_cleanup(mode="manual", scopes=["logs"], older_than_days=30)

    assert result["status"] == "partial_success"
    assert result["deleted_logs_count"] == 1
    assert not original.exists()
    assert added.exists()


def test_confirmed_cleanup_does_not_delete_record_added_after_preview(monkeypatch):
    cleanup = _cleanup_module()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE storage_cleanup_settings (id INTEGER PRIMARY KEY, protect_recent_days INTEGER);
        INSERT INTO storage_cleanup_settings VALUES (1, 1);
        CREATE TABLE run_records (id INTEGER PRIMARY KEY, status TEXT, started_at TEXT, finished_at TEXT);
        INSERT INTO run_records VALUES (1, 'success', '2020-10-01 10:00:00', '2020-10-01 10:01:00');
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: conn)
    for name in ("_cleanup_screenshot_artifacts", "_cleanup_report_artifacts", "_cleanup_orphan_files"):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)
    try:
        preview = cleanup.preview_cleanup(scopes=["inspection"], older_than_days=30, include_all_items=True)
        conn.execute("INSERT INTO run_records VALUES (2, 'success', '2020-10-02 10:00:00', '2020-10-02 10:01:00')")
        with cleanup.confirmed_cleanup(preview["_all_items"]):
            result = cleanup.run_cleanup(mode="manual", scopes=["inspection"], older_than_days=30)
        assert result["deleted_run_records_count"] == 1
        assert [row[0] for row in conn.execute("SELECT id FROM run_records")] == [2]
    finally:
        conn.close()


def test_invalid_timestamps_and_unfinished_runs_are_not_cleanup_candidates():
    cleanup = _cleanup_module()
    assert cleanup.parse_db_time("") == datetime.max
    assert cleanup.parse_db_time("2020-10-01 garbage") == datetime.max
    for status, finished_at in (("running", None), ("waiting", None), ("running", "2020-10-01 10:01:00")):
        assert not cleanup._finished_run({
            "status": status, "started_at": "2020-10-01 10:00:00", "finished_at": finished_at,
        })
    assert not cleanup._finished_run({
        "status": "success", "started_at": "broken", "finished_at": "2020-10-01 10:01:00",
    })


def test_month_cleanup_keeps_running_periodic_record(monkeypatch):
    cleanup = _cleanup_module()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE storage_cleanup_settings (id INTEGER PRIMARY KEY, protect_recent_days INTEGER);
        INSERT INTO storage_cleanup_settings VALUES (1, 1);
        CREATE TABLE periodic_report_runs (id INTEGER PRIMARY KEY, status TEXT, started_at TEXT, finished_at TEXT);
        INSERT INTO periodic_report_runs VALUES (1, 'success', '2020-10-01 10:00:00', '2020-10-01 10:01:00');
        INSERT INTO periodic_report_runs VALUES (2, 'running', '2020-10-02 10:00:00', NULL);
        INSERT INTO periodic_report_runs VALUES (3, 'waiting', '2020-10-03 10:00:00', NULL);
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: conn)
    for name in ("_cleanup_periodic_archives", "_cleanup_screenshot_artifacts", "_cleanup_report_artifacts", "_cleanup_old_run_records"):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)
    try:
        preview = cleanup.preview_cleanup(scopes=["periodic"], target_month="2020-10")
        assert [item["name"] for item in preview["items"]] == ["周期报告记录 #1"]
        result = cleanup.run_cleanup(mode="manual", scopes=["periodic"], target_month="2020-10")
        assert result["deleted_periodic_run_records_count"] == 1
        assert [row[0] for row in conn.execute("SELECT id FROM periodic_report_runs ORDER BY id")] == [2, 3]
    finally:
        conn.close()


def test_month_cleanup_keeps_active_inspection_files_and_record(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    from app import storage_paths
    data_dir = tmp_path / "data"
    report_dir = data_dir / "reports"
    files = {}
    for run_id in (1, 2):
        screenshot = report_dir / "2020" / "10" / "01" / f"capture-{run_id}.png"
        report = report_dir / "2020" / "10" / "01" / f"report-{run_id}.docx"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(b"image")
        report.write_bytes(b"report")
        files[run_id] = (screenshot, report)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE storage_cleanup_settings (id INTEGER PRIMARY KEY, protect_recent_days INTEGER);
        INSERT INTO storage_cleanup_settings VALUES (1, 1);
        CREATE TABLE run_records (id INTEGER PRIMARY KEY, status TEXT, started_at TEXT, finished_at TEXT, report_path TEXT);
        INSERT INTO run_records VALUES (1, 'running', '2020-10-01 10:00:00', NULL, '');
        INSERT INTO run_records VALUES (2, 'success', '2020-10-01 10:00:00', '2020-10-01 10:01:00', '');
        CREATE TABLE screenshot_results (run_id INTEGER, file_path TEXT);
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    for run_id, (screenshot, report) in files.items():
        conn.execute("UPDATE run_records SET report_path = ? WHERE id = ?", (str(report), run_id))
        conn.execute("INSERT INTO screenshot_results VALUES (?, ?)", (run_id, str(screenshot)))
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "report_dir", report_dir)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: conn)
    monkeypatch.setattr(storage_paths, "get_run_date", lambda _run_id: ("2020", "10", "01"))
    try:
        result = cleanup.run_cleanup(mode="manual", scopes=["inspection"], target_month="2020-10")
        assert result["status"] == "success"
        assert result["deleted_screenshots_count"] == 1
        assert result["deleted_reports_count"] == 1
        assert result["deleted_run_records_count"] == 1
        assert all(path.exists() for path in files[1])
        assert all(not path.exists() for path in files[2])
        assert [row[0] for row in conn.execute("SELECT id FROM run_records")] == [1]
    finally:
        conn.close()


def test_cleanup_api_rejects_stale_preview_before_deleting(monkeypatch):
    cleanup = _cleanup_module()
    from app import main
    from fastapi import HTTPException

    deleted = []
    monkeypatch.setattr(cleanup, "preview_cleanup", lambda **kwargs: {
        "status": "success", "fingerprint": "b" * 64, "total_items": 1,
    })
    monkeypatch.setattr(cleanup, "run_cleanup", lambda **kwargs: deleted.append(kwargs))

    with pytest.raises(HTTPException) as error:
        main.api_run_storage_cleanup({
            "scopes": ["logs"], "older_than_days": 30,
            "preview_fingerprint": "a" * 64,
        })
    assert error.value.status_code == 409
    assert not deleted


def test_cleanup_preview_lists_run_records(monkeypatch):
    cleanup = _cleanup_module()
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    old = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    connection.executescript("""
        CREATE TABLE storage_cleanup_settings (id INTEGER PRIMARY KEY, protect_recent_days INTEGER);
        INSERT INTO storage_cleanup_settings VALUES (1, 1);
        CREATE TABLE run_records (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT);
    """)
    connection.executemany("INSERT INTO run_records VALUES (?, ?, ?)", [(1, old, old), (2, recent, recent)])
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: connection)
    for name in ("_cleanup_screenshot_artifacts", "_cleanup_report_artifacts", "_cleanup_orphan_files"):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)
    try:
        result = cleanup.preview_cleanup(scopes=["inspection"], older_than_days=30)
        assert result["total_items"] == 1
        assert result["items"][0]["kind"] == "run_record"
        assert result["items"][0]["name"] == "巡检记录 #1"
        assert connection.execute("SELECT COUNT(*) FROM run_records").fetchone()[0] == 2
    finally:
        connection.close()


def test_selected_periodic_cleanup_removes_only_old_periodic_records(monkeypatch):
    cleanup = _cleanup_module()
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    connection.executescript("""
        CREATE TABLE storage_cleanup_settings (id INTEGER PRIMARY KEY, protect_recent_days INTEGER);
        INSERT INTO storage_cleanup_settings VALUES (1, 1);
        CREATE TABLE periodic_report_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT);
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    connection.executemany(
        "INSERT INTO periodic_report_runs VALUES (?, ?, ?)",
        [(1, old, old), (2, recent, recent)],
    )
    monkeypatch.setattr(cleanup, "connect", lambda: connection)
    monkeypatch.setattr(cleanup, "_cleanup_periodic_archives", lambda *args, **kwargs: None)
    monkeypatch.setattr(cleanup, "_cleanup_orphan_files", lambda *args, **kwargs: None)

    try:
        result = cleanup.run_cleanup(mode="manual", scopes=["periodic"], older_than_days=30)
        assert result["status"] == "success"
        assert result["deleted_periodic_run_records_count"] == 1
        assert connection.execute("SELECT id FROM periodic_report_runs").fetchone()[0] == 2
    finally:
        connection.close()


def test_auto_cleanup_removes_expired_file_and_records_success(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    expired_file = log_dir / "expired.log"
    expired_file.write_text("old log", encoding="utf-8")
    old_time = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(expired_file, (old_time, old_time))

    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    recorded = []

    class Connection:
        def execute(self, query, params=()):
            if "SELECT * FROM storage_cleanup_settings" in query:
                return _Rows({"enabled": 1, "protect_recent_days": 1, "log_retention_days": 2})
            if "INSERT INTO storage_cleanup_runs" in query:
                recorded.append(params)
            return _Rows(None)

    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(Connection()))
    for name in (
        "_cleanup_periodic_archives", "_cleanup_screenshot_artifacts",
        "_cleanup_report_artifacts", "_cleanup_browser_state",
        "_cleanup_orphan_files", "_cleanup_old_run_records",
    ):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)
    result = cleanup.run_cleanup(mode="auto")

    assert result["status"] == "success"
    assert result["deleted_logs_count"] == 1
    assert result["deleted_files_count"] == 1
    assert not expired_file.exists()
    assert len(recorded) == 1


def test_auto_cleanup_reports_file_delete_failure(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    expired_file = log_dir / "locked.log"
    expired_file.write_text("old log", encoding="utf-8")
    old_time = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(expired_file, (old_time, old_time))
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    recorded = []

    class Connection:
        def execute(self, query, params=()):
            if "SELECT * FROM storage_cleanup_settings" in query:
                return _Rows({"enabled": 1, "protect_recent_days": 1, "log_retention_days": 2})
            if "INSERT INTO storage_cleanup_runs" in query:
                recorded.append(params)
            return _Rows(None)

    monkeypatch.setattr(cleanup, "connect", lambda: nullcontext(Connection()))
    for name in (
        "_cleanup_periodic_archives", "_cleanup_screenshot_artifacts",
        "_cleanup_report_artifacts", "_cleanup_browser_state",
        "_cleanup_orphan_files", "_cleanup_old_run_records",
    ):
        monkeypatch.setattr(cleanup, name, lambda *args, **kwargs: None)
    record_cleanup_calls = []
    monkeypatch.setattr(cleanup, "_cleanup_old_run_records", lambda *args, **kwargs: record_cleanup_calls.append(True))

    original_unlink = Path.unlink

    def deny_unlink(path, *args, **kwargs):
        if path == expired_file:
            raise PermissionError("permission denied")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny_unlink)
        result = cleanup.run_cleanup(mode="auto")

    assert result["status"] == "failed"
    assert "permission denied" in result["error_summary"]
    assert result["deleted_files_count"] == 0
    assert expired_file.exists()
    assert record_cleanup_calls == []
    assert len(recorded) == 1
    assert recorded[0][1] == "failed"


def test_directory_cleanup_reports_nested_file_failure(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    legacy_dir = data_dir / "screenshots" / "42"
    legacy_dir.mkdir(parents=True)
    locked_file = legacy_dir / "locked.png"
    locked_file.write_bytes(b"image")
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    original_unlink = Path.unlink

    def deny_unlink(path, *args, **kwargs):
        if path == locked_file:
            raise PermissionError("permission denied")
        return original_unlink(path, *args, **kwargs)

    errors = []
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny_unlink)
        deleted, _ = cleanup.safe_delete_dir(legacy_dir, errors=errors)

    assert deleted == 0
    assert locked_file.exists()
    assert any("permission denied" in error for error in errors)


def test_orphan_artifacts_keep_their_own_retention_after_record_expires(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    data_dir = tmp_path / "data"
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "report_dir", data_dir / "reports")
    monkeypatch.setattr(cleanup.settings, "log_dir", data_dir / "logs")
    monkeypatch.setattr(cleanup.settings, "browser_state_dir", data_dir / "browser-state")
    started_at = datetime.now() - timedelta(days=40)
    day_dir = data_dir / "reports" / started_at.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True)
    screenshot = day_dir / "inspection.png"
    screenshot.write_bytes(b"image")
    report = day_dir / "inspection.docx"
    report.write_bytes(b"report")
    forty_days_ago = started_at.timestamp()
    os.utime(screenshot, (forty_days_ago, forty_days_ago))
    os.utime(report, (forty_days_ago, forty_days_ago))

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE storage_cleanup_settings (
            id INTEGER PRIMARY KEY, enabled INTEGER, allow_manual_cleanup INTEGER,
            protect_recent_days INTEGER, screenshot_retention_days INTEGER,
            report_retention_days INTEGER, run_record_retention_days INTEGER
        );
        INSERT INTO storage_cleanup_settings VALUES (1, 1, 1, 1, 90, 90, 30);
        CREATE TABLE run_records (
            id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report_path TEXT
        );
        CREATE TABLE screenshot_results (
            run_id INTEGER REFERENCES run_records(id) ON DELETE CASCADE, file_path TEXT
        );
        CREATE TABLE periodic_report_runs (
            id INTEGER, zip_path TEXT, mail_status TEXT, finished_at TEXT, started_at TEXT
        );
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    conn.execute(
        "INSERT INTO run_records VALUES (1, ?, ?, ?)",
        (started_at.isoformat(sep=" "), started_at.isoformat(sep=" "), str(report)),
    )
    conn.execute("INSERT INTO screenshot_results VALUES (1, ?)", (str(screenshot),))
    monkeypatch.setattr(cleanup, "connect", lambda: conn)
    try:
        first = cleanup.run_cleanup(mode="auto")
        assert first["status"] == "success"
        assert first["deleted_run_records_count"] == 1
        assert screenshot.exists()
        assert report.exists()

        second = cleanup.run_cleanup(mode="auto")
        assert second["status"] == "success"
        assert second["deleted_screenshots_count"] == 0
        assert second["deleted_reports_count"] == 0
        assert screenshot.exists()
        assert report.exists()

        expired_time = (datetime.now() - timedelta(days=100)).timestamp()
        os.utime(screenshot, (expired_time, expired_time))
        os.utime(report, (expired_time, expired_time))
        third = cleanup.run_cleanup(mode="auto")
        assert third["status"] == "success"
        assert third["deleted_screenshots_count"] == 1
        assert third["deleted_reports_count"] == 1
        assert not screenshot.exists()
        assert not report.exists()
    finally:
        conn.close()


def test_failed_record_delete_is_not_counted(monkeypatch):
    cleanup = _cleanup_module()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE run_records (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT);
        INSERT INTO run_records VALUES (1, '2020-01-01 00:00:00', '2020-01-01 00:00:00');
        CREATE TRIGGER block_delete BEFORE DELETE ON run_records
        BEGIN SELECT RAISE(ABORT, 'delete blocked'); END;
    """)
    monkeypatch.setattr(cleanup, "connect", lambda: conn)
    results = {"deleted_run_records_count": 0, "errors": []}
    try:
        cleanup._cleanup_old_run_records(1, datetime.now() - timedelta(days=1), False, results)
        assert results["deleted_run_records_count"] == 0
        assert "delete blocked" in results["errors"][0]
        assert conn.execute("SELECT COUNT(*) FROM run_records").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize(
    "mode,config,after,expected",
    [
        ("daily", {"time": "09:15"}, datetime(2026, 10, 12, 9, 10), datetime(2026, 10, 12, 9, 15)),
        ("weekly", {"time": "09:15", "day_of_week": 3}, datetime(2026, 10, 12, 9, 10), datetime(2026, 10, 14, 9, 15)),
        ("monthly", {"time": "09:15", "day_of_month": 15}, datetime(2026, 10, 12, 9, 10), datetime(2026, 10, 15, 9, 15)),
    ],
)
def test_auto_cleanup_next_time_for_each_frequency(mode, config, after, expected):
    import json
    from zoneinfo import ZoneInfo
    from app import scheduler

    zone = ZoneInfo("Asia/Shanghai")
    actual = scheduler._cleanup_next(
        {"cleanup_schedule_mode": mode, "cleanup_schedule_config": json.dumps(config)},
        after.replace(tzinfo=zone),
    )
    assert actual == expected.replace(tzinfo=zone)


def test_disabled_auto_cleanup_job_does_not_execute(monkeypatch):
    from app import scheduler, db

    monkeypatch.setattr(db, "connect", lambda: nullcontext(_Connection({"enabled": 0})))
    executions = []
    monkeypatch.setattr(scheduler, "run_storage_cleanup_job", lambda: executions.append("cleanup"))
    monkeypatch.setattr(scheduler, "_upsert_job", lambda *args, **kwargs: executions.append("schedule"))

    scheduler._run_storage_cleanup_job()
    assert executions == []


def test_enabled_auto_cleanup_job_schedules_next_before_executing(monkeypatch):
    import json
    from app import scheduler, db

    config = {"enabled": 1, "cleanup_schedule_mode": "daily", "cleanup_schedule_config": json.dumps({"time": "02:30"})}
    monkeypatch.setattr(db, "connect", lambda: nullcontext(_Connection(config)))
    events = []
    monkeypatch.setattr(scheduler, "_upsert_job", lambda name, callback, trigger: events.append(("schedule", name)))
    monkeypatch.setattr(scheduler, "run_storage_cleanup_job", lambda: events.append(("cleanup", None)))

    scheduler._run_storage_cleanup_job()
    assert events == [("schedule", "storage-cleanup-task"), ("cleanup", None)]


def test_auto_cleanup_removes_all_configured_artifact_types(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    from app import storage_paths

    data_dir = tmp_path / "data"
    old_date = datetime.now() - timedelta(days=100)
    stamp = old_date.strftime("%Y-%m-%d %H:%M:%S")
    day_dir = data_dir / "reports" / old_date.strftime("%Y/%m/%d")
    log_dir = data_dir / "logs"
    state_dir = data_dir / "browser-state"
    periodic_dir = day_dir.parent
    for directory in (day_dir, log_dir, state_dir, periodic_dir):
        directory.mkdir(parents=True, exist_ok=True)
    files = {
        "screenshot": day_dir / "capture.png",
        "report": day_dir / "report.docx",
        "archive": periodic_dir / "archive.zip",
        "log": log_dir / "old.log",
        "state": state_dir / "auth.json",
    }
    for path in files.values():
        path.write_bytes(b"old")
        os.utime(path, (old_date.timestamp(), old_date.timestamp()))

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE storage_cleanup_settings (
            id INTEGER PRIMARY KEY, protect_recent_days INTEGER,
            periodic_sent_retention_days INTEGER, periodic_failed_retention_days INTEGER,
            screenshot_retention_days INTEGER, report_retention_days INTEGER,
            run_record_retention_days INTEGER, log_retention_days INTEGER,
            browser_state_retention_days INTEGER, browser_state_cleanup_enabled INTEGER
        );
        INSERT INTO storage_cleanup_settings VALUES (1, 3, 30, 30, 30, 30, 30, 30, 30, 1);
        CREATE TABLE run_records (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report_path TEXT);
        CREATE TABLE screenshot_results (run_id INTEGER REFERENCES run_records(id) ON DELETE CASCADE, file_path TEXT);
        CREATE TABLE periodic_report_runs (id INTEGER PRIMARY KEY, zip_path TEXT, mail_status TEXT, finished_at TEXT, started_at TEXT);
        CREATE TABLE storage_cleanup_runs (
            mode TEXT, status TEXT, started_at TEXT, finished_at TEXT,
            deleted_files_count INTEGER, deleted_bytes INTEGER,
            deleted_screenshots_count INTEGER, deleted_reports_count INTEGER,
            deleted_periodic_archives_count INTEGER, deleted_logs_count INTEGER,
            deleted_browser_state_count INTEGER, deleted_run_records_count INTEGER,
            error_summary TEXT, detail_json TEXT
        );
    """)
    connection.execute("INSERT INTO run_records VALUES (1, ?, ?, ?)", (stamp, stamp, str(files["report"])))
    connection.execute("INSERT INTO screenshot_results VALUES (1, ?)", (str(files["screenshot"]),))
    connection.execute("INSERT INTO periodic_report_runs VALUES (2, ?, 'success', ?, ?)", (str(files["archive"]), stamp, stamp))
    monkeypatch.setattr(cleanup.settings, "data_dir", data_dir)
    monkeypatch.setattr(cleanup.settings, "log_dir", log_dir)
    monkeypatch.setattr(cleanup.settings, "browser_state_dir", state_dir)
    monkeypatch.setattr(cleanup.settings, "report_dir", data_dir / "reports")
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cleanup, "connect", lambda: connection)
    monkeypatch.setattr(storage_paths, "get_run_date", lambda run_id: tuple(old_date.strftime("%Y/%m/%d").split("/")))
    try:
        result = cleanup.run_cleanup(mode="auto")
        assert result["status"] == "success"
        assert result["deleted_files_count"] == 5
        assert result["deleted_screenshots_count"] == 1
        assert result["deleted_reports_count"] == 1
        assert result["deleted_periodic_archives_count"] == 1
        assert result["deleted_logs_count"] == 1
        assert result["deleted_browser_state_count"] == 1
        assert result["deleted_run_records_count"] == 1
        assert all(not path.exists() for path in files.values())
        assert connection.execute("SELECT COUNT(*) FROM run_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM storage_cleanup_runs").fetchone()[0] == 1
    finally:
        connection.close()


def test_periodic_archive_retention_uses_report_status_after_mail_removal(monkeypatch, tmp_path):
    cleanup = _cleanup_module()
    from app import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings_module.settings, "log_dir", tmp_path / "logs")
    completed_at = (datetime.now() - timedelta(days=10)).isoformat(sep=" ", timespec="seconds")
    generated = tmp_path / "generated.zip"
    failed = tmp_path / "failed.zip"
    generated.write_bytes(b"generated")
    failed.write_bytes(b"failed")

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE periodic_report_runs (id INTEGER, zip_path TEXT, status TEXT, mail_status TEXT, "
        "started_at TEXT, finished_at TEXT)"
    )
    connection.executemany(
        "INSERT INTO periodic_report_runs VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, str(generated), "success", "failed: old SMTP error", completed_at, completed_at),
            (2, str(failed), "failed", "sent", completed_at, completed_at),
        ],
    )
    monkeypatch.setattr(cleanup, "connect", lambda: connection)
    result = {"errors": [], "deleted_files_count": 0, "deleted_bytes": 0, "deleted_periodic_archives_count": 0}
    try:
        cleanup._cleanup_periodic_archives(
            sent_days=5, failed_days=30, protect_date=datetime.now() - timedelta(days=1),
            dry_run=False, results=result,
        )
    finally:
        connection.close()

    assert not generated.exists()
    assert failed.exists()
    assert result["deleted_periodic_archives_count"] == 1
