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


def test_empty_job_sends_dingtalk_failure_notification_and_keeps_failed_status(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    job = {"id": 7, "name": "巡检", "dingtalk_enabled": 1, "send_dingtalk_on_error": 1}
    record = {"id": 9}
    notices = []
    monkeypatch.setattr(runner, "get_job", lambda _: job)
    monkeypatch.setattr(runner, "list_screenshot_items", lambda _: [])
    monkeypatch.setattr(runner, "get_run", lambda _: dict(record))
    monkeypatch.setattr(runner, "update_run", lambda _, **fields: record.update(fields))
    monkeypatch.setattr(runner, "generate_docx", lambda *_: "report.docx")
    def notify(*_args, **kwargs):
        assert record["status"] == "running"
        assert not record.get("finished_at")
        notices.append(kwargs)

    monkeypatch.setattr(runner, "_send_dingtalk_notification", notify)
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


def test_job_cannot_be_deleted_while_its_run_is_active(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    deleted = []
    monkeypatch.setattr(main, "get_job", lambda _: {"id": 1})
    monkeypatch.setattr(main, "delete_job", lambda job_id: deleted.append(job_id))
    monkeypatch.setattr(main, "reload_jobs", lambda: None)

    active_lease = job_execution.acquire_job_lease(1)
    try:
        with pytest.raises(HTTPException) as exc:
            main.api_delete_job(1)
        assert exc.value.status_code == 409
        assert deleted == []
    finally:
        active_lease.close()

    assert main.api_delete_job(1) == {"ok": True}
    assert deleted == [1]


def test_deleting_job_finishes_queued_item_test(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE report_jobs (id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES report_jobs(id) "
        "ON DELETE SET NULL, status TEXT, finished_at TEXT, error_summary TEXT)"
    )
    conn.execute("INSERT INTO report_jobs VALUES (7)")
    conn.execute("INSERT INTO run_records VALUES (1, 7, 'queued', NULL, '')")
    monkeypatch.setattr(repository, "connect", lambda: conn)
    try:
        repository.delete_job(7)
        row = conn.execute("SELECT job_id, status, finished_at, error_summary FROM run_records").fetchone()
        assert row["job_id"] is None
        assert row["status"] == "interrupted"
        assert row["finished_at"]
        assert "任务已删除" in row["error_summary"]
        assert repository.start_queued_run(1) is False
    finally:
        conn.close()


def test_duplicate_api_request_creates_only_one_run(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    created = []
    monkeypatch.setattr(main, "get_job", lambda _: {"id": 1, "name": "巡检"})
    monkeypatch.setattr(main, "create_run", lambda *_, **kwargs: created.append((100, kwargs)) or 100)
    queue = BackgroundTasks()
    try:
        assert main.api_run_job(1, queue)["run_id"] == 100
        with pytest.raises(HTTPException) as exc:
            main.api_run_job(1, queue)
        assert exc.value.status_code == 409
        assert len(created) == len(queue.tasks) == 1
        assert created[0][1]["status"] == "queued"
    finally:
        for task in queue.tasks:
            task.kwargs["execution_lease"].close()


def test_item_test_is_queued_once_and_returns_its_run_id(monkeypatch):
    from app import db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER, job_name TEXT, "
        "started_at TEXT, schedule_slot TEXT, status TEXT)"
    )
    monkeypatch.setattr(repository, "connect", lambda: conn)
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(main, "get_screenshot_item", lambda _: {"id": 8, "job_id": 7, "name": "图表"})
    queue = BackgroundTasks()
    try:
        response = main.api_test_item(8, queue)
        run_id = response["run_id"]
        assert response["ok"] is True
        assert conn.execute("SELECT status FROM run_records WHERE id = ?", (run_id,)).fetchone()[0] == "queued"
        assert queue.tasks[0].args == (8, run_id)
        with pytest.raises(HTTPException) as exc:
            main.api_test_item(8, queue)
        assert exc.value.status_code == 409
        assert len(queue.tasks) == 1
    finally:
        conn.close()


def test_failed_submission_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main, "create_run", lambda *_, **__: (_ for _ in ()).throw(RuntimeError("数据库失败")))
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


def test_runner_startup_error_finishes_queued_record(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    monkeypatch.setattr(runner, "get_job", lambda _: None)
    finished = []
    monkeypatch.setattr(runner, "finish_active_run", lambda *args: finished.append(args) or True)

    with pytest.raises(RuntimeError, match="任务不存在"):
        runner.run_job(1, 42)

    assert finished and finished[0][0:2] == (42, "failed")
    assert "任务不存在" in finished[0][2]
    lease = job_execution.acquire_job_lease(1)
    lease.close()


def test_scheduled_run_finishes_queue_if_job_disappears(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    calls = 0
    finished = []

    def get_job_once(_):
        nonlocal calls
        calls += 1
        return {"id": 1, "name": "巡检"} if calls == 1 else None

    monkeypatch.setattr(runner, "get_job", get_job_once)
    monkeypatch.setattr(runner, "create_run", lambda *_, **kwargs: 42)
    monkeypatch.setattr(runner, "finish_active_run", lambda *args: finished.append(args) or True)

    with pytest.raises(RuntimeError, match="任务不存在"):
        runner.run_job(1, schedule_type="morning")
    assert finished and finished[0][0:2] == (42, "failed")


def test_queued_item_test_finishes_if_item_was_deleted(monkeypatch):
    monkeypatch.setattr("app.repository.get_screenshot_item", lambda _: None)
    finished = []
    monkeypatch.setattr(runner, "finish_active_run", lambda *args: finished.append(args) or True)

    with pytest.raises(RuntimeError, match="截图项不存在"):
        runner.test_capture_item(8, 42)
    assert finished and finished[0][0:2] == (42, "failed")


def test_recovery_preserves_live_job_and_finishes_abandoned_runs(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    started = datetime.now(ZoneInfo(job_execution.settings.default_timezone)).isoformat(timespec="seconds")
    conn.execute("CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER, status TEXT, started_at TEXT, finished_at TEXT, error_summary TEXT)")
    conn.executemany(
        "INSERT INTO run_records VALUES (?, ?, ?, ?, NULL, '')",
        [(1, 1, "queued", started), (2, 2, "running", started), (3, None, "running", started)],
    )
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(repository, "connect", lambda: conn)
    active_lease = job_execution.acquire_job_lease(2)
    try:
        assert job_execution.recover_interrupted_runs() == 0
        assert [row[0] for row in conn.execute("SELECT status FROM run_records ORDER BY id")] == [
            "queued", "running", "running",
        ]
        assert conn.execute("SELECT finished_at FROM run_records WHERE id = 2").fetchone()[0] is None
    finally:
        active_lease.close()
    try:
        assert job_execution.recover_interrupted_runs(include_deleted_jobs=True, queued_grace_seconds=0) == 3
        assert conn.execute("SELECT status FROM run_records WHERE id = 1").fetchone()[0] == "interrupted"
        assert conn.execute("SELECT status FROM run_records WHERE id = 2").fetchone()[0] == "interrupted"
        assert conn.execute("SELECT status FROM run_records WHERE id = 3").fetchone()[0] == "interrupted"
    finally:
        conn.close()


def test_old_queued_run_is_recovered_and_cannot_be_started(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER, status TEXT, started_at TEXT, finished_at TEXT, error_summary TEXT)")
    old = (datetime.now(ZoneInfo(job_execution.settings.default_timezone)) - timedelta(minutes=20)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO run_records VALUES (1, 7, 'queued', ?, NULL, '')", (old,))
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(repository, "connect", lambda: conn)
    try:
        assert job_execution.recover_interrupted_runs() == 1
        assert conn.execute("SELECT status FROM run_records WHERE id = 1").fetchone()[0] == "interrupted"
        assert repository.start_queued_run(1) is False
        assert repository.skip_queued_run(1, "重复") is False
    finally:
        conn.close()


def test_periodic_recovery_finishes_old_orphaned_run_but_spares_recent_one(monkeypatch):
    from app import db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER PRIMARY KEY, job_id INTEGER, status TEXT, started_at TEXT, finished_at TEXT, error_summary TEXT)")
    now = datetime.now(ZoneInfo(job_execution.settings.default_timezone))
    old = (now - timedelta(minutes=20)).isoformat(timespec="seconds")
    recent = now.isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO run_records VALUES (?, NULL, ?, ?, NULL, '')",
        [(1, "queued", old), (2, "running", old), (3, "queued", recent)],
    )
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(repository, "connect", lambda: conn)
    try:
        assert job_execution.recover_interrupted_runs() == 2
        assert [row[0] for row in conn.execute("SELECT status FROM run_records ORDER BY id")] == [
            "interrupted", "interrupted", "queued",
        ]
    finally:
        conn.close()


def test_real_browser_window_wait_has_a_deadline(monkeypatch):
    class BusyLock:
        def acquire(self, *, timeout):
            assert timeout > 0
            return False

        def release(self):
            raise AssertionError("未获得锁时不应释放")

    monkeypatch.setattr(runner, "_real_browser_lock", BusyLock())
    with pytest.raises(TimeoutError, match="独占浏览器窗口"):
        with runner._browser_window_access(True):
            pass


def test_scheduled_run_waits_for_manual_run(tmp_path, monkeypatch):
    monkeypatch.setattr(job_execution.settings, "data_dir", tmp_path)
    called = Event()
    entered = Event()
    queued = []
    monkeypatch.setattr(runner, "get_job", lambda _: {"id": 1, "name": "巡检"})
    monkeypatch.setattr(runner, "create_run", lambda *_, **kwargs: queued.append(kwargs) or 101)
    monkeypatch.setattr(runner, "_execute_job", lambda *_: called.set() or 101)
    manual = job_execution.acquire_job_lease(1)
    def scheduled():
        entered.set()
        runner.run_job(1)
    thread = Thread(target=scheduled, daemon=True)
    thread.start()
    try:
        assert entered.wait(2)
        assert queued == [{"status": "queued"}]
        assert not called.wait(.1)
    finally:
        manual.close()
        thread.join(3)
    assert not thread.is_alive() and called.is_set()
