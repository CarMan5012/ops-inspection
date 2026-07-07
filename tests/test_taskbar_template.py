from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from app import screenshot


class TaskbarTemplateTests(unittest.TestCase):
    def test_fit_taskbar_template_scales_with_width(self) -> None:
        template = Image.new("RGBA", (3840, 72), (240, 246, 252, 255))

        fitted = screenshot._fit_taskbar_template(template, 1920)

        self.assertEqual(fitted.size, (1920, 36))

    def test_format_taskbar_clock_matches_windows_template(self) -> None:
        now = datetime(2026, 7, 7, 13, 57)

        self.assertEqual(screenshot._format_taskbar_clock(now), ("13:57", "2026/7/7"))

    def test_taskbar_clock_font_prefers_configured_windows_font(self) -> None:
        with patch.dict("os.environ", {"TASKBAR_CLOCK_FONT_PATH": r"C:\Windows\Fonts\msyh.ttc"}):
            paths = screenshot._taskbar_font_paths()

        self.assertEqual(str(paths[0]), r"C:\Windows\Fonts\msyh.ttc")
        self.assertTrue(any(str(path).endswith("msyh.ttc") for path in paths[:4]))

    def test_item_form_only_exposes_taskbar_enabled_toggle(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "item_form.html").read_text(encoding="utf-8")

        self.assertIn('name="taskbar_enabled"', html)
        self.assertNotIn('name="taskbar_style"', html)
        self.assertNotIn('name="taskbar_mode"', html)

    def test_taskbar_always_appends_even_with_legacy_overlay_value(self) -> None:
        with TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "shot.png"
            Image.new("RGB", (384, 100), (20, 24, 28)).save(image_path)

            screenshot._apply_simulated_taskbar(
                image_path,
                {"taskbar_enabled": 1, "taskbar_mode": "overlay"},
            )

            with Image.open(image_path) as result:
                self.assertGreater(result.height, 100)

    def test_trim_mss_capture_removes_left_top_black_desktop_margin(self) -> None:
        with TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "mss.png"
            img = Image.new("RGB", (120, 80), (0, 0, 0))
            for y in range(10, 80):
                for x in range(10, 120):
                    img.putpixel((x, y), (240, 240, 240))
            img.save(image_path)

            screenshot._trim_mss_desktop_margin(image_path)

            with Image.open(image_path) as result:
                self.assertEqual(result.size, (110, 70))
                self.assertEqual(result.getpixel((0, 0)), (240, 240, 240))

    def test_clock_redraw_preserves_template_border(self) -> None:
        taskbar = Image.new("RGBA", (3721, 72), (235, 244, 252, 255))
        border = (121, 132, 143, 255)
        for x in range(taskbar.width):
            taskbar.putpixel((x, 0), border)
            taskbar.putpixel((x, taskbar.height - 1), border)

        screenshot._draw_taskbar_clock(taskbar, datetime(2026, 7, 7, 14, 25))

        self.assertEqual(taskbar.getpixel((taskbar.width - 20, 0)), border)
        self.assertEqual(taskbar.getpixel((taskbar.width - 20, taskbar.height - 1)), border)

    def test_clock_redraw_does_not_flatten_clock_area(self) -> None:
        taskbar = Image.new("RGBA", (3721, 72), (235, 244, 252, 255))
        marker = (10, 20, 30, 255)
        taskbar.putpixel((taskbar.width - 145, taskbar.height // 2), marker)

        screenshot._draw_taskbar_clock(taskbar, datetime(2026, 7, 7, 14, 25))

        self.assertEqual(taskbar.getpixel((taskbar.width - 145, taskbar.height // 2)), marker)

    def test_clock_redraw_only_adds_text_without_erasing_blank_area(self) -> None:
        taskbar = Image.new("RGBA", (3620, 72), (235, 244, 252, 255))
        marker = (10, 20, 30, 255)
        clock_width = max(round(taskbar.height * 1.9), round(taskbar.width * 0.04))
        x = taskbar.width - clock_width + 2
        y = max(2, round(taskbar.height * 0.12)) + 10
        taskbar.putpixel((x, y), marker)

        screenshot._draw_taskbar_clock(taskbar, datetime(2026, 7, 7, 14, 25))

        self.assertEqual(taskbar.getpixel((x, y)), marker)


if __name__ == "__main__":
    unittest.main()
