from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
    raw_key = (secret_value or settings.secret_key or "change-me").encode("utf-8")
    return hashlib.sha256(raw_key).digest()


def _fernet_cipher(secret_value: str | None = None):
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except Exception:
        return None
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
        if cipher is None:
            return False
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
    if cipher is not None:
        return FERNET_SECRET_PREFIX + cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    nonce = os.urandom(16)
    plain = plain_text.encode("utf-8")
    ciphertext = _xor_bytes(plain, _keystream(nonce, len(plain)))
    body = nonce + ciphertext
    mac = hmac.new(_secret_master_key(), b"v1:" + body, hashlib.sha256).digest()
    return LOCAL_SECRET_PREFIX + base64.urlsafe_b64encode(body + mac).decode("ascii")


def decrypt_secret(cipher_text: str) -> str:
    """Decrypt stored secrets and keep compatibility with old base64/plain rows."""
    if not cipher_text:
        return ""
    text = str(cipher_text)
    if text.startswith(FERNET_SECRET_PREFIX):
        token = text.removeprefix(FERNET_SECRET_PREFIX).encode("utf-8")
        for secret_value in [None, *getattr(settings, "legacy_secret_keys", [])]:
            cipher = _fernet_cipher(secret_value)
            if cipher is None:
                continue
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
        if secret_uses_current_key(text):
            return text
        plain = decrypt_secret(text)
        return encrypt_secret(plain) if plain else ""
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


def verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    normalized = "".join(str(code or "").split())
    if not normalized.isdigit():
        return False
    now = int(time.time())
    for drift in range(-window, window + 1):
        expected = get_totp_code(secret, now + drift * 30)
        if hmac.compare_digest(expected, normalized):
            return True
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


def resolve_mail_credential(profile: dict[str, Any]) -> str:
    """解析邮件 SMTP 的发信密码，统一使用页面保存的直填值"""
    if not profile:
        return ""
        
    password = decrypt_secret(str(profile.get("password_secret") or ""))
    if not password:
        password = str(profile.get("password") or "")
            
    return password


def times_to_cron(
    frequency: str,
    day_of_week: str | None,
    day_of_month: str | None,
    times_list: list[dict[str, Any]],
) -> tuple[str, str]:
    """
    将中文时间表单字段转换为分号分隔的 5段 cron 表达式及可读中文说明
    """
    if not times_list:
        times_list = [{"ampm": "am", "hour": 9, "minute": 0}]
        
    cron_list = []
    label_times = []
    
    day_month_cron = "*"
    day_week_cron = "*"
    label_prefix = ""
    
    if frequency == "daily":
        label_prefix = "每天"
    elif frequency == "workday":
        day_week_cron = "mon-fri"
        label_prefix = "工作日(周一至周五)"
    elif frequency == "weekly":
        mapping_en = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}
        mapping_cn = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
        try:
            val = int(day_of_week or 1)
        except ValueError:
            val = 1
        day_week_cron = mapping_en.get(val, "mon")
        label_prefix = f"每周{mapping_cn.get(val, '一')}"
    elif frequency == "monthly":
        try:
            val = int(day_of_month or 1)
        except ValueError:
            val = 1
        day_month_cron = str(val)
        label_prefix = f"每月 {val} 号"
    elif frequency == "monthly_last":
        day_month_cron = "L"
        label_prefix = "每月最后一天"
        
    for t in times_list:
        ampm = t.get("ampm", "am")
        try:
            hour_12 = int(t.get("hour", 9))
        except (ValueError, TypeError):
            hour_12 = 9
        try:
            minute = int(t.get("minute", 0))
        except (ValueError, TypeError):
            minute = 0
            
        if ampm == "pm" and hour_12 < 12:
            hour_24 = hour_12 + 12
        elif ampm == "am" and hour_12 == 12:
            hour_24 = 0
        else:
            hour_24 = hour_12
            
        # 5 段标准 crontab：分 时 日 月 周（去掉了秒，避免 6 段问题）
        cron_list.append(f"{minute} {hour_24} {day_month_cron} * {day_week_cron}")
        
        ampm_cn = "上午" if ampm == "am" else "下午"
        label_times.append(f"{ampm_cn}{hour_12}点{minute:02d}分" if minute else f"{ampm_cn}{hour_12}点")
        
    cron_expr = ";".join(cron_list)
    label_expr = f"{label_prefix} {', '.join(label_times)}"
    return cron_expr, label_expr


