"""First-run administrator setup and account storage."""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from app.db import connect
from app.settings import settings


def installation_state() -> str:
    with connect() as conn:
        row = conn.execute("SELECT state FROM installation_state WHERE id = 1").fetchone()
    return str(row[0]) if row else "unknown"


def admin_account() -> tuple[str, str] | None:
    with connect() as conn:
        row = conn.execute("SELECT username, password_hash FROM admin_account WHERE id = 1").fetchone()
    return (str(row[0]), str(row[1])) if row else None


def _setup_code_path() -> Path:
    return settings.data_dir / "setup-code"


def ensure_setup_code() -> str | None:
    if installation_state() != "pending":
        return None
    path = _setup_code_path()
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(secrets.token_urlsafe(24))
        path.chmod(0o600)
    except FileExistsError:
        pass
    code = path.read_text(encoding="utf-8").strip()
    if len(code) < 24:
        raise RuntimeError("初始化码文件无效，请检查数据目录")
    return code


def verify_setup_code(code: str) -> bool:
    path = _setup_code_path()
    try:
        expected = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    return installation_state() == "pending" and hmac.compare_digest(code, expected)


def complete_setup(username: str, password: str, code: str, *, preferences: dict[str, str] | None = None) -> bool:
    if not verify_setup_code(code):
        return False
    path = _setup_code_path()

    from app.auth import hash_admin_password

    password_hash = hash_admin_password(password)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT state FROM installation_state WHERE id = 1").fetchone()
        account = conn.execute("SELECT 1 FROM admin_account WHERE id = 1").fetchone()
        if not state or state[0] != "pending" or account:
            return False
        conn.execute(
            "INSERT INTO admin_account (id, username, password_hash) VALUES (1, ?, ?)",
            (username, password_hash),
        )
        for key, value in (preferences or {}).items():
            if key not in {"app_name", "access_context", "holiday_primary_url", "holiday_backup_url"}:
                raise ValueError("Unsupported setup preference")
            conn.execute(
                "REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, value),
            )
        conn.execute("UPDATE installation_state SET state = 'initialized' WHERE id = 1")
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # The database state still closes the setup route.
    return True


def change_admin_username(current_password: str, new_username: str) -> bool:
    from app.auth import _verify_admin_password_hash

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT password_hash FROM admin_account WHERE id = 1").fetchone()
        if not row or not _verify_admin_password_hash(current_password, str(row[0])):
            return False
        conn.execute("UPDATE admin_account SET username = ? WHERE id = 1", (new_username,))
        conn.execute("DELETE FROM auth_sessions")
    return True


def change_admin_password(current_password: str, new_password: str) -> bool:
    from app.auth import _verify_admin_password_hash, hash_admin_password

    new_hash = hash_admin_password(new_password)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT password_hash FROM admin_account WHERE id = 1").fetchone()
        if not row or not _verify_admin_password_hash(current_password, str(row[0])):
            return False
        conn.execute("UPDATE admin_account SET password_hash = ? WHERE id = 1", (new_hash,))
        conn.execute("DELETE FROM auth_sessions")
    return True
