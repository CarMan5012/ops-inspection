from __future__ import annotations

import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "自动化巡检报告系统")
        self.secret_key = os.getenv("APP_SECRET_KEY", "change-me")
        self.admin_username = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_password = os.getenv("ADMIN_PASSWORD", "admin")

        self.data_dir = Path(os.getenv("APP_DATA_DIR", "data")).resolve()
        self.db_path = self.data_dir / "app.db"
        self.screenshot_dir = self.data_dir / "screenshots"
        self.report_dir = self.data_dir / "reports"
        self.log_dir = self.data_dir / "logs"
        self.browser_state_dir = self.data_dir / "browser-state"

        self.default_timezone = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
        self.max_recent_runs = int(os.getenv("APP_MAX_RECENT_RUNS", "50"))

        # 对 api_prefix 做规范化：以 / 开头，不能以 / 结尾（除非就是 /），默认 /api
        raw_prefix = os.getenv("API_PREFIX", "/api").strip()
        if not raw_prefix:
            raw_prefix = "/api"
        if not raw_prefix.startswith("/"):
            raw_prefix = "/" + raw_prefix
        if len(raw_prefix) > 1 and raw_prefix.endswith("/"):
            raw_prefix = raw_prefix.rstrip("/")
        self.api_prefix = raw_prefix

        # 对 frontend_base_path 做规范化
        raw_frontend = os.getenv("FRONTEND_BASE_PATH", "/").strip()
        if not raw_frontend:
            raw_frontend = "/"
        if not raw_frontend.startswith("/"):
            raw_frontend = "/" + raw_frontend
        if len(raw_frontend) > 1 and raw_frontend.endswith("/"):
            raw_frontend = raw_frontend.rstrip("/")
        self.frontend_base_path = raw_frontend

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.screenshot_dir,
            self.report_dir,
            self.log_dir,
            self.browser_state_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
