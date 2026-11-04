"""Retired email settings must not affect report generation or DingTalk."""

import sqlite3
import sys
import types

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import dingtalk, periodic_reporter, repository, runner, utils


def test_old_inspection_email_flags_do_not_change_new_run(monkeypatch):
    job = {
        "id": 7, "name": "巡检", "send_mail_on_error": 1,
        "mail_profile_id": 88, "dingtalk_enabled": 1,
    }
    record = {"id": 9, "status": "queued"}
    notices = []
    monkeypatch.setattr(runner, "get_job", lambda _id: job)
    monkeypatch.setattr(runner, "get_run", lambda _id: dict(record))
    monkeypatch.setattr(runner, "update_run", lambda _id, **fields: record.update(fields))
    monkeypatch.setattr(runner, "list_screenshot_items", lambda _id: [])
    monkeypatch.setattr(runner, "generate_docx", lambda *_args: "report.docx")
    monkeypatch.setattr(runner, "_send_dingtalk_notification", lambda *_args, **fields: notices.append(fields))

    assert runner._execute_job(7, 9, is_scheduled=False) == 9
    assert record["status"] == "failed"
    assert record["report_path"] == "report.docx"
    assert "mail_status" not in record
    assert len(notices) == 1 and notices[0]["status"] == "failed"
    assert "mail_status" not in notices[0]


def test_old_periodic_email_flags_do_not_block_archive_or_dingtalk(monkeypatch, tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE run_records (id INTEGER, job_id INTEGER, job_name TEXT, status TEXT, "
        "report_path TEXT, started_at TEXT, finished_at TEXT, success_count INTEGER, failed_count INTEGER)"
    )
    run = {
        "id": 9, "report_type": "weekly", "period_start": "2026-10-01 00:00:00",
        "period_end": "2026-10-07 23:59:59", "status": "running",
        "mail_status": "", "error_summary": "",
    }
    setting = {
        "name": "周报", "send_empty_report": 1, "include_docx": 0,
        "include_screenshots": 0, "enable_email": 1, "mail_profile_id": 88,
        "dingtalk_enabled": 1, "dingtalk_profile_id": 5,
    }
    stage = tmp_path / "stage"
    stage.mkdir()
    sent_messages = []
    monkeypatch.setattr(periodic_reporter, "connect", lambda: connection)
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_run", lambda _id: dict(run))
    monkeypatch.setattr(periodic_reporter, "get_periodic_report_setting", lambda _type: setting)
    monkeypatch.setattr(periodic_reporter, "save_periodic_report_run", lambda data, _id: run.update(data))
    monkeypatch.setattr(periodic_reporter.settings, "data_dir", tmp_path)
    monkeypatch.setattr(periodic_reporter.tempfile, "mkdtemp", lambda **_kwargs: str(stage))
    monkeypatch.setattr(repository, "get_dingtalk_profile", lambda _id: {
        "webhook": "https://example.test/robot", "secret": "", "keyword": "", "emoji_enabled": 0,
    })
    monkeypatch.setattr(utils, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(dingtalk, "send_dingtalk_msg", lambda *_args: sent_messages.append(_args) or "sent")

    try:
        periodic_reporter.execute_periodic_report_flow(9)
    finally:
        connection.close()

    assert run["status"] == "success"
    assert run["mail_status"] == ""
    assert run["dingtalk_status"] == "sent"
    assert run["zip_path"]
    assert len(sent_messages) == 1
    assert "邮件状态" not in sent_messages[0][4]
