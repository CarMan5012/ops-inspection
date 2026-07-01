from __future__ import annotations

import os
import secrets
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "自动化巡检报告系统")
        self.admin_username = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_password = os.getenv("ADMIN_PASSWORD", "admin")

        self.data_dir = Path(os.getenv("APP_DATA_DIR", "data")).resolve()
        raw_secret_key = self._load_secret_from_file() or os.getenv("APP_SECRET_KEY", "").strip()
        self.secret_key = raw_secret_key or self._load_or_create_secret_key()
        self.legacy_secret_keys = ["change-me"] if self.secret_key != "change-me" else []
        self.db_path = self.data_dir / "app.db"
        self.screenshot_dir = self.data_dir / "screenshots"
        self.report_dir = self.data_dir / "reports"
        self.log_dir = self.data_dir / "logs"
        self.browser_state_dir = self.data_dir / "browser-state"

        self.default_timezone = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
        self.max_recent_runs = int(os.getenv("APP_MAX_RECENT_RUNS", "50"))

        # 全局钉钉配置
        self.dingtalk_webhook = os.getenv("DINGTALK_WEBHOOK", "").strip()
        self.dingtalk_secret = os.getenv("DINGTALK_SECRET", "").strip()
        self.dingtalk_keyword = os.getenv("DINGTALK_KEYWORD", "").strip()

        # 对 frontend_base_path 做规范化
        raw_frontend = os.getenv("FRONTEND_BASE_PATH", "/ops").strip()
        if not raw_frontend:
            raw_frontend = "/"
        if not raw_frontend.startswith("/"):
            raw_frontend = "/" + raw_frontend
        if len(raw_frontend) > 1 and raw_frontend.endswith("/"):
            raw_frontend = raw_frontend.rstrip("/")
        self.frontend_base_path = raw_frontend

        # 对 api_prefix 做规范化：以 / 开头，不能以 / 结尾（除非就是 /），默认 /api
        raw_prefix = os.getenv("API_PREFIX", "/api").strip()
        if not raw_prefix:
            raw_prefix = "/api"
        if not raw_prefix.startswith("/"):
            raw_prefix = "/" + raw_prefix
        if len(raw_prefix) > 1 and raw_prefix.endswith("/"):
            raw_prefix = raw_prefix.rstrip("/")
        
        # 将 api_prefix 统一挂载在 frontend_base_path 下（如果 frontend_base_path 不是 / 并且 api_prefix 不以 frontend_base_path 开头）
        if self.frontend_base_path != "/" and not raw_prefix.startswith(self.frontend_base_path + "/"):
            self.api_prefix = self.frontend_base_path + raw_prefix
        else:
            self.api_prefix = raw_prefix

    def _load_secret_from_file(self) -> str:
        explicit_path = os.getenv("APP_SECRET_KEY_FILE", "").strip()
        if explicit_path:
            return self._read_or_create_secret_file(Path(explicit_path))

        default_secret_path = Path("/run/secrets/app_secret_key")
        if default_secret_path.exists():
            return self._read_or_create_secret_file(default_secret_path)
        return ""

    def _read_or_create_secret_file(self, path: Path) -> str:
        try:
            if path.exists():
                if path.is_dir():
                    raise RuntimeError(f"APP secret file path is a directory: {path}")
                secret = path.read_text(encoding="utf-8").strip()
                if secret:
                    return secret
            path.parent.mkdir(parents=True, exist_ok=True)
            secret = secrets.token_urlsafe(48)
            path.write_text(secret + "\n", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return secret
        except OSError as exc:
            raise RuntimeError(f"APP secret file is not readable or writable: {path}") from exc

    def _load_or_create_secret_key(self) -> str:
        key_path = self.data_dir / "app-secret.key"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if key_path.exists():
                value = key_path.read_text(encoding="utf-8").strip()
                if value:
                    return value
            value = secrets.token_urlsafe(48)
            key_path.write_text(value, encoding="utf-8")
            return value
        except OSError:
            return "change-me"

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
