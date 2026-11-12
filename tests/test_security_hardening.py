import base64
import hashlib
import hmac
import sqlite3
import sys
import types

import pytest
from fastapi import Request


if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import auth, db, login_rate_limit, main, screenshot, utils
from app.settings import Settings


def _legacy_cipher(plain: str) -> str:
    nonce = b"0123456789abcdef"
    body = nonce + utils._xor_bytes(plain.encode(), utils._keystream(nonce, len(plain)))
    mac = hmac.new(utils._secret_master_key(), b"v1:" + body, hashlib.sha256).digest()
    return "v1:" + base64.urlsafe_b64encode(body + mac).decode()


def test_new_secrets_use_fernet_and_legacy_values_upgrade():
    old = _legacy_cipher("stored-password")
    assert utils.decrypt_secret(old) == "stored-password"
    upgraded = utils.normalize_secret_for_storage(old)
    assert upgraded.startswith("fernet:")
    assert utils.decrypt_secret(upgraded) == "stored-password"
    assert utils.normalize_secret_for_storage(upgraded) == upgraded
    with pytest.raises(ValueError, match="cannot be decrypted"):
        utils.normalize_secret_for_storage("v1:invalid")


def test_database_migrates_all_secret_locations():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE auth_profiles (id INTEGER, password TEXT, password_secret TEXT, updated_at TEXT);
        CREATE TABLE mail_profiles (id INTEGER, password TEXT, password_secret TEXT, updated_at TEXT);
        CREATE TABLE report_jobs (id INTEGER, dingtalk_webhook TEXT, dingtalk_secret TEXT, updated_at TEXT);
        CREATE TABLE dingtalk_profiles (id INTEGER, webhook TEXT, secret TEXT);
        CREATE TABLE periodic_report_settings (id INTEGER, dingtalk_webhook TEXT, dingtalk_secret TEXT);
        CREATE TABLE system_settings (key TEXT, value TEXT);
    """)
    old = _legacy_cipher("secret")
    conn.execute("INSERT INTO auth_profiles VALUES (1, '', ?, '')", (old,))
    conn.execute("INSERT INTO mail_profiles VALUES (1, '', ?, '')", (old,))
    conn.execute("INSERT INTO report_jobs VALUES (1, ?, ?, '')", (old, old))
    conn.execute("INSERT INTO dingtalk_profiles VALUES (1, ?, ?)", (old, old))
    conn.execute("INSERT INTO periodic_report_settings VALUES (1, ?, ?)", (old, old))
    conn.execute("INSERT INTO system_settings VALUES ('mfa_totp_secret', ?)", (old,))
    db.migrate_sensitive_values(conn)
    for table, column in (
        ("auth_profiles", "password_secret"), ("mail_profiles", "password_secret"),
        ("report_jobs", "dingtalk_secret"), ("dingtalk_profiles", "secret"),
        ("periodic_report_settings", "dingtalk_secret"), ("system_settings", "value"),
    ):
        value = conn.execute(f"SELECT {column} FROM {table}").fetchone()[0]
        assert value.startswith("fernet:")
        assert utils.decrypt_secret(value) == "secret"
    conn.close()


def test_browser_state_cache_upgrades_when_loaded(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(_legacy_cipher('{"cookies": [], "origins": []}'))
    monkeypatch.setattr(screenshot, "_storage_state_path", lambda _: state_path)
    browser = types.SimpleNamespace(new_context=lambda **kwargs: kwargs)
    context = screenshot._new_context(browser, {"id": 1}, 800, 600)
    assert context["storage_state"] == {"cookies": [], "origins": []}
    assert state_path.read_text().startswith("fernet:")


def test_login_attempt_limit_is_atomic_and_resets_on_success(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE login_attempts (
        client_key TEXT PRIMARY KEY, window_start REAL, attempts INTEGER, blocked_until REAL)""")
    monkeypatch.setattr(login_rate_limit, "connect", lambda: conn)
    now = [1000.0]
    monkeypatch.setattr(login_rate_limit.time, "time", lambda: now[0])
    for _ in range(login_rate_limit.MAX_ATTEMPTS):
        assert login_rate_limit.consume_login_attempt("127.0.0.1") == 0
    assert login_rate_limit.consume_login_attempt("127.0.0.1") == login_rate_limit.BLOCK_SECONDS
    assert login_rate_limit.consume_login_attempt("127.0.0.1") == login_rate_limit.BLOCK_SECONDS
    assert login_rate_limit.consume_login_attempt("127.0.0.2") == 0
    now[0] += login_rate_limit.BLOCK_SECONDS + 1
    assert login_rate_limit.consume_login_attempt("127.0.0.1") == 0
    login_rate_limit.clear_login_attempts("127.0.0.1")
    assert conn.execute("SELECT COUNT(*) FROM login_attempts WHERE client_key = ?",
                        (login_rate_limit._client_key("127.0.0.1"),)).fetchone()[0] == 0
    conn.close()


def test_login_rejects_blocked_request_before_decrypt(monkeypatch):
    monkeypatch.setattr(main, "installation_state", lambda: "initialized")
    monkeypatch.setattr(main, "public_base_path", lambda: "/ops")
    monkeypatch.setattr(main, "consume_login_attempt", lambda _: 123)
    monkeypatch.setattr(main, "decrypt_login_payload", lambda _: pytest.fail("decryption reached"))
    request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/ops/login",
                       "query_string": b"", "headers": [], "server": ("testserver", 80),
                       "client": ("127.0.0.1", 10000)})
    response = main.login(request, "anything")
    assert response.headers["Retry-After"] == "123"
    assert "error=rate" in response.headers["Location"]


def test_admin_credentials_are_read_from_database(monkeypatch):
    encoded = auth.hash_admin_password("different-secret")
    monkeypatch.setattr(auth, "admin_account", lambda: ("admin", encoded))
    monkeypatch.setattr(auth, "is_token_revoked", lambda _: False)
    assert auth.check_password("admin", "different-secret")
    assert not auth.check_password("admin", "admin")
    assert not auth.check_password("wrong", "different-secret")
    token = auth.create_token("admin")
    assert auth.verify_token(token)


def test_empty_master_key_file_fails_closed(tmp_path, monkeypatch):
    key_path = tmp_path / "app-secret.key"
    key_path.write_text("")
    instance = object.__new__(Settings)
    instance.data_dir = tmp_path
    with pytest.raises(RuntimeError, match="empty"):
        instance._load_or_create_secret_key()
