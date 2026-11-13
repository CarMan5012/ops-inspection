from datetime import datetime, timedelta
import sqlite3
import sys
import types

import pytest


def _storage_modules():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import cleanup, storage_paths

    return cleanup, storage_paths


def _set_test_paths(monkeypatch, tmp_path, cleanup, storage_paths):
    monkeypatch.setattr(storage_paths.settings, "data_dir", tmp_path)
    monkeypatch.setattr(storage_paths.settings, "report_dir", tmp_path / "reports")
    monkeypatch.setattr(storage_paths, "get_run_date", lambda _run_id: ("2020", "01", "01"))


def test_day_layout_has_no_run_id_and_avoids_name_collisions(monkeypatch, tmp_path):
    cleanup, storage_paths = _storage_modules()
    _set_test_paths(monkeypatch, tmp_path, cleanup, storage_paths)

    screenshot_dir = storage_paths.get_screenshot_dir(42)
    report_dir = storage_paths.get_report_dir(42)
    assert screenshot_dir == tmp_path / "reports/2020/01/01"
    assert report_dir == screenshot_dir

    first = storage_paths.unique_artifact_path(screenshot_dir, "09-05_job_dashboard", ".png")
    first.write_bytes(b"image")
    second = storage_paths.unique_artifact_path(screenshot_dir, "09-05_job_dashboard", ".png")
    assert second.name == "09-05_job_dashboard-2.png"
    assert storage_paths.resolve_artifact_path(str(first)) == first.resolve()

    container_path = "/app/data/reports/2020/01/01/old.docx"
    mapped = report_dir / "old.docx"
    mapped.write_bytes(b"report")
    assert storage_paths.resolve_artifact_path(container_path) == mapped.resolve()


def test_storage_usage_classifies_files_under_reports(monkeypatch, tmp_path):
    cleanup, storage_paths = _storage_modules()
    _set_test_paths(monkeypatch, tmp_path, cleanup, storage_paths)
    monkeypatch.setattr(cleanup.settings, "db_path", tmp_path / "app.db")
    monkeypatch.setattr(cleanup.settings, "log_dir", tmp_path / "logs")
    monkeypatch.setattr(cleanup.settings, "browser_state_dir", tmp_path / "browser-state")
    monkeypatch.setattr(cleanup.settings, "ensure_dirs", lambda: None)
    day_dir = tmp_path / "reports/2020/01/01"
    day_dir.mkdir(parents=True)
    (day_dir / "capture.png").write_bytes(b"image")
    (day_dir / "daily.docx").write_bytes(b"word")
    (day_dir.parent / "weekly.zip").write_bytes(b"zip")

    usage = cleanup.get_storage_usage()
    assert usage["screenshots_bytes"] == 5
    assert usage["reports_bytes"] == 4
    assert usage["periodic_reports_bytes"] == 3


def test_cleanup_removes_only_expired_artifact_type(monkeypatch, tmp_path):
    cleanup, storage_paths = _storage_modules()
    _set_test_paths(monkeypatch, tmp_path, cleanup, storage_paths)
    screenshot = storage_paths.get_screenshot_dir(42) / "09-05_job_dashboard.png"
    report = storage_paths.get_report_dir(42) / "09-05_job.docx"
    screenshot.write_bytes(b"image")
    report.write_bytes(b"report")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER, started_at TEXT, finished_at TEXT, report_path TEXT)")
    conn.execute("CREATE TABLE screenshot_results (run_id INTEGER, file_path TEXT)")
    conn.execute("INSERT INTO run_records VALUES (42, '2020-01-01 09:00:00', '2020-01-01 09:05:00', ?)", (str(report),))
    conn.execute("INSERT INTO screenshot_results VALUES (42, ?)", (str(screenshot),))
    monkeypatch.setattr(cleanup, "connect", lambda: conn)
    protect_date = datetime.now() - timedelta(days=7)

    result = {"deleted_files_count": 0, "deleted_bytes": 0, "deleted_screenshots_count": 0, "errors": []}
    cleanup._cleanup_screenshot_artifacts(30, protect_date, False, result)
    assert not screenshot.exists()
    assert report.exists()
    assert result["deleted_screenshots_count"] == 1

    result["deleted_reports_count"] = 0
    cleanup._cleanup_report_artifacts(30, protect_date, False, result)
    assert not report.exists()
    assert result["deleted_reports_count"] == 1


