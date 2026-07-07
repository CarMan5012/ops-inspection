from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSPECTION = "\u5de1\u68c0\u622a\u56fe"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class DefaultsContractTests(unittest.TestCase):
    def test_job_defaults_are_4k_150_percent_everywhere(self) -> None:
        db = read("app/db.py")
        main = read("app/main.py")
        spa = read("app/static/frontend/app.js")

        self.assertIn("browser_width INTEGER NOT NULL DEFAULT 3840", db)
        self.assertIn("browser_height INTEGER NOT NULL DEFAULT 2160", db)
        self.assertIn("browser_scale_factor REAL NOT NULL DEFAULT 1.5", db)
        self.assertIn("browser_width: int = Form(3840)", main)
        self.assertIn("browser_height: int = Form(2160)", main)
        self.assertIn('"browser_width": int_value(payload, "browser_width", 3840)', main)
        self.assertIn('"browser_height": int_value(payload, "browser_height", 2160)', main)
        self.assertIn('"browser_scale_factor": float_value(payload, "browser_scale_factor", 1.5)', main)
        self.assertIn('"browser_width", job?.browser_width || 3840', spa)
        self.assertIn('"browser_height", job?.browser_height || 2160', spa)
        self.assertIn('name="browser_scale_factor"', spa)
        self.assertIn('step="0.1"', spa)
        self.assertIn('job?.browser_scale_factor ?? 1.5', spa)

    def test_screenshot_item_defaults_are_consistent(self) -> None:
        db = read("app/db.py")
        main = read("app/main.py")
        item_form = read("app/templates/item_form.html")

        self.assertIn("item_type TEXT NOT NULL DEFAULT 'web'", db)
        self.assertIn(f"section TEXT NOT NULL DEFAULT '{INSPECTION}'", db)
        self.assertIn("capture_mode TEXT NOT NULL DEFAULT 'viewport'", db)
        self.assertIn('item_type: str = Form("web")', main)
        self.assertIn(f'section: str = Form("{INSPECTION}")', main)
        self.assertIn('capture_mode: str = Form("viewport")', main)
        self.assertIn('"item_type": text_value(payload, "item_type", "web")', main)
        self.assertIn(f'"section": text_value(payload, "section", "{INSPECTION}")', main)
        self.assertIn(f"value=\"{{{{ item.section or '{INSPECTION}' }}}}\"", item_form)

    def test_time_range_label_has_no_extra_spaces(self) -> None:
        self.assertNotIn("最近 24 小时", read("app/main.py"))
        self.assertNotIn("最近 24 小时", read("app/static/frontend/app.js"))


if __name__ == "__main__":
    unittest.main()