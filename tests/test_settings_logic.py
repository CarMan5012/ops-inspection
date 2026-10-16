import json
import sqlite3
import sys
import types

import pytest
from fastapi import HTTPException


def _load_modules():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import db, main, scheduler, utils
    return db, main, scheduler, utils


def test_mfa_rebinding_keeps_existing_protection_until_code_is_verified(monkeypatch):
    _, main, _, utils = _load_modules()

    stored = {
        main.MFA_ENABLED_KEY: "1",
        main.MFA_SECRET_KEY: "old-secret",
        "dingtalk_keyword": "保留关键字",
    }
    monkeypatch.setattr(main, "get_system_setting", lambda key, default="": stored.get(key, default))
    monkeypatch.setattr(main, "set_system_setting", lambda key, value: stored.__setitem__(key, value))
    monkeypatch.setattr(main, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(utils, "encrypt_secret", lambda value: value)
    monkeypatch.setattr(main, "generate_totp_secret", lambda: "new-secret")
    monkeypatch.setattr(main, "build_totp_uri", lambda *_: "otpauth://test")
    monkeypatch.setattr(main, "verify_totp_code", lambda secret, code, **_: secret == "new-secret" and code == "123456")

    main.api_generate_mfa_secret()
    assert stored[main.MFA_ENABLED_KEY] == "1"
    assert stored[main.MFA_SECRET_KEY] == "old-secret"
    assert stored[main.MFA_PENDING_SECRET_KEY] == "new-secret"

    with pytest.raises(HTTPException, match="MFA 验证码不正确"):
        main.api_update_security_settings({"mfa_enabled": True, "mfa_code": "000000"})
    assert stored[main.MFA_SECRET_KEY] == "old-secret"

    main.api_update_security_settings({"mfa_enabled": True, "mfa_code": "123456"})
    assert stored[main.MFA_SECRET_KEY] == "new-secret"
    assert stored[main.MFA_PENDING_SECRET_KEY] == ""
    assert stored[main.MFA_ENABLED_KEY] == "1"

    main.api_update_security_settings({"session_ttl_minutes": 60})
    assert stored["dingtalk_keyword"] == "保留关键字"


def test_storage_schedule_label_matches_saved_frequency(monkeypatch):
    db, main, scheduler, _ = _load_modules()

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE storage_cleanup_settings (
            id INTEGER PRIMARY KEY, enabled INTEGER, allow_manual_cleanup INTEGER,
            cleanup_schedule_mode TEXT, cleanup_schedule_label TEXT,
            cleanup_schedule_config TEXT, periodic_sent_retention_days INTEGER,
            periodic_failed_retention_days INTEGER, screenshot_retention_days INTEGER,
            report_retention_days INTEGER, run_record_retention_days INTEGER,
            log_retention_days INTEGER, browser_state_retention_days INTEGER,
            browser_state_cleanup_enabled INTEGER, protect_recent_days INTEGER,
            updated_at TEXT
        )"""
    )
    monkeypatch.setattr(db, "connect", lambda: connection)
    monkeypatch.setattr(scheduler, "reload_jobs", lambda: None)
    try:
        main.api_update_storage_settings({
            "cleanup_schedule_mode": "weekly",
            "cleanup_schedule_label": "每天 02:30",
            "cleanup_schedule_config": json.dumps({"time": "09:15", "day_of_week": "3"}),
            "screenshot_retention_days": 45,
        })
        row = connection.execute(
            "SELECT cleanup_schedule_mode, cleanup_schedule_label, cleanup_schedule_config FROM storage_cleanup_settings"
        ).fetchone()
        assert row[0] == "weekly"
        assert row[1] == "每周 周三 09:15"
        assert json.loads(row[2]) == {"time": "09:15", "day_of_week": "3"}

        main.api_update_storage_settings({"enabled": False})
        retained = connection.execute(
            "SELECT cleanup_schedule_label, screenshot_retention_days, enabled FROM storage_cleanup_settings"
        ).fetchone()
        assert retained[0] == "每周 周三 09:15"
        assert retained[1] == 45
        assert retained[2] == 0

        main.api_update_storage_settings({"enabled": True, "allow_manual_cleanup": True})
        enabled = connection.execute(
            "SELECT enabled, allow_manual_cleanup FROM storage_cleanup_settings"
        ).fetchone()
        assert tuple(enabled) == (1, 1)

        connection.execute("CREATE TABLE storage_cleanup_runs (id INTEGER PRIMARY KEY)")
        from app import cleanup
        monkeypatch.setattr(cleanup, "get_storage_usage", lambda: {"total_bytes": 0})
        response = main.api_get_storage()
        assert response["config"]["enabled"] is True
        assert response["config"]["allow_manual_cleanup"] is True
        assert response["config"]["browser_state_cleanup_enabled"] is False

        with pytest.raises(HTTPException, match="自动清理计划无效"):
            main.api_update_storage_settings({
                "cleanup_schedule_mode": "monthly",
                "cleanup_schedule_config": json.dumps({"time": "25:00", "day_of_month": "32"}),
            })
        assert connection.execute("SELECT cleanup_schedule_mode FROM storage_cleanup_settings").fetchone()[0] == "weekly"
    finally:
        connection.close()


def test_storage_retention_settings_survive_startup_seed():
    db, _, _, _ = _load_modules()
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript("""
            CREATE TABLE periodic_report_settings (report_type TEXT);
            INSERT INTO periodic_report_settings VALUES ('weekly'), ('monthly');
            CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE storage_cleanup_settings (
                id INTEGER PRIMARY KEY, enabled INTEGER,
                periodic_sent_retention_days INTEGER, report_retention_days INTEGER,
                protect_recent_days INTEGER, browser_state_retention_days INTEGER
            );
            INSERT INTO storage_cleanup_settings VALUES (1, 1, 3, 30, 3, 90);
        """)
        db.seed_system_defaults(connection)
        db.seed_system_defaults(connection)
        row = connection.execute("""
            SELECT periodic_sent_retention_days, report_retention_days,
                   protect_recent_days, browser_state_retention_days
            FROM storage_cleanup_settings
        """).fetchone()
        assert tuple(row) == (3, 30, 3, 90)
    finally:
        connection.close()


def test_new_mfa_secret_uses_separate_replay_window(monkeypatch):
    db, _, _, utils = _load_modules()
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
    )
    monkeypatch.setattr(db, "connect", lambda: connection)
    current_secret = "JBSWY3DPEHPK3PXP"
    pending_secret = "KRUGS4ZANFZSAYJA"
    try:
        assert utils.verify_totp_code(current_secret, utils.get_totp_code(current_secret))
        pending_code = utils.get_totp_code(pending_secret)
        assert utils.verify_totp_code(pending_secret, pending_code, replay_key="last_used_pending_totp_slice")
        assert not utils.verify_totp_code(pending_secret, pending_code, replay_key="last_used_pending_totp_slice")
    finally:
        connection.close()