def test_cleanup_keeps_daily_report_referenced_by_newer_run(monkeypatch, tmp_path):
    cleanup, storage_paths = _storage_modules()
    _set_test_paths(monkeypatch, tmp_path, cleanup, storage_paths)
    report = storage_paths.get_report_dir(42) / "获取标题_2020-01-01.docx"
    report.write_bytes(b"report")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER, started_at TEXT, finished_at TEXT, report_path TEXT)")
    conn.execute("INSERT INTO run_records VALUES (42, '2020-01-01 09:00:00', '2020-01-01 09:05:00', ?)", (str(report),))
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO run_records VALUES (43, ?, ?, ?)", (today, today, str(report)))
    monkeypatch.setattr(cleanup, "connect", lambda: conn)
    result = {"deleted_files_count": 0, "deleted_bytes": 0, "deleted_reports_count": 0, "errors": []}

    cleanup._cleanup_report_artifacts(30, datetime.now() - timedelta(days=7), False, result)
    assert report.exists()
    assert result["deleted_reports_count"] == 0


def test_same_job_updates_one_daily_report(monkeypatch, tmp_path):
    _cleanup, storage_paths = _storage_modules()
    from app import report, repository

    _set_test_paths(monkeypatch, tmp_path, _cleanup, storage_paths)
    previous = {"run": None}
    monkeypatch.setattr(repository, "get_previous_run_today", lambda _job_id, _run_id: previous["run"])
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [previous["run"]] if previous["run"] else [])
    monkeypatch.setattr(repository, "list_screenshot_results", lambda _run_id: [])
    job = {"id": 7, "name": "production", "report_title": "获取标题"}
    first = {"id": 42, "started_at": "2020-01-01T09:05:00+08:00", "status": "success", "schedule_slot": "calendar-0905"}
    second = {"id": 43, "started_at": "2020-01-01T17:05:00+08:00", "status": "success", "schedule_slot": "calendar-1705"}

    first_path = report.generate_docx(job, first, [])
    previous["run"] = {"id": 42, "started_at": first["started_at"], "report_path": first_path}
    second_path = report.generate_docx(job, second, [])

    assert first_path == second_path
    assert report.Path(second_path).name == "获取标题-2020-01-01.docx"
    assert len(list((tmp_path / "reports/2020/01/01").glob("*.docx"))) == 1


def test_download_requires_file_to_belong_to_requested_run(monkeypatch, tmp_path):
    _cleanup, storage_paths = _storage_modules()
    from app import main
    from fastapi import HTTPException
    from starlette.requests import Request

    monkeypatch.setattr(storage_paths.settings, "data_dir", tmp_path)
    picture = tmp_path / "reports/2020/01/01/09-05_job_dashboard.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b"image")
    monkeypatch.setattr(main, "list_screenshot_results", lambda run_id: [{"file_path": str(picture)}] if run_id == 42 else [])
    request = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/artifact", "headers": [], "query_string": b""})

    assert main.artifact(request, "screenshots", 42, picture.name).path == picture
    download_request = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/artifact", "headers": [(b"accept", b"text/html")], "query_string": b"download=1"})
    download = main.artifact(download_request, "screenshots", 42, picture.name)
    assert download.path == picture
    assert "attachment" in download.headers["content-disposition"]
    with pytest.raises(HTTPException) as error:
        main.artifact(request, "screenshots", 43, picture.name)
    assert error.value.status_code == 404


def test_screenshot_result_time_uses_configured_timezone(monkeypatch):
    _storage_modules()
    from app import main

    monkeypatch.setattr(main.settings, "default_timezone", "Asia/Shanghai")
    result = main.with_file_url({"created_at": "2026-10-29 09:52:37"})
    assert result["created_at_local"] == "2026-10-29 17:52:37"
