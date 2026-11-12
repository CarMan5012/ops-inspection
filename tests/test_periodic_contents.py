import sqlite3
import sys
import types
import zipfile

import pytest

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import periodic_reporter


@pytest.mark.parametrize("include_docx", [0, 1])
def test_periodic_zip_respects_selected_contents(monkeypatch, tmp_path, include_docx):
    report = tmp_path / "daily.docx"
    screenshot = tmp_path / "morning-chart.png"
    afternoon_screenshot = tmp_path / "afternoon-chart.png"
    report.write_bytes(b"word")
    screenshot.write_bytes(b"image")
    afternoon_screenshot.write_bytes(b"image")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE run_records (id INTEGER, job_id INTEGER, job_name TEXT, status TEXT, "
        "report_path TEXT, started_at TEXT, finished_at TEXT, success_count INTEGER, failed_count INTEGER, schedule_slot TEXT)"
    )
    conn.execute("CREATE TABLE screenshot_results (run_id INTEGER, item_name TEXT, file_path TEXT, status TEXT)")
    conn.execute(
        "INSERT INTO run_records VALUES (1, 7, '巡检', 'success', ?, '2026-10-10T09:00:00', "
        "'2026-10-10T09:01:00', 1, 0, 'calendar-0900')",
        (str(report),),
    )
    conn.execute("INSERT INTO screenshot_results VALUES (1, '图表', ?, 'success')", (str(screenshot),))
    conn.execute(
        "INSERT INTO run_records VALUES (2, 7, '巡检', 'success', ?, '2026-10-10T17:00:00', "
        "'2026-10-10T17:01:00', 1, 0, 'calendar-1700')",
        (str(report),),
    )
    conn.execute("INSERT INTO screenshot_results VALUES (2, '下午图表', ?, 'success')", (str(afternoon_screenshot),))
    run = {
        "id": 9, "report_type": "weekly", "period_start": "2026-10-10 00:00:00",
        "period_end": "2026-10-10 23:59:59", "status": "running",
        "mail_status": "", "error_summary": "",
    }
    setting = {
        "name": "周报", "send_empty_report": 0, "include_docx": include_docx,
        "include_screenshots": 1, "enable_email": 0, "dingtalk_enabled": 0,
    }
    monkeypatch.setattr(periodic_reporter, "connect", lambda: conn)
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_run", lambda _id: dict(run))
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_setting", lambda _type: setting)
    monkeypatch.setattr(periodic_reporter, "save_periodic_report_run", lambda data, _id: run.update(data))
    monkeypatch.setattr(periodic_reporter.settings, "data_dir", tmp_path)
    monkeypatch.setattr(periodic_reporter.tempfile, "mkdtemp", lambda **_: str(tmp_path / "stage"))
    (tmp_path / "stage").mkdir()

    periodic_reporter.execute_periodic_report_flow(9)

    assert run["status"] == "success"
    with zipfile.ZipFile(run["zip_path"]) as archive:
        names = archive.namelist()
    assert any(name.endswith("morning-chart.png") for name in names)
    assert any(name.endswith("afternoon-chart.png") for name in names)
    assert any(name.endswith("daily.docx") for name in names) is bool(include_docx)


def test_periodic_record_is_kept_when_archive_cannot_be_removed(monkeypatch, tmp_path):
    from pathlib import Path
    from fastapi import HTTPException
    from app import main, repository

    archive = tmp_path / "report.zip"
    archive.write_bytes(b"zip")
    deleted = []
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(repository, "get_periodic_report_run", lambda _id: {
        "id": 7, "status": "success", "zip_path": str(archive),
    })
    monkeypatch.setattr(repository, "delete_periodic_report_run", lambda run_id: deleted.append(run_id))
    original_unlink = Path.unlink

    def locked_unlink(path, *args, **kwargs):
        if path == archive:
            raise PermissionError("locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    with pytest.raises(HTTPException) as error:
        main._remove_periodic_report_run(7)
    assert error.value.status_code == 500
    assert archive.exists()
    assert deleted == []


def test_running_periodic_report_cannot_be_deleted(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from app import main, repository

    deleted = []
    monkeypatch.setattr(repository, "get_periodic_report_run", lambda _id: {
        "id": 8, "status": "running", "zip_path": "",
    })
    monkeypatch.setattr(repository, "delete_periodic_report_run", lambda run_id: deleted.append(run_id))
    with pytest.raises(HTTPException) as error:
        main._remove_periodic_report_run(8)
    assert error.value.status_code == 409
    assert deleted == []
