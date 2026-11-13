from __future__ import annotations

import secrets
from pathlib import Path

from app.access_context import INTERNAL_BASE


class Settings:
    def __init__(self) -> None:
        self.app_name = "运维巡检平台"
        self.data_dir = Path("data").resolve()
        self.secret_key = self._load_or_create_secret_key()
        self.legacy_secret_keys = ["change-me"] if self.secret_key != "change-me" else []
        self.db_path = self.data_dir / "app.db"
        self.report_dir = self.data_dir / "reports"
        self.log_dir = self.data_dir / "logs"
        self.browser_state_dir = self.data_dir / "browser-state"

        self.default_timezone = "Asia/Shanghai"
        self.holiday_primary_url = "https://timor.tech/api/holiday/year/{year}/"
        self.holiday_backup_url = "https://publicapi.xiaoai.me/holiday/year?date={year}"
        # Private router namespace. The public path is stored in system_settings.
        self.frontend_base_path = INTERNAL_BASE
        self.api_prefix = INTERNAL_BASE + "/api"

    def _load_or_create_secret_key(self) -> str:
        key_path = self.data_dir / "app-secret.key"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if key_path.exists():
                value = key_path.read_text(encoding="utf-8").strip()
                if value:
                    return value
                raise RuntimeError(f"APP secret file is empty: {key_path}")
            value = secrets.token_urlsafe(48)
            try:
                with open(key_path, "x", encoding="utf-8") as f:
                    f.write(value)
            except FileExistsError:
                pass
            saved = key_path.read_text(encoding="utf-8").strip()
            if not saved:
                raise RuntimeError(f"APP secret file is empty: {key_path}")
            return saved
        except OSError as exc:
            raise RuntimeError(f"APP secret file is not readable or writable: {key_path}") from exc

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.report_dir,
            self.log_dir,
            self.browser_state_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
