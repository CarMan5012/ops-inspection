from datetime import datetime
from io import BytesIO
import re
import sqlite3
import sys
import types

import pytest

from app.watermark import DEFAULT_TIME_FORMAT, TIME_FORMATS, WATERMARK_DEFAULTS, format_watermark_time, validate_time_format, validate_watermark_alignment, watermark_options


def test_watermark_time_formats_and_default():
    moment = datetime(2026, 10, 15, 14, 30, 5)
    assert DEFAULT_TIME_FORMAT == "datetime_seconds"
    assert WATERMARK_DEFAULTS["watermark_enabled"] == 1
    assert WATERMARK_DEFAULTS["watermark_text"] == "王"
    assert WATERMARK_DEFAULTS["watermark_opacity"] == 20
    assert WATERMARK_DEFAULTS["watermark_angle"] == 36
    assert WATERMARK_DEFAULTS["watermark_alignment"] == "reference"
    assert [format_watermark_time(moment, option["id"]) for option in TIME_FORMATS] == [
        "2026-10-15 14:30:05",
        "2026-10-15 14:30",
        "2026/10/15 14:30:05",
        "2026年10月15日 14:30:05",
    ]
    assert all("strftime" not in option for option in watermark_options())
    with pytest.raises(ValueError, match="水印时间格式无效"):
        validate_time_format("%Q")
    with pytest.raises(ValueError, match="水印文字对齐方式无效"):
        validate_watermark_alignment("justify")


def test_save_and_preview_reject_oversized_watermark_text():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from fastapi import HTTPException
    from app import main

    long_text = "王" * 201
    with pytest.raises(HTTPException, match="水印文字不能超过 200 字"):
        main.api_item_payload({"watermark_text": long_text}, 1)
    with pytest.raises(HTTPException, match="预览文字不能超过 200 字"):
        main.api_watermark_preview({"watermark_text": long_text})


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
    assert rendered[0].startswith("部门名称\n")
    assert "{time}" not in rendered[0]

    item["watermark_time_enabled"] = 0
    screenshot._apply_configured_watermark(image_path, item)
    assert rendered[-1] == "部门名称"

    item["watermark_text"] = ""
    screenshot._apply_configured_watermark(image_path, item)
    assert len(rendered) == 2


def test_default_watermark_renders_surname_above_timestamp(monkeypatch, tmp_path):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import screenshot

    rendered = []
    monkeypatch.setattr(screenshot, "_apply_watermark", lambda **kwargs: rendered.append(kwargs))
    screenshot._apply_configured_watermark(tmp_path / "capture.png", WATERMARK_DEFAULTS)

    assert len(rendered) == 1
    assert rendered[0]["text"].startswith("王\n")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", rendered[0]["text"].splitlines()[1])
    assert rendered[0]["angle"] == 36
    assert rendered[0]["font_size"] == 53
    assert rendered[0]["alignment"] == "reference"
    assert rendered[0]["time_line_index"] == 1


