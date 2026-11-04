"""The frontend configuration must agree with the backend's saved values."""

import sqlite3
import sys
import types

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import main  # noqa: E402
from app.ui_config import CLEANUP_PERIODS, CLEANUP_SCOPES, RUN_PAGE_SIZES  # noqa: E402
from app.watermark import WATERMARK_DEFAULTS  # noqa: E402


def test_ui_config_matches_job_item_and_watermark_defaults():
    config = main.api_ui_config()
    job = main.api_job_payload(dict(config["job_defaults"]))
    item = main.api_item_payload(dict(config["item_defaults"]), 1)

    assert job["browser_width"] == config["job_defaults"]["browser_width"]
    assert job["browser_height"] == config["job_defaults"]["browser_height"]
    assert item["item_type"] == config["item_defaults"]["item_type"]
    assert item["timeout_seconds"] == config["item_defaults"]["timeout_seconds"]
    assert {key: item[key] for key in WATERMARK_DEFAULTS} == WATERMARK_DEFAULTS
    assert config["watermark_defaults"] == WATERMARK_DEFAULTS
    assert config["watermark_rules"]["opacity"]["min"] == 0
    assert config["watermark_rules"]["opacity"]["max"] == 255


def test_ui_choices_match_backend_supported_values():
    config = main.api_ui_config()
    assert tuple(config["run_page_sizes"]) == RUN_PAGE_SIZES
    assert config["run_default_page_size"] in RUN_PAGE_SIZES
    assert config["cleanup_scopes"] == CLEANUP_SCOPES
    assert config["cleanup_periods"] == CLEANUP_PERIODS
