"""Public URL prefix selected during installation or in system settings."""

from __future__ import annotations

import re


SETTING_KEY = "access_context"
INTERNAL_BASE = "/ops"
# Existing initialized installations used /ops before this setting existed.
LEGACY_BASE = "/ops"


def normalize_access_context(value: str) -> str:
    value = value.strip()
    if value in {"", "/"}:
        return ""
    if len(value) > 80 or not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value):
        raise ValueError("访问路径前缀需为空，或以 / 开头并仅包含字母、数字、下划线、连字符和多级路径")
    return value


def public_base_path() -> str:
    from app.installation import installation_state
    from app.repository import get_system_setting

    if installation_state() == "pending":
        return ""
    return get_system_setting(SETTING_KEY, LEGACY_BASE)


def public_api_prefix() -> str:
    return public_base_path() + "/api"


def cookie_path() -> str:
    return public_base_path() or "/"
