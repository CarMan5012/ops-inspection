import sqlite3
import sys
import types

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import screenshot
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def test_total_timeout_stops_retries(monkeypatch):
    calls = []
    clock = {"now": screenshot.time.monotonic()}

    def capture_once(_item, _job, _run_id, **kwargs):
        calls.append(kwargs["total_deadline"])
        clock["now"] += 241
        raise TimeoutError("页面没有完成")

    monkeypatch.setattr(screenshot, "_capture_once", capture_once)
    monkeypatch.setattr(screenshot.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(screenshot.time, "monotonic", lambda: clock["now"])

    result = screenshot.capture_item(
        {"name": "测试", "retry_count": 2, "total_timeout_seconds": 240}, {}, 1,
    )
    assert result.status == "failed"
    assert "总耗时已达 240 秒" in result.error_message
    assert "最后阶段：页面没有完成" in result.error_message
    assert len(calls) == 1


def test_browser_disconnect_restarts_before_retry(monkeypatch):
    class BrowserStub:
        def __init__(self):
            self.connected = True
            self.closed = False

        def is_connected(self):
            return self.connected

        def close(self):
            self.closed = True

    original = BrowserStub()
    replacement = BrowserStub()
    session = screenshot.BrowserSession(original, lambda: replacement)
    attempts = []

    def capture_once(_item, _job, _run_id, **kwargs):
        browser = kwargs["browser"]
        attempts.append(browser)
        if len(attempts) == 1:
            browser.connected = False
            raise RuntimeError("准备浏览器失败：browser disconnected")
        return screenshot.CaptureResult(status="success", file_path="ok.png")

    monkeypatch.setattr(screenshot, "_capture_once", capture_once)
    monkeypatch.setattr(screenshot.time, "sleep", lambda _seconds: None)
    result = screenshot.capture_item(
        {"name": "测试", "retry_count": 1, "total_timeout_seconds": 240}, {}, 1,
        browser_session=session,
    )
    assert result.status == "success"
    assert attempts == [original, replacement]
    assert original.closed
    assert session.browser is replacement


def test_page_failure_reuses_browser_with_new_attempt(monkeypatch):
    class BrowserStub:
        def is_connected(self):
            return True

        def close(self):
            raise AssertionError("page failure should not restart browser")

    browser = BrowserStub()
    session = screenshot.BrowserSession(browser, lambda: None)
    attempts = []

    def capture_once(_item, _job, _run_id, **kwargs):
        attempts.append(kwargs["browser"])
        if len(attempts) == 1:
            raise RuntimeError("等待页面失败：图表尚未出现")
        return screenshot.CaptureResult(status="success", file_path="ok.png")

    monkeypatch.setattr(screenshot, "_capture_once", capture_once)
    monkeypatch.setattr(screenshot.time, "sleep", lambda _seconds: None)
    result = screenshot.capture_item(
        {"name": "测试", "retry_count": 1, "total_timeout_seconds": 240}, {}, 1,
        browser_session=session,
    )
    assert result.status == "success"
    assert attempts == [browser, browser]


def test_stalled_login_restarts_connected_browser():
    class BrowserStub:
        def is_connected(self):
            return True

    try:
        raise RuntimeError("登录失败：提交按钮卡住") from PlaywrightTimeoutError("timeout")
    except RuntimeError as exc:
        assert screenshot._browser_needs_restart(exc, BrowserStub())


def test_total_timeout_column_is_available_for_existing_database(monkeypatch, tmp_path):
    from app import db

    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "test.db")
    monkeypatch.setattr(db, "_migrate_plain_database", lambda: None)
    db.init_db()
    with db.connect() as conn:
        conn.execute("ALTER TABLE screenshot_items DROP COLUMN total_timeout_seconds")
    db.init_db()
    with db.connect() as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(screenshot_items)")}
    assert columns["total_timeout_seconds"][4] == "240"
    with db.connect() as conn:
        result_columns = {row[1] for row in conn.execute("PRAGMA table_info(screenshot_results)")}
    assert {"content_status", "content_summary"} <= result_columns