def validate_cron_expression(cron_expression: str) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from zoneinfo import ZoneInfo
    cron_parts = [p.strip() for p in cron_expression.split(";") if p.strip()]
    if not cron_parts:
        raise ValueError("定时 Cron 表达式不能为空")
    for cron_str in cron_parts:
        # 去掉 L 以检测常规段落合法性
        cron_str_test = cron_str.replace("L", "*")
        parts = cron_str_test.split()
        if len(parts) == 6:
            # 兼容处理可能传进来的 6 段 cron（跳过第一段秒）
            cron_str_test = " ".join(parts[1:])
        elif len(parts) != 5:
            raise ValueError(f"表达式 '{cron_str}' 格式不正确，必须为 5 段(分 时 日 月 周)")
        try:
            CronTrigger.from_crontab(cron_str_test, timezone=ZoneInfo(settings.default_timezone))
        except Exception as e:
            raise ValueError(f"表达式 '{cron_str}' 无效: {e}")


def get_next_run_times(cron_expression: str, limit: int = 3) -> list[str]:
    from apscheduler.triggers.cron import CronTrigger
    from zoneinfo import ZoneInfo
    import calendar
    
    if not cron_expression:
        return []
        
    local_tz = ZoneInfo(settings.default_timezone)
    now = datetime.now(local_tz)
    
    cron_parts = [p.strip() for p in cron_expression.split(";") if p.strip()]
    fire_times = []
    
    for cron_str in cron_parts:
        is_monthly_last = "L" in cron_str
        cron_str_for_trigger = cron_str.replace("L", "*")
        
        parts = cron_str_for_trigger.split()
        if len(parts) == 6:
            cron_str_for_trigger = " ".join(parts[1:])
            
        try:
            trigger = CronTrigger.from_crontab(cron_str_for_trigger, timezone=local_tz)
        except Exception:
            continue
            
        prev = now
        for _ in range(limit * 8):
            next_time = trigger.get_next_fire_time(None, prev)
            if not next_time:
                break
            
            if is_monthly_last:
                # 检查此 next_time 是否真的为该月最后一天
                last_day = calendar.monthrange(next_time.year, next_time.month)[1]
                if next_time.day == last_day:
                    fire_times.append(next_time)
            else:
                fire_times.append(next_time)
            prev = next_time
            
    if not fire_times:
        return []
        
    unique_times = sorted(list(set(fire_times)))
    return [t.strftime("%Y-%m-%d %H:%M:%S") for t in unique_times[:limit]]


def has_more_scheduled_runs_today(cron_expression: str, start_time_str: str, timezone_str: str) -> bool:
    from apscheduler.triggers.cron import CronTrigger
    from zoneinfo import ZoneInfo
    from datetime import datetime, timedelta
    
    if not cron_expression:
        return False
        
    local_tz = ZoneInfo(timezone_str)
    
    try:
        if "T" in start_time_str:
            base_dt = datetime.fromisoformat(start_time_str)
        else:
            base_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=local_tz)
    except Exception:
        base_dt = datetime.now(local_tz)
        
    check_time = base_dt + timedelta(seconds=60)
    
    cron_parts = [p.strip() for p in cron_expression.split(";") if p.strip()]
    for cron_str in cron_parts:
        cron_str_for_trigger = cron_str.replace("L", "*")
        parts = cron_str_for_trigger.split()
        if len(parts) == 6:
            cron_str_for_trigger = " ".join(parts[1:])
        try:
            trigger = CronTrigger.from_crontab(cron_str_for_trigger, timezone=local_tz)
            next_time = trigger.get_next_fire_time(None, check_time)
            if next_time:
                next_time_local = next_time.astimezone(local_tz)
                if (next_time_local.year == base_dt.year and 
                    next_time_local.month == base_dt.month and 
                    next_time_local.day == base_dt.day):
                    return True
        except Exception:
            continue
    return False
