import sqlite3
import sys
import types
from io import BytesIO

import pytest
from PIL import Image

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import taskbar_templates


def image_bytes(size=(1920, 48)):
    image = Image.new("RGB", size, "#244466")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_backend_lists_defaults_and_uploaded_taskbar_and_resolves_selection(tmp_path, monkeypatch):
    from app import screenshot

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE taskbar_templates (id INTEGER PRIMARY KEY, name TEXT UNIQUE, stored_name TEXT UNIQUE,
            original_name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE screenshot_items (id INTEGER PRIMARY KEY, taskbar_template_key TEXT);
    """)
    monkeypatch.setattr(taskbar_templates, "connect", lambda: db)
    monkeypatch.setattr(taskbar_templates.settings, "data_dir", tmp_path)

    defaults = taskbar_templates.all_templates()
    assert len([row for row in defaults if row["kind"] == "builtin"]) == 4
    uploaded = taskbar_templates.create_uploaded("蓝色底栏", "bar.png", image_bytes())
    assert uploaded["key"] in {row["key"] for row in taskbar_templates.all_templates()}
    assert screenshot._taskbar_template_path(17, uploaded["key"]) == taskbar_templates.selected_path(uploaded["key"])
    assert screenshot.browser_window_size(
        {"taskbar_enabled": 1, "taskbar_template_key": uploaded["key"]},
        {"browser_width": 1200, "browser_height": 800}, 17,
    ) == (1200, 770)
    capture = tmp_path / "capture.png"
    Image.new("RGB", (1200, 770), "red").save(capture)
    screenshot._apply_simulated_taskbar(capture, {"taskbar_enabled": 1, "taskbar_template_key": uploaded["key"]}, 17)
    with Image.open(capture) as result:
        assert result.size == (1200, 800)

    db.execute("INSERT INTO screenshot_items (taskbar_template_key) VALUES (?)", (uploaded["key"],))
    with pytest.raises(ValueError, match="正在使用"):
        taskbar_templates.delete_uploaded(int(uploaded["key"].split(":")[1]))
    db.execute("DELETE FROM screenshot_items")
    path = taskbar_templates.selected_path(uploaded["key"])
    taskbar_templates.delete_uploaded(int(uploaded["key"].split(":")[1]))
    assert not path.exists()
    db.close()


def test_invalid_taskbar_image_is_rejected():
    with pytest.raises(ValueError, match="宽幅横条"):
        taskbar_templates.validate_image(image_bytes((500, 500)))
    with pytest.raises(ValueError, match="无法读取"):
        taskbar_templates.validate_image(b"not an image")


def test_auto_taskbar_is_shared_by_all_items_in_one_run():
    from app import screenshot

    items = [{"taskbar_template_key": ""} for _ in range(5)]
    choices = [screenshot._taskbar_template_path(2468, item["taskbar_template_key"]) for item in items]
    assert len(set(choices)) == 1
    assert screenshot._taskbar_template_path(2468, "builtin:2.png") == taskbar_templates.BUILTIN_DIR / "2.png"
