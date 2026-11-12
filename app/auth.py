from __future__ import annotations

import hashlib
import hmac
import base64
import secrets
import time

from fastapi import HTTPException, Request, status

from app.settings import settings
from app.installation import admin_account, installation_state


COOKIE_NAME = "inspection_auth"
DEFAULT_SESSION_TTL_SECONDS = 30 * 60
MAX_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SCRYPT_N = 2 ** 17
SCRYPT_R = 8
SCRYPT_P = 1


def hash_admin_password(password: str) -> str:
    """Hash an administrator password without storing the plaintext."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=256 * 1024 * 1024)
    return "scrypt:%d:%d:%d:%s:%s" % (
        SCRYPT_N, SCRYPT_R, SCRYPT_P,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _verify_admin_password_hash(password: str, encoded: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded.split(":")
        n, r, p = int(n_text), int(r_text), int(p_text)
        if (algorithm, n, r, p) != ("scrypt", SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        salt = base64.b64decode(salt_text, altchars=b"-_", validate=True)
        expected = base64.b64decode(digest_text, altchars=b"-_", validate=True)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n,
                                r=r, p=p, dklen=32, maxmem=256 * 1024 * 1024)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _session_signing_extra() -> str:
    account = admin_account()
    return account[1] if account else ""


def admin_username() -> str:
    account = admin_account()
    return account[0] if account else ""


def get_session_ttl_seconds() -> int:
    return DEFAULT_SESSION_TTL_SECONDS


def create_token(username: str) -> str:
    timestamp = str(int(time.time()))
    lifetime = get_session_ttl_seconds()
    payload = f"{username}:{timestamp}:{lifetime}:{secrets.token_urlsafe(12)}"
    signature = _sign(payload, _session_signing_extra())
    return f"{payload}:{signature}"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_idle_token(username: str) -> str:
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}:idle:v1:{secrets.token_urlsafe(12)}"
    token = f"{payload}:{_sign(payload, _session_signing_extra())}"
    from app.db import connect
    with connect() as conn:
        conn.execute(
            "DELETE FROM auth_sessions WHERE last_activity < ?",
            (time.time() - MAX_SESSION_TTL_SECONDS,),
        )
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, last_activity) VALUES (?, ?)",
            (_token_hash(token), time.time()),
        )
    return token


def idle_session_active(token: str, now: float, lifetime: int) -> bool:
    try:
        from app.db import connect
        with connect() as conn:
            row = conn.execute(
                "SELECT last_activity FROM auth_sessions WHERE token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
        return bool(row and now - float(row[0]) < lifetime)
    except Exception:
        return False


def renew_idle_session(token: str) -> bool:
    if not token or not verify_token(token) or len(token.split(":")) != 6:
        return False
    now = time.time()
    try:
        from app.db import connect
        with connect() as conn:
            cursor = conn.execute(
                "UPDATE auth_sessions SET last_activity = ? WHERE token_hash = ? AND last_activity > ?",
                (now, _token_hash(token), now - get_session_ttl_seconds()),
            )
            return cursor.rowcount == 1
    except Exception:
        return False


def is_token_revoked(token: str) -> bool:
    if not token:
        return True
    try:
        from app.db import connect
        with connect() as conn:
            # 定期清理已过期的 Token 黑名单数据
            conn.execute("DELETE FROM token_blacklist WHERE expired_at < ?", (time.time(),))
            row = conn.execute("SELECT 1 FROM token_blacklist WHERE token = ?", (token,)).fetchone()
            return row is not None
    except Exception:
        return False


def revoke_token(token: str | None) -> None:
    if not token:
        return
    parts = token.split(":")
    if len(parts) == 6 and parts[2:4] == ["idle", "v1"]:
        try:
            from app.db import connect
            with connect() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))
        except Exception:
            pass
        return
    if len(parts) not in (3, 5):
        return
    timestamp = parts[1]
    try:
        issued_at = int(timestamp)
    except ValueError:
        return
    
    # 设置日后可能延长；黑名单必须覆盖该令牌可能通过校验的最长时间。
    expired_at = issued_at + MAX_SESSION_TTL_SECONDS
    if expired_at > time.time():
        try:
            from app.db import connect
            with connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO token_blacklist (token, expired_at) VALUES (?, ?)",
                    (token, expired_at)
                )
                conn.execute("DELETE FROM token_blacklist WHERE expired_at < ?", (time.time(),))
        except Exception:
            pass


def verify_token(token: str | None) -> bool:
    if not token:
        return False
    if is_token_revoked(token):
        return False
    parts = token.split(":")
    if len(parts) not in (3, 5, 6):
        return False
    username, timestamp = parts[:2]
    signature = parts[-1]
    account = admin_account()
    if not account or username != account[0]:
        return False
    try:
        issued_at = int(timestamp)
    except ValueError:
        return False
    current_lifetime = get_session_ttl_seconds()
    if len(parts) == 6:
        if parts[2:4] != ["idle", "v1"] or not parts[4]:
            return False
        lifetime = current_lifetime
    elif len(parts) == 5:
        try:
            issued_lifetime = int(parts[2])
        except ValueError:
            return False
        if not 5 * 60 <= issued_lifetime <= MAX_SESSION_TTL_SECONDS or not parts[3]:
            return False
        lifetime = min(current_lifetime, issued_lifetime)
    else:
        lifetime = current_lifetime  # 兼容升级前已签发的会话
    now = time.time()
    if (len(parts) != 6 and now - issued_at > lifetime) or issued_at > now + 60:
        return False
    for secret_value in [settings.secret_key, *getattr(settings, "legacy_secret_keys", [])]:
        expected = _sign(":".join(parts[:-1]), account[1], secret_value)
        if hmac.compare_digest(signature, expected):
            return len(parts) != 6 or idle_session_active(token, now, lifetime)
    return False


def check_password(username: str, password: str) -> bool:
    account = admin_account()
    return bool(account and hmac.compare_digest(username, account[0])
                and _verify_admin_password_hash(password, account[1]))


def require_login(request: Request) -> None:
    if verify_token(request.cookies.get(COOKIE_NAME)):
        return
    from app.access_context import public_base_path

    base = public_base_path()
    login_url = "/setup" if installation_state() == "pending" else "/login"
    if base:
        login_url = base + login_url
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": login_url},
    )


def _sign(payload: str, extra: str = "", secret_value: str | None = None) -> str:
    full_payload = f"{payload}:{extra}"
    return hmac.new(
        (secret_value or settings.secret_key).encode("utf-8"),
        full_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
