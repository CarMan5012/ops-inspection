import json
import sqlite3
import sys
import types
from datetime import datetime, timedelta
from threading import Event, Thread
from zoneinfo import ZoneInfo

import pytest
from docx import Document
from fastapi import BackgroundTasks, HTTPException
from PIL import Image

if "pysqlcipher3" not in sys.modules:
    stub = types.ModuleType("pysqlcipher3")
    stub.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = stub

from app import job_execution, main, periodic_reporter, report, repository, runner
from app.date_schedule import resolved_time


@pytest.mark.parametrize("afternoon_complete", [True, False])
def test_monthly_completion_uses_resolved_random_slots(monkeypatch, afternoon_complete):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER, status TEXT, started_at TEXT, schedule_slot TEXT)")
    job = {"id": 42, "name": "随机分钟巡检", "enabled": 1, "schedule_mode": "calendar",
           "schedule_config": json.dumps({"frequency": "daily", "times": ["09:R", "17:R"]})}
    now = datetime.now(ZoneInfo(periodic_reporter.settings.default_timezone)).replace(hour=23, minute=59)
    for slot in ["09:R", "17:R"] if afternoon_complete else ["09:R"]:
        started = datetime.combine(now.date(), resolved_time(slot, now.date(), 42), tzinfo=now.tzinfo)
        conn.execute("INSERT INTO run_records(job_id,status,started_at,schedule_slot) VALUES (42,'success',?,?)",
                     ((started + timedelta(seconds=1)).isoformat(), f"calendar-{slot.replace(':', '')}"))
    monkeypatch.setattr(periodic_reporter, "connect", lambda: conn)
    monkeypatch.setattr(periodic_reporter, "list_jobs", lambda: [job])
    completed, reasons = periodic_reporter.check_today_jobs_completed(now)
    assert completed is afternoon_complete
    if not afternoon_complete:
        assert any("calendar-17R" in reason for reason in reasons)
    conn.close()


def test_custom_morning_is_preserved_when_afternoon_uses_template(tmp_path, monkeypatch):
    previous = []
    snapshots = {}
    monkeypatch.setattr(report, "get_report_dir", lambda _: tmp_path)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_: list(reversed(previous)))
    monkeypatch.setattr(repository, "list_screenshot_results", lambda rid: snapshots[rid])
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_: previous[-1] if previous else None)
    image = tmp_path / "morning.png"
    Image.new("RGB", (120, 90), "blue").save(image)
    job = {"id": 7, "name": "生产巡检", "report_title": "生产日报"}
    morning = {"id": 201, "started_at": "2026-10-10T09:05:00+08:00"}
    first = {"id": 11, "item_name": "上午业务看板", "section": "自定义业务章节", "status": "success", "file_path": str(image)}
    path = report.generate_docx(job, morning, [first])
    snapshots[201] = [first]
    previous.append({**morning, "report_path": path})
    afternoon = {"id": 202, "started_at": "2026-10-10T17:05:00+08:00"}
    second = {"id": 12, "item_name": "下午看板", "section": "服务器资源",
              "status": "failed", "error_message": "Grafana 返回 502", "file_path": ""}
    assert report.generate_docx(job, afternoon, [second]) == path
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "自定义业务章节" in text and "上午业务看板" in text
    assert "截图失败：下午看板。原因：Grafana 返回 502" in text
    assert len(doc.inline_shapes) == 1


def test_empty_job_sends_failure_notification_and_keeps_failed_status(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    job = {"id": 7, "name": "巡检", "send_mail_on_error": 0,
           "dingtalk_enabled": 1, "send_dingtalk_on_error": 1}
    record = {"id": 9}
    notices = []
    monkeypatch.setattr(runner, "get_job", lambda _: job)
    monkeypatch.setattr(runner, "list_screenshot_items", lambda _: [])
    monkeypatch.setattr(runner, "get_run", lambda _: dict(record))
    monkeypatch.setattr(runner, "update_run", lambda _, **fields: record.update(fields))
    monkeypatch.setattr(runner, "generate_docx", lambda *_: "report.docx")
    monkeypatch.setattr(runner, "_send_dingtalk_notification", lambda *_args, **kwargs: notices.append(kwargs))
    assert runner.run_job(7, 9) == 9
    assert record["status"] == "failed" and record["finished_at"]
    assert len(notices) == 1 and notices[0]["status"] == "failed"
    assert "无可执行" in notices[0]["error_summary"]


def test_same_job_lock_rejects_duplicate_and_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    lease = job_execution.acquire_job_lease(1)
    try:
        with pytest.raises(job_execution.JobAlreadyRunning):
            job_execution.acquire_job_lease(1)
        other = job_execution.acquire_job_lease(2)
        other.close()
    finally:
        lease.close()
    replacement = job_execution.acquire_job_lease(1)
    replacement.close()


def test_duplicate_api_request_creates_only_one_run(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    created = []
    monkeypatch.setattr(main, "get_job", lambda _: {"id": 1, "name": "巡检"})
    monkeypatch.setattr(main, "create_run", lambda *_: created.append(100) or 100)
    queue = BackgroundTasks()
    try:
        assert main.api_run_job(1, queue)["run_id"] == 100
        with pytest.raises(HTTPException) as exc:
            main.api_run_job(1, queue)
        assert exc.value.status_code == 409
        assert len(created) == len(queue.tasks) == 1
    finally:
        for task in queue.tasks:
            task.kwargs["execution_lease"].close()


def test_failed_submission_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main, "create_run", lambda *_: (_ for _ in ()).throw(RuntimeError("数据库失败")))
    with pytest.raises(RuntimeError):
        main.queue_inspection_job({"id": 1, "name": "巡检"}, BackgroundTasks())
    lease = job_execution.acquire_job_lease(1)
    lease.close()


def test_runner_exception_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    monkeypatch.setattr(runner, "_execute_job", lambda *_: (_ for _ in ()).throw(RuntimeError("报告失败")))
    with pytest.raises(RuntimeError):
        runner.run_job(1, 100)
    lease = job_execution.acquire_job_lease(1)
    lease.close()


def test_scheduled_run_waits_for_manual_run(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    called = Event()
    entered = Event()
    monkeypatch.setattr(runner, "_execute_job", lambda *_: called.set() or 101)
    manual = job_execution.acquire_job_lease(1)
    def scheduled():
        entered.set()
        runner.run_job(1)
    thread = Thread(target=scheduled, daemon=True)
    thread.start()
    try:
        assert entered.wait(2)
        assert not called.wait(.1)
    finally:
        manual.close()
        thread.join(3)
    assert not thread.is_alive() and called.is_set()
