import json
import sqlite3
import sys
import types

import pytest
from fastapi import HTTPException, Request, Response


def _load_modules():
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import db, main, scheduler, utils
    return db, main, scheduler, utils


@pytest.fixture(autouse=True)
def mock_admin_account(monkeypatch):
    _load_modules()
    from app import auth

    password_hash = auth.hash_admin_password("test-password")
    monkeypatch.setattr(auth, "admin_account", lambda: ("admin", password_hash))


def test_mfa_rebinding_keeps_existing_protection_until_code_is_verified(monkeypatch):
    _, main, _, utils = _load_modules()

    stored = {
        main.MFA_ENABLED_KEY: "1",
        main.MFA_SECRET_KEY: "old-secret",
        "session_ttl_minutes": "10080",
        "dingtalk_keyword": "保留关键字",
    }
    monkeypatch.setattr(main, "get_system_setting", lambda key, default="": stored.get(key, default))
    monkeypatch.setattr(main, "set_system_setting", lambda key, value: stored.__setitem__(key, value))
    monkeypatch.setattr(main, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(utils, "encrypt_secret", lambda value: value)
    monkeypatch.setattr(main, "generate_totp_secret", lambda: "new-secret")
    monkeypatch.setattr(main, "build_totp_uri", lambda *_: "otpauth://test")
    monkeypatch.setattr(main, "verify_totp_code", lambda secret, code, **_: (secret, code) in {("new-secret", "123456"), ("old-secret", "654321")})

    main.api_generate_mfa_secret()
    assert stored[main.MFA_ENABLED_KEY] == "1"
    assert stored[main.MFA_SECRET_KEY] == "old-secret"
    assert stored[main.MFA_PENDING_SECRET_KEY] == "new-secret"

    with pytest.raises(HTTPException, match="当前 MFA 验证码不正确"):
        main.api_update_security_settings({"mfa_enabled": True, "mfa_code": "123456"})
    assert stored[main.MFA_SECRET_KEY] == "old-secret"

    with pytest.raises(HTTPException, match="MFA 验证码不正确"):
        main.api_update_security_settings({"mfa_enabled": True, "mfa_code": "000000", "mfa_current_code": "654321"})
    assert stored[main.MFA_SECRET_KEY] == "old-secret"

    main.api_update_security_settings({"mfa_enabled": True, "mfa_code": "123456", "mfa_current_code": "654321"})
    assert stored[main.MFA_SECRET_KEY] == "new-secret"
    assert stored[main.MFA_PENDING_SECRET_KEY] == ""
    assert stored[main.MFA_ENABLED_KEY] == "1"

    assert main.security_settings()["session_ttl_minutes"] == 30
    main.api_update_security_settings({"session_ttl_minutes": 60})
    assert stored["session_ttl_minutes"] == "10080"
    assert stored["dingtalk_keyword"] == "保留关键字"


def test_mfa_disable_and_qr_require_current_code(monkeypatch):
    _, main, _, utils = _load_modules()
    stored = {main.MFA_ENABLED_KEY: "1", main.MFA_SECRET_KEY: "old-secret"}
    monkeypatch.setattr(main, "get_system_setting", lambda key, default="": stored.get(key, default))
    monkeypatch.setattr(main, "set_system_setting", lambda key, value: stored.__setitem__(key, value))
    monkeypatch.setattr(main, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(main, "verify_totp_code", lambda secret, code, **_: secret == "old-secret" and code == "654321")
    monkeypatch.setattr(main, "build_totp_uri", lambda *_: "otpauth://test")

    with pytest.raises(HTTPException, match="当前 MFA 验证码不正确"):
        main.api_update_security_settings({"mfa_enabled": False})
    assert stored[main.MFA_ENABLED_KEY] == "1"
    with pytest.raises(HTTPException, match="当前 MFA 验证码不正确"):
        main.api_get_current_mfa_qr({})
    assert main.api_get_current_mfa_qr({"mfa_current_code": "654321"})["mfa_secret"] == "old-secret"
    main.api_update_security_settings({"mfa_enabled": False, "mfa_current_code": "654321"})
    assert stored[main.MFA_ENABLED_KEY] == "0"


def test_totp_rejects_replay_store_failure(monkeypatch):
    db, _, _, utils = _load_modules()
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database unavailable")))
    secret = "JBSWY3DPEHPK3PXP"
    assert not utils.verify_totp_code(secret, utils.get_totp_code(secret))
    assert not utils.verify_totp_code(secret, "12345")


def test_session_lifetime_is_fixed_at_login_and_tokens_are_unique(monkeypatch):
    _load_modules()
    from app import auth

    now = [1_000_000]
    lifetime = [300]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    monkeypatch.setattr(auth, "get_session_ttl_seconds", lambda: lifetime[0])
    monkeypatch.setattr(auth, "is_token_revoked", lambda _: False)

    first = auth.create_token("admin")
    second = auth.create_token("admin")
    assert first != second
    legacy_payload = f"admin:{now[0]}"
    legacy_token = f"{legacy_payload}:{auth._sign(legacy_payload, auth._session_signing_extra())}"
    assert auth.verify_token(legacy_token)
    now[0] += 299
    assert auth.verify_token(first)
    now[0] += 2
    lifetime[0] = 3600
    assert not auth.verify_token(first)

    longer = auth.create_token("admin")
    now[0] += 1800
    assert auth.verify_token(longer)
    lifetime[0] = 300
    assert not auth.verify_token(longer)


def test_revoked_token_remains_blocked_after_lifetime_increase(monkeypatch):
    db, _, _, _ = _load_modules()
    from app import auth

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE token_blacklist (token TEXT PRIMARY KEY, expired_at REAL)")
    monkeypatch.setattr(db, "connect", lambda: connection)
    now = [1_000_000]
    lifetime = [300]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    monkeypatch.setattr(auth, "get_session_ttl_seconds", lambda: lifetime[0])
    try:
        token = auth.create_token("admin")
        auth.revoke_token(token)
        assert connection.execute("SELECT expired_at FROM token_blacklist WHERE token = ?", (token,)).fetchone()[0] == now[0] + auth.MAX_SESSION_TTL_SECONDS
        now[0] += 600
        lifetime[0] = 3600
        assert not auth.verify_token(token)
    finally:
        connection.close()


def test_idle_session_only_renews_on_explicit_activity(monkeypatch):
    db, _, _, _ = _load_modules()
    from app import auth

    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE token_blacklist (token TEXT PRIMARY KEY, expired_at REAL);
        CREATE TABLE auth_sessions (token_hash TEXT PRIMARY KEY, last_activity REAL NOT NULL);
    """)
    monkeypatch.setattr(db, "connect", lambda: connection)
    now = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    monkeypatch.setattr(auth, "get_session_ttl_seconds", lambda: 300)
    try:
        token = auth.create_idle_token("admin")
        assert auth.verify_token(token)
        now[0] += 299
        assert auth.verify_token(token)  # Read-only requests do not extend the session.
        assert auth.renew_idle_session(token)
        now[0] += 299
        assert auth.verify_token(token)
        now[0] += 2
        assert not auth.verify_token(token)
        assert not auth.renew_idle_session(token)

        other = auth.create_idle_token("admin")
        assert auth.verify_token(other)
        auth.revoke_token(other)
        assert not auth.verify_token(other)
    finally:
        connection.close()


def test_renew_endpoint_refreshes_cookies_and_migrates_legacy_token(monkeypatch):
    db, main, _, _ = _load_modules()
    from app import auth

    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE token_blacklist (token TEXT PRIMARY KEY, expired_at REAL);
        CREATE TABLE auth_sessions (token_hash TEXT PRIMARY KEY, last_activity REAL NOT NULL);
    """)
    monkeypatch.setattr(db, "connect", lambda: connection)
    monkeypatch.setattr(auth.time, "time", lambda: 1_000_000.0)
    monkeypatch.setattr(auth, "get_session_ttl_seconds", lambda: 300)
    monkeypatch.setattr(main, "get_session_ttl_seconds", lambda: 300)
    monkeypatch.setattr(main, "cookie_path", lambda: "/ops")
    try:
        legacy = auth.create_token("admin")
        cookie_header = f"{auth.COOKIE_NAME}={legacy}; csrf_token=sample-csrf".encode()
        request = Request({
            "type": "http", "method": "POST", "scheme": "http",
            "path": "/api/session/renew", "query_string": b"",
            "headers": [(b"cookie", cookie_header)], "server": ("testserver", 80),
        })
        response = Response()
        result = main.api_renew_session(request, response)
        auth_cookie = next(value.decode() for key, value in response.raw_headers if key == b"set-cookie" and value.startswith(b"inspection_auth="))
        renewed_token = auth_cookie.split(";", 1)[0].split("=", 1)[1]
        assert result["idle_timeout_seconds"] == 300
        assert len(renewed_token.split(":")) == 6
        assert "Max-Age=300" in auth_cookie
        assert auth.verify_token(renewed_token)
        assert any(key == b"set-cookie" and b"csrf_token=sample-csrf" in value for key, value in response.raw_headers)
    finally:
        connection.close()


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
