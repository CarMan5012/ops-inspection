"""Monitor the filesystem containing the persistent application data."""

from __future__ import annotations

import logging
import shutil

from app.repository import get_dingtalk_profile, get_system_setting, set_system_setting
from app.settings import settings

logger = logging.getLogger("app.storage_alert")

ENABLED_KEY = "disk_alert_enabled"
THRESHOLD_KEY = "disk_alert_threshold"
PROFILE_KEY = "disk_alert_profile_id"
SENT_KEY = "disk_alert_sent"


def disk_status() -> dict:
    usage = shutil.disk_usage(settings.data_dir)
    percent = round(usage.used * 100 / usage.total, 1) if usage.total else 0.0
    return {
        "path": str(settings.data_dir),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": percent,
    }


def alert_settings() -> dict:
    return {
        "enabled": get_system_setting(ENABLED_KEY, "0") == "1",
        "threshold": int(get_system_setting(THRESHOLD_KEY, "80")),
        "profile_id": int(get_system_setting(PROFILE_KEY, "0")),
    }


def check_disk_alert() -> None:
    """Send once per threshold crossing; a failed send can retry next check."""
    try:
        config = alert_settings()
        if not config["enabled"]:
            return
        disk = disk_status()
        if disk["used_percent"] < config["threshold"]:
            if get_system_setting(SENT_KEY, "0") != "0":
                set_system_setting(SENT_KEY, "0")
            return
        if get_system_setting(SENT_KEY, "0") == "1":
            return
        profile = get_dingtalk_profile(config["profile_id"])
        if not profile:
            logger.warning("磁盘告警未发送：钉钉机器人不存在")
            return
        from app.dingtalk import send_dingtalk_msg
        from app.utils import decrypt_secret

        webhook = decrypt_secret(profile["webhook"]) if profile.get("webhook") else ""
        secret = decrypt_secret(profile["secret"]) if profile.get("secret") else ""
        title = "巡检系统磁盘空间告警"
        text = (
            "### 巡检系统磁盘空间告警\n\n"
            f"- 数据目录：`{disk['path']}`\n"
            f"- 已用：**{disk['used_percent']}%**（阈值 {config['threshold']}%）\n"
            f"- 已用容量：{disk['used_bytes'] / 1024 ** 3:.1f} GB\n"
            f"- 可用容量：{disk['free_bytes'] / 1024 ** 3:.1f} GB"
        )
        result = send_dingtalk_msg(webhook, secret, profile.get("keyword"), title, text)
        if result == "sent":
            set_system_setting(SENT_KEY, "1")
            logger.warning("磁盘空间已达到 %s%%，钉钉告警已发送", disk["used_percent"])
        else:
            logger.error("磁盘空间告警发送失败：%s", result)
    except Exception:
        logger.exception("磁盘空间告警检查失败")