def test_old_surname_preset_is_migrated_without_changing_custom_layout(monkeypatch, tmp_path):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import db

    db_path = tmp_path / "old-layout.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE screenshot_items (
                id INTEGER PRIMARY KEY, watermark_enabled INTEGER,
                watermark_time_enabled INTEGER, watermark_text TEXT,
                watermark_font_size INTEGER, watermark_gap_x INTEGER,
                watermark_gap_y INTEGER, watermark_angle INTEGER,
                watermark_alignment TEXT, watermark_opacity INTEGER
            )
        """)
        conn.executemany(
            "INSERT INTO screenshot_items VALUES (?, 1, 1, '王', ?, ?, ?, ?, ?, ?)",
            [
                (1, 24, 140, 140, -45, "center", 65),
                (2, 30, 140, 140, -45, "left", 65),
                (3, 52, 500, 300, 45, "right", 52),
                (4, 53, 234, 594, 36, "reference", 26),
            ],
        )
    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "db_path", db_path)
    monkeypatch.setattr(db, "_migrate_plain_database", lambda: None)
    db.init_db()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("""
            SELECT watermark_font_size, watermark_gap_x, watermark_gap_y,
                   watermark_angle, watermark_alignment, watermark_opacity
            FROM screenshot_items ORDER BY id
        """).fetchall()
    assert rows == [
        (53, 234, 594, 36, "reference", 20),
        (30, 140, 140, -45, "left", 65),
        (53, 234, 594, 36, "reference", 20),
        (53, 234, 594, 36, "reference", 20),
    ]

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE screenshot_items SET watermark_opacity = 26 WHERE id = 4")
    db.init_db()
    with sqlite3.connect(db_path) as conn:
        opacity = conn.execute("SELECT watermark_opacity FROM screenshot_items WHERE id = 4").fetchone()[0]
    assert opacity == 26


def test_watermark_line_alignment_is_stable(monkeypatch, tmp_path):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from PIL import Image, ImageDraw
    from app import screenshot

    positions = {}
    original_text = ImageDraw.ImageDraw.text
    calls = []

    def capture_text(draw, xy, value, *args, **kwargs):
        calls.append((value, xy, kwargs["font"].size, kwargs.get("stroke_width", 0)))
        return original_text(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    for alignment in ("left", "center", "right"):
        path = tmp_path / f"{alignment}.png"
        Image.new("RGB", (800, 400), "white").save(path)
        calls.clear()
        screenshot._apply_watermark(path, "王\n2026-10-26 08:48:13", 65, 52, 500, 300, 45, alignment, time_line_index=1)
        assert [call[0] for call in calls] == ["王", "2026-10-26 08:48:13"]
        assert calls[0][2] == 52
        assert calls[1][2] > calls[0][2] or calls[1][3] > 0
        positions[alignment] = calls[0][1][0] - calls[1][1][0]
    assert positions["left"] < positions["center"] < positions["right"]


def test_preview_uses_capture_aspect_ratio_and_watermark_renderer():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from PIL import Image
    from app import screenshot

    for capture_size, expected_size in [((3840, 2160), (1200, 675)), ((1898, 2058), (1200, 1301))]:
        png = screenshot.render_watermark_preview_png(WATERMARK_DEFAULTS, *capture_size)
        with Image.open(BytesIO(png)) as preview:
            assert preview.size == expected_size
            assert preview.getchannel("A").getextrema()[1] > 0


def test_large_preview_renders_at_display_size_without_full_resolution_canvas(monkeypatch):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from PIL import Image
    from app import screenshot

    allocations = []
    original_new = Image.new

    def record_new(mode, size, *args, **kwargs):
        allocations.append(size)
        return original_new(mode, size, *args, **kwargs)

    monkeypatch.setattr(Image, "new", record_new)
    png = screenshot.render_watermark_preview_png(WATERMARK_DEFAULTS, 7680, 8640)
    with Image.open(BytesIO(png)) as preview:
        assert preview.size == (1200, 1350)
        assert preview.getchannel("A").getextrema()[1] > 0
    assert all(width * height < 10_000_000 for width, height in allocations)


def test_default_watermark_tiles_follow_full_capture_geometry():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from PIL import Image
    from app import screenshot

    image = Image.new("RGBA", (3840, 2088), "white")
    rendered = screenshot._render_watermark(
        image, "王\n2026-10-26 08:48:13",
        WATERMARK_DEFAULTS["watermark_opacity"],
        WATERMARK_DEFAULTS["watermark_font_size"],
        WATERMARK_DEFAULTS["watermark_gap_x"],
        WATERMARK_DEFAULTS["watermark_gap_y"],
        WATERMARK_DEFAULTS["watermark_angle"],
        WATERMARK_DEFAULTS["watermark_alignment"],
        time_line_index=1,
    )
    channel = rendered.convert("RGB").getchannel("R")
    mask = channel.point(lambda value: 255 if value <= 250 else 0)
    for index, expected_x in enumerate((231, 1191, 2151, 3111)):
        cell = mask.crop((index * 960, 1250, (index + 1) * 960, 1900))
        box = cell.getbbox()
        assert box is not None
        assert abs(index * 960 + box[0] - expected_x) <= 5
        assert abs(1250 + box[1] - 1392) <= 5
        assert abs(1250 + box[3] - 1810) <= 5
    assert channel.getextrema()[0] == 247


def test_reference_angle_responds_to_window_size():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import screenshot

    for size, measured_angle in [
        ((3840, 2088), 36.19), ((1898, 2058), 42.27),
        ((2489, 1470), 37.20), ((2489, 2043), 41.21),
    ]:
        assert screenshot._reference_metric(*size, "angle") == pytest.approx(measured_angle, abs=0.01)


def test_reference_watermark_stays_readable_on_small_windows():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import screenshot

    assert 130 <= screenshot._reference_metric(800, 600, "date_width") <= 200
    assert 30 <= screenshot._reference_metric(800, 600, "angle") <= 45
    assert screenshot._reference_metric(1898, 2058, "date_width") == 474
    assert 30 <= screenshot._reference_metric(7680, 8640, "angle") <= 45


def test_reference_upper_surnames_follow_left_center_right_pattern(monkeypatch):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from PIL import Image
    from app import screenshot

    placements = []
    original_composite = Image.Image.alpha_composite

    def record_composite(image, source, dest=(0, 0), source_offset=(0, 0)):
        placements.append((dest, source.size))
        return original_composite(image, source, dest, source_offset)

    monkeypatch.setattr(Image.Image, "alpha_composite", record_composite)
    screenshot._render_watermark(
        Image.new("RGBA", (2489, 1470), "white"), "王\n2026-10-26 08:48:13",
        WATERMARK_DEFAULTS["watermark_opacity"], WATERMARK_DEFAULTS["watermark_font_size"],
        WATERMARK_DEFAULTS["watermark_gap_x"], WATERMARK_DEFAULTS["watermark_gap_y"],
        WATERMARK_DEFAULTS["watermark_angle"], WATERMARK_DEFAULTS["watermark_alignment"],
        time_line_index=1,
    )
    assert len(placements) == 16

    def center(index):
        (x, y), (width, height) = placements[index]
        return x + width / 2, y + height / 2

    lower_names = [center(column * 4 + 1) for column in range(4)]
    upper_names = [center(column * 4 + 3) for column in range(4)]
    assert [lower_names[i + 1][0] - lower_names[i][0] for i in range(3)] == pytest.approx([622.25] * 3, abs=1)
    shifts = [(upper_names[i][0] - lower_names[i][0], upper_names[i][1] - lower_names[i][1]) for i in range(4)]
    assert shifts[0][0] < -200 and shifts[0][1] > -735
    assert shifts[1] == pytest.approx((0, -735), abs=1)
    assert shifts[2][0] > 200 and shifts[2][1] < -735
    assert shifts[3] == pytest.approx(shifts[2], abs=1)


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
