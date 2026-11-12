import sqlite3
import sys
import types

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import storage_alert
import pytest
from fastapi import HTTPException


def test_disk_alert_sends_once_until_usage_recovers(monkeypatch):
    saved = {storage_alert.SENT_KEY: "0"}
    sent = []
    current = {"percent": 81.0}
    monkeypatch.setattr(storage_alert, "alert_settings", lambda: {"enabled": True, "threshold": 80, "profile_id": 2})
    monkeypatch.setattr(storage_alert, "disk_status", lambda: {
        "path": "/app/data", "used_percent": current["percent"],
        "used_bytes": 81 * 1024 ** 3, "free_bytes": 19 * 1024 ** 3,
    })
    monkeypatch.setattr(storage_alert, "get_system_setting", lambda key, default="": saved.get(key, default))
    monkeypatch.setattr(storage_alert, "set_system_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(storage_alert, "get_dingtalk_profile", lambda _id: {
        "webhook": "encrypted", "secret": "", "keyword": "巡检",
    })
    from app import dingtalk, utils
    monkeypatch.setattr(utils, "decrypt_secret", lambda _: "https://example.test/robot")
    monkeypatch.setattr(dingtalk, "send_dingtalk_msg", lambda *args: sent.append(args) or "sent")

    storage_alert.check_disk_alert()
    storage_alert.check_disk_alert()
    assert len(sent) == 1
    assert saved[storage_alert.SENT_KEY] == "1"
    current["percent"] = 75.0
    storage_alert.check_disk_alert()
    assert saved[storage_alert.SENT_KEY] == "0"
    current["percent"] = 82.0
    storage_alert.check_disk_alert()
    assert len(sent) == 2


def test_disk_alert_retries_failed_send(monkeypatch):
    saved = {}
    monkeypatch.setattr(storage_alert, "alert_settings", lambda: {"enabled": True, "threshold": 80, "profile_id": 2})
    monkeypatch.setattr(storage_alert, "disk_status", lambda: {
        "path": "/app/data", "used_percent": 85.0, "used_bytes": 85, "free_bytes": 15,
    })
    monkeypatch.setattr(storage_alert, "get_system_setting", lambda key, default="": saved.get(key, default))
    monkeypatch.setattr(storage_alert, "set_system_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(storage_alert, "get_dingtalk_profile", lambda _id: {"webhook": "encrypted", "secret": ""})
    from app import dingtalk, utils
    monkeypatch.setattr(utils, "decrypt_secret", lambda _: "https://example.test/robot")
    monkeypatch.setattr(dingtalk, "send_dingtalk_msg", lambda *args: "failed: timeout")

    storage_alert.check_disk_alert()
    assert storage_alert.SENT_KEY not in saved


def test_disk_alert_settings_require_valid_profile_and_threshold(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "get_dingtalk_profile", lambda _id: None)
    with pytest.raises(HTTPException, match="钉钉机器人"):
        main.api_update_disk_alert({"enabled": True, "threshold": 80, "profile_id": 99})
    with pytest.raises(HTTPException, match="告警阈值"):
        main.api_update_disk_alert({"enabled": False, "threshold": 100, "profile_id": 0})
