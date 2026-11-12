import sqlite3
import sys
import types

import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response


if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import db, holiday_calendar, main


def test_system_preferences_are_saved_and_used_by_holiday_refresh(monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(main, "get_system_setting", lambda key, default="":
                        (conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone() or [default])[0])
    monkeypatch.setattr(holiday_calendar, "get_system_setting", main.get_system_setting)
    monkeypatch.setattr(main, "public_base_path", lambda: "/ops")
    monkeypatch.setattr(main, "cookie_path", lambda: "/ops")
    monkeypatch.setattr(main, "admin_username", lambda: "admin")
    monkeypatch.setattr(holiday_calendar.settings, "data_dir", tmp_path)
    monkeypatch.setattr(holiday_calendar, "_years", {})
    old_title = main.app.title
    primary = "http://intranet.example/holidays/{year}"
    backup = "http://backup.example/holidays/{year}"
    try:
        result = main.api_save_system_preferences(
            {"app_name": "巡检中心", "holiday_primary_url": primary, "holiday_backup_url": backup},
            BackgroundTasks(),
            Request({"type": "http", "method": "PUT", "scheme": "http", "path": "/ops/api/system-preferences",
                     "query_string": b"", "headers": [], "server": ("testserver", 80)}),
            Response(),
        )
        assert result["app_name"] == main.display_name() == "巡检中心"
        assert main.app.title == "巡检中心"
        requested = []
        monkeypatch.setattr(holiday_calendar, "_fetch_json", lambda url: requested.append(url) or {})
        monkeypatch.setattr(holiday_calendar, "_backup_days", lambda _year, _data: {"2026-01-01": True})
        assert holiday_calendar.refresh_year(2026)
        assert requested == ["http://intranet.example/holidays/2026"]
    finally:
        main.app.title = old_title
        conn.close()


@pytest.mark.parametrize("value", [
    "ftp://example.com/{year}",
    "https://example.com/calendar",
    "https://user:pass@example.com/{year}",
    "https://[bad/{year}",
    "https://example.com/{year}/{month}",
])
def test_holiday_url_validation_rejects_unsafe_or_invalid_values(value):
    with pytest.raises(HTTPException):
        main.validate_holiday_url(value)
