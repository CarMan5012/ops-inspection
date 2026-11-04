from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet

from app.settings import settings


def now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.default_timezone))


def today_text() -> str:
    return now_local().strftime("%Y-%m-%d")


def timestamp_text() -> str:
    return now_local().strftime("%Y%m%d_%H%M%S")


def safe_name(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def relative_to_data(path: str | Path) -> str:
    path_obj = Path(path).resolve()
    try:
        return str(path_obj.relative_to(settings.data_dir))
    except ValueError:
        return str(path_obj)


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def render_template(value: str, context: dict[str, str]) -> str:
    result = value
    for key, item in context.items():
        result = result.replace("{" + key + "}", item)
    return result


import os
import json
import base64
import hashlib
import hmac
import time
from urllib.parse import quote


FERNET_SECRET_PREFIX = "fernet:"
LOCAL_SECRET_PREFIX = "v1:"


def _secret_master_key(secret_value: str | None = None) -> bytes:
    key_value = secret_value if secret_value is not None else settings.secret_key
    if not key_value:
        raise RuntimeError("APP secret key is missing")
    raw_key = key_value.encode("utf-8")
    return hashlib.sha256(raw_key).digest()


def _fernet_cipher(secret_value: str | None = None):
    key = base64.urlsafe_b64encode(_secret_master_key(secret_value))
    return Fernet(key)


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _keystream(nonce: bytes, size: int, secret_value: str | None = None) -> bytes:
    key = hmac.new(_secret_master_key(secret_value), b"ops-inspection-secret-stream", hashlib.sha256).digest()
    blocks: list[bytes] = []
    counter = 0
    while sum(len(block) for block in blocks) < size:
        blocks.append(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:size]


def is_encrypted_secret(value: str | None) -> bool:
    text = str(value or "")
    return text.startswith(FERNET_SECRET_PREFIX) or text.startswith(LOCAL_SECRET_PREFIX)


def secret_uses_current_key(value: str | None) -> bool:
    text = str(value or "")
    if text.startswith(FERNET_SECRET_PREFIX):
        cipher = _fernet_cipher()
        try:
            cipher.decrypt(text.removeprefix(FERNET_SECRET_PREFIX).encode("utf-8"))
            return True
        except Exception:
            return False
    if text.startswith(LOCAL_SECRET_PREFIX):
        try:
            raw = base64.urlsafe_b64decode(text.removeprefix(LOCAL_SECRET_PREFIX).encode("ascii"))
            if len(raw) < 16 + 32:
                return False
            body, mac = raw[:-32], raw[-32:]
            expected = hmac.new(_secret_master_key(), b"v1:" + body, hashlib.sha256).digest()
            return hmac.compare_digest(mac, expected)
        except Exception:
            return False
    return False


def encrypt_secret(plain_text: str) -> str:
    """Encrypt secrets before writing them to sqlite."""
    if not plain_text:
        return ""
    cipher = _fernet_cipher()
    return FERNET_SECRET_PREFIX + cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_secret(cipher_text: str) -> str:
    """Decrypt stored secrets and keep compatibility with old base64/plain rows."""
    if not cipher_text:
        return ""
    text = str(cipher_text)
    if text.startswith(FERNET_SECRET_PREFIX):
        token = text.removeprefix(FERNET_SECRET_PREFIX).encode("utf-8")
        for secret_value in [None, *getattr(settings, "legacy_secret_keys", [])]:
            cipher = _fernet_cipher(secret_value)
            try:
                return cipher.decrypt(token).decode("utf-8")
            except Exception:
                continue
        return ""

    if text.startswith(LOCAL_SECRET_PREFIX):
        try:
            raw = base64.urlsafe_b64decode(text.removeprefix(LOCAL_SECRET_PREFIX).encode("ascii"))
            if len(raw) < 16 + 32:
                return ""
            body, mac = raw[:-32], raw[-32:]
            nonce, ciphertext = body[:16], body[16:]
            for secret_value in [None, *getattr(settings, "legacy_secret_keys", [])]:
                expected = hmac.new(_secret_master_key(secret_value), b"v1:" + body, hashlib.sha256).digest()
                if not hmac.compare_digest(mac, expected):
                    continue
                return _xor_bytes(ciphertext, _keystream(nonce, len(ciphertext), secret_value)).decode("utf-8")
            return ""
        except Exception:
            return ""

    try:
        return base64.b64decode(text.encode("utf-8"), validate=True).decode("utf-8")
    except Exception:
        return text


def normalize_secret_for_storage(value: str | None, legacy_base64: bool = False) -> str:
    if not value:
        return ""
    text = str(value)
    if is_encrypted_secret(text):
        if text.startswith(FERNET_SECRET_PREFIX) and secret_uses_current_key(text):
            return text
        plain = decrypt_secret(text)
        if not plain:
            raise ValueError("Stored secret cannot be decrypted; refusing to replace it")
        return encrypt_secret(plain)
    plain = decrypt_secret(text) if legacy_base64 else text
    return encrypt_secret(plain) if plain else ""


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _normalize_totp_secret(secret: str) -> bytes:
    cleaned = "".join(str(secret or "").strip().upper().split())
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    return base64.b32decode(cleaned + padding, casefold=True)


def get_totp_code(secret: str, for_time: int | None = None, step: int = 30, digits: int = 6) -> str:
    timestamp = int(time.time()) if for_time is None else int(for_time)
    counter = int(timestamp // step)
    key = _normalize_totp_secret(secret)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(code_int % (10 ** digits)).zfill(digits)


def verify_totp_code(secret: str, code: str, window: int = 1, replay_key: str = "last_used_totp_slice") -> bool:
    normalized = "".join(str(code or "").split())
    if not secret or len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
        return False
    now = int(time.time())
    matched_time_slice = None
    for drift in range(-window, window + 1):
        target_time = now + drift * 30
        try:
            expected = get_totp_code(secret, target_time)
        except (ValueError, TypeError, base64.binascii.Error):
            return False
        if hmac.compare_digest(expected, normalized):
            matched_time_slice = target_time // 30
            break
            
    if matched_time_slice is None:
        return False
        
    try:
        from app.db import connect
        with connect() as conn:
            # 先取得写锁，让同一验证码的并发请求串行检查与写入。
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM system_settings WHERE key = ?",
                (replay_key,),
            ).fetchone()
            if row:
                last_used = int(row[0] or 0)
                if matched_time_slice <= last_used:
                    return False
            
            conn.execute(
                """
                REPLACE INTO system_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (replay_key, str(matched_time_slice)),
            )
        return True
    except Exception:
        # 无法持久化重放记录时拒绝验证码，避免数据库异常绕过 MFA。
        return False


def build_totp_uri(secret: str, account: str, issuer: str) -> str:
    issuer_text = issuer or "Ops Inspection"
    account_text = account or "admin"
    label = f"{quote(issuer_text)}:{quote(account_text)}"
    return (
        f"otpauth://totp/{label}"
        f"?secret={quote(secret)}&issuer={quote(issuer_text)}&algorithm=SHA1&digits=6&period=30"
    )


def mask_value(value: str) -> str:
    """脱敏敏感信息（如 adm***）用于界面展示"""
    if not value:
        return ""
    if len(value) <= 3:
        return value[0] + "*" * (len(value) - 1)
    return value[:3] + "***"



def resolve_auth_credential(profile: dict[str, Any]) -> dict[str, str]:
    """解析认证的用户名与密码，统一使用页面保存的直填值"""
    if not profile:
        return {"username": "", "password": ""}
        
    username = str(profile.get("username_value") or "")
    if not username:
        username = str(profile.get("username") or "")

    password = decrypt_secret(str(profile.get("password_secret") or ""))
    if not password:
        password = str(profile.get("password") or "")

    return {"username": username, "password": password}
