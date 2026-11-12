import sqlite3
import sys
import types

import pytest
from fastapi import HTTPException, Request


if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import auth, db, installation, main, repository


def _tables(conn):
    conn.executescript("""
        CREATE TABLE installation_state (id INTEGER PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE admin_account (id INTEGER PRIMARY KEY, username TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE auth_sessions (token_hash TEXT PRIMARY KEY, last_activity REAL NOT NULL);
        CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)


def test_new_database_starts_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, "data_dir", tmp_path)
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "app.db")
    monkeypatch.setattr(db.settings, "ensure_dirs", lambda: tmp_path.mkdir(exist_ok=True))
    monkeypatch.setattr(db, "_migrate_plain_database", lambda: None)
    db.init_db()
    assert installation.installation_state() == "pending"
    assert installation.admin_account() is None
    db.init_db()
    assert installation.installation_state() == "pending"


def test_first_run_setup_is_one_time_and_password_change_revokes_sessions(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    _tables(conn)
    conn.execute("INSERT INTO installation_state VALUES (1, 'pending')")
    conn.commit()
    monkeypatch.setattr(installation, "connect", lambda: conn)
    monkeypatch.setattr(repository, "connect", lambda: conn)
    monkeypatch.setattr(installation.settings, "data_dir", tmp_path)
    monkeypatch.setattr(auth, "is_token_revoked", lambda _: False)
    try:
        assert not auth.check_password("admin", "admin")
        code = installation.ensure_setup_code()
        assert code and len(code) >= 24
        assert not installation.complete_setup("admin", "long-password-123", "wrong")
        assert installation.complete_setup("admin", "long-password-123", code,
                                           preferences={"app_name": "巡检中心", "access_context": ""})
        assert installation.installation_state() == "initialized"
        assert dict(conn.execute("SELECT key, value FROM system_settings").fetchall()) == {
            "app_name": "巡检中心", "access_context": ""}
        assert not installation.ensure_setup_code()
        assert not installation.complete_setup("attacker", "long-password-123", code)
        assert not (tmp_path / "setup-code").exists()
        assert auth.check_password("admin", "long-password-123")
        token = auth.create_token("admin")
        assert auth.verify_token(token)
        assert not installation.change_admin_password("wrong", "next-password-123")
        assert installation.change_admin_password("long-password-123", "next-password-123")
        assert not auth.verify_token(token)
        assert not auth.check_password("admin", "long-password-123")
        assert auth.check_password("admin", "next-password-123")
        assert not installation.change_admin_username("wrong", "operator")
        assert installation.change_admin_username("next-password-123", "operator")
        assert not auth.check_password("admin", "next-password-123")
        assert auth.check_password("operator", "next-password-123")
    finally:
        conn.close()


def test_old_database_without_installation_record_is_rejected():
    conn = sqlite3.connect(":memory:")
    _tables(conn)
    try:
        with pytest.raises(RuntimeError, match="旧数据库"):
            db._initialize_installation_state(conn, existing_database=True)
        assert conn.execute("SELECT COUNT(*) FROM installation_state").fetchone()[0] == 0
    finally:
        conn.close()


def test_setup_routes_redirect_and_close_after_creation(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _tables(conn)
    conn.execute("INSERT INTO installation_state VALUES (1, 'pending')")
    conn.commit()
    monkeypatch.setattr(installation, "connect", lambda: conn)
    monkeypatch.setattr(repository, "connect", lambda: conn)
    monkeypatch.setattr(installation.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main, "consume_login_attempt", lambda _: 0)
    monkeypatch.setattr(main, "clear_login_attempts", lambda _: None)
    code = installation.ensure_setup_code()
    request = Request({"type": "http", "method": "GET", "scheme": "http",
                       "path": "/ops/setup", "query_string": b"", "headers": [],
                       "server": ("testserver", 80), "client": ("127.0.0.1", 10000)})
    try:
        with pytest.raises(HTTPException) as denied:
            auth.require_login(request)
        assert denied.value.headers["Location"] == "/setup"
        response = main.login_page(request)
        assert response.status_code == 303
        assert response.headers["location"] == "/setup"
        page = main.setup_page(request)
        assert page.status_code == 200
        assert b"csrf_token=" in page.headers["set-cookie"].encode()
        bad = main.setup_submit(request, "admin", "long-password-123", "long-password-123", "wrong",
                                "运维巡检平台", "", "", "")
        assert bad.headers["location"] == "/setup?error=code"
        created = main.setup_submit(request, "admin", "long-password-123", "long-password-123", code,
                                    "运维巡检平台", "", "", "")
        assert created.status_code == 303 and created.headers["location"] == "/login"
        assert main.setup_page(request).headers["location"] == "/login"
    finally:
        conn.close()
