from datetime import datetime
import sqlite3
import sys
import types

import pytest

from app.watermark import DEFAULT_TIME_FORMAT, TIME_FORMATS, format_watermark_time, validate_time_format, watermark_options


def test_watermark_time_formats_and_default():
    moment = datetime(2026, 10, 15, 14, 30, 5)
    assert DEFAULT_TIME_FORMAT == "datetime_seconds"
    assert [format_watermark_time(moment, option["id"]) for option in TIME_FORMATS] == [
        "2026-10-15 14:30:05",
        "2026-10-15 14:30",
        "2026/10/15 14:30:05",
        "2026年10月15日 14:30:05",
    ]
    assert all("strftime" not in option for option in watermark_options())
    with pytest.raises(ValueError, match="水印时间格式无效"):
        validate_time_format("%Q")


def test_configured_watermark_combines_time_and_custom_text(monkeypatch, tmp_path):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import screenshot

    rendered = []
    monkeypatch.setattr(screenshot, "_apply_watermark", lambda **kwargs: rendered.append(kwargs["text"]))
    image_path = tmp_path / "capture.png"
    item = {
        "watermark_enabled": 1,
        "watermark_time_enabled": 1,
        "watermark_time_format": "datetime_seconds",
        "watermark_text": "部门名称",
    }
    screenshot._apply_configured_watermark(image_path, item)
    assert len(rendered) == 1
    assert rendered[0].endswith("\n部门名称")
    assert "{time}" not in rendered[0]

    item["watermark_time_enabled"] = 0
    screenshot._apply_configured_watermark(image_path, item)
    assert rendered[-1] == "部门名称"

    item["watermark_text"] = ""
    screenshot._apply_configured_watermark(image_path, item)
    assert len(rendered) == 2


def test_existing_watermark_placeholders_migrate_once(monkeypatch, tmp_path):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import db

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE screenshot_items (id INTEGER PRIMARY KEY, watermark_enabled INTEGER, watermark_text TEXT)")
        conn.executemany(
            "INSERT INTO screenshot_items (id, watermark_enabled, watermark_text) VALUES (?, ?, ?)",
            [(1, 1, "{time}\n部门名称"), (2, 1, "仅自定义文字"), (3, 1, "{time}"), (4, 1, "")],
        )
    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "db_path", db_path)
    monkeypatch.setattr(db, "_migrate_plain_database", lambda: None)
    db.init_db()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT watermark_time_enabled, watermark_time_format, watermark_text FROM screenshot_items ORDER BY id"
        ).fetchall()
    assert rows == [
        (1, "datetime_seconds", "部门名称"),
        (0, "datetime_seconds", "仅自定义文字"),
        (1, "datetime_seconds", ""),
        (1, "datetime_seconds", "省客户服务中心\ndwangchengyi7(王诚毅)"),
    ]
    db.init_db()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT watermark_text FROM screenshot_items WHERE id = 1").fetchone()[0] == "部门名称"
