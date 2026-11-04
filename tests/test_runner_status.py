from contextlib import nullcontext
import sqlite3
import sys
from types import SimpleNamespace
import types

import pytest

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import runner


@pytest.mark.parametrize(
    ("capture_status", "expected_status"),
    [("success", "success"), ("failed", "failed")],
)
def test_run_result_is_preserved_when_notification_fails(monkeypatch, capture_status, expected_status):
    job = {"id": 7, "name": "巡检"}
    item = {"id": 8, "name": "图表", "section": "资源", "enabled": 1, "real_browser_capture": 0}
    run = {"id": 9, "started_at": "2026-10-10T09:00:00+08:00", "status": "running"}
    results = []

    monkeypatch.setattr(runner, "get_job", lambda _: job)
    monkeypatch.setattr(runner, "list_screenshot_items", lambda _: [item])
    monkeypatch.setattr(runner, "get_run", lambda _: dict(run))
    monkeypatch.setattr(runner, "update_run", lambda _id, **fields: run.update(fields))
    monkeypatch.setattr(runner, "add_screenshot_result", lambda result: results.append(result))
    monkeypatch.setattr(runner, "list_screenshot_results", lambda _: list(results))
    monkeypatch.setattr(runner, "generate_docx", lambda *_: "report.docx")
    monkeypatch.setattr(runner, "sync_playwright", nullcontext)
    monkeypatch.setattr(runner.BrowserSession, "open", lambda *_: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        runner,
        "capture_item",
        lambda *_args, **_kwargs: SimpleNamespace(
            status=capture_status,
            file_path="chart.png" if capture_status == "success" else "",
            error_message="页面打不开" if capture_status == "failed" else "",
            duration_seconds=1.0,
            content_status="data" if capture_status == "success" else "not_applicable",
            content_summary="1 个图表已显示" if capture_status == "success" else "",
            monitor_data=None,
        ),
    )

    def fail_notification(*_args, **_kwargs):
        assert run["status"] == "running"
        assert not run.get("finished_at")
        raise RuntimeError("通知接口超时")

    monkeypatch.setattr(runner, "_send_dingtalk_notification", fail_notification)
    assert runner.run_job(7, 9) == 9
    assert run["status"] == expected_status
    assert run["success_count"] == (1 if capture_status == "success" else 0)
    assert run["failed_count"] == (0 if capture_status == "success" else 1)
    assert run["dingtalk_status"].startswith("failed:")
    if capture_status == "failed":
        assert "全部 1 个截图项失败" in run["error_summary"]


def test_single_item_unexpected_error_does_not_leave_running_record(monkeypatch, tmp_path):
    item = {"id": 8, "job_id": 7, "name": "图表", "section": "资源"}
    run = {"status": "running"}
    monkeypatch.setattr(runner.settings, "data_dir", tmp_path)
    monkeypatch.setattr("app.repository.get_screenshot_item", lambda _: item)
    monkeypatch.setattr(runner, "get_job", lambda _: {"id": 7, "name": "巡检"})
    monkeypatch.setattr(runner, "create_run", lambda *_: (_ for _ in ()).throw(AssertionError("已有排队记录不应再创建")))
    monkeypatch.setattr(runner, "start_queued_run", lambda _: True)
    monkeypatch.setattr(runner, "update_run", lambda _id, **fields: run.update(fields))

    def fail_capture(*_args, **_kwargs):
        raise RuntimeError("浏览器崩溃")

    monkeypatch.setattr(runner, "capture_item", fail_capture)
    assert runner.test_capture_item(8, 9) == 9
    assert run["status"] == "failed"
    assert run["failed_count"] == 1
    assert "浏览器崩溃" in run["error_summary"]
    assert run["finished_at"]
