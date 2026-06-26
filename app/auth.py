from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

from app.settings import settings


COOKIE_NAME = "inspection_auth"
DEFAULT_SESSION_TTL_SECONDS = 30 * 60


def get_session_ttl_seconds() -> int:
    try:
        from app.repository import get_system_setting

        minutes = int(get_system_setting("session_ttl_minutes", "30") or "30")
    except Exception:
        minutes = 30
    minutes = max(5, min(minutes, 7 * 24 * 60))
    return minutes * 60


def create_token(username: str) -> str:
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    signature = _sign(payload, settings.admin_password)
    return f"{payload}:{signature}"


def verify_token(token: str | None) -> bool:
    if not token:
        return False
    parts = token.split(":")
    if len(parts) != 3:
        return False
    username, timestamp, signature = parts
    if username != settings.admin_username:
        return False
    try:
        issued_at = int(timestamp)
    except ValueError:
        return False
    if time.time() - issued_at > get_session_ttl_seconds():
        return False
    for secret_value in [settings.secret_key, *getattr(settings, "legacy_secret_keys", [])]:
        expected = _sign(f"{username}:{timestamp}", settings.admin_password, secret_value)
        if hmac.compare_digest(signature, expected):
            return True
    return False


def check_password(username: str, password: str) -> bool:
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(
        password,
        settings.admin_password,
    )


def require_login(request: Request) -> None:
    if verify_token(request.cookies.get(COOKIE_NAME)):
        return
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/login"},
    )


def _sign(payload: str, extra: str = "", secret_value: str | None = None) -> str:
    full_payload = f"{payload}:{extra}"
    return hmac.new(
        (secret_value or settings.secret_key).encode("utf-8"),
        full_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
