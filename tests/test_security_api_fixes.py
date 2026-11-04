from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from pathlib import Path
from threading import Barrier
import shutil
import sqlite3
import sys
import types

import pytest
from fastapi import HTTPException


if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import db, main, utils


def test_legacy_full_reset_is_disabled() -> None:
    with pytest.raises(HTTPException) as caught:
        main.reset_database()
    assert caught.value.status_code == 410


def test_plain_database_migration_restores_original_after_final_verification_failure(tmp_path, monkeypatch) -> None:
    database = tmp_path / "app.db"
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
            connection.execute("INSERT INTO sample VALUES ('original')")
    monkeypatch.setattr(db.settings, "db_path", database)

    class ExportConnection:
        def execute(self, statement: str):
            if statement.startswith("SELECT sqlcipher_export"):
                exported = Path(str(database) + ".encrypted_tmp")
                shutil.copy2(database, exported)
                with closing(sqlite3.connect(exported)) as connection:
                    with connection:
                        connection.execute("UPDATE sample SET value = 'exported'")
            return self

        def close(self) -> None:
            pass

    original_opens = 0

    def fake_connect(path: str, **kwargs):
        nonlocal original_opens
        if Path(path) == database:
            original_opens += 1
            if original_opens == 1:
                return ExportConnection()
            raise RuntimeError("post-swap verification failed")
        return sqlite3.connect(path, **kwargs)

    monkeypatch.setattr(db, "sqlite3", types.SimpleNamespace(connect=fake_connect))
    with pytest.raises(RuntimeError, match="明文备份保留"):
        db._migrate_plain_database()

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "original"
    backup = Path(str(database) + ".backup_before_encrypt")
    assert backup.is_file()
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "original"
    assert not Path(str(database) + ".encrypted_tmp").exists()


def test_totp_same_code_cannot_pass_concurrently(tmp_path, monkeypatch) -> None:
    database = tmp_path / "totp.db"
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute("CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

    @contextmanager
    def connect():
        connection = sqlite3.connect(database, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    monkeypatch.setattr(db, "connect", connect)
    fixed_time = 1_000_000
    monkeypatch.setattr(utils, "time", types.SimpleNamespace(time=lambda: fixed_time))
    secret = "JBSWY3DPEHPK3PXP"
    code = utils.get_totp_code(secret, fixed_time)
    gate = Barrier(2)

    def verify() -> bool:
        gate.wait()
        return utils.verify_totp_code(secret, code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(verify), pool.submit(verify)]]
    assert sorted(results) == [False, True]


@pytest.mark.parametrize(
    ("create_route", "update_route", "save_name", "get_name", "payload"),
    [
        ("api_create_auth_profile", "api_update_auth_profile", "save_auth_profile", "get_auth_profile", {"name": "重复连接"}),
        ("api_create_dingtalk_profile", "api_update_dingtalk_profile", "save_dingtalk_profile", "get_dingtalk_profile", {"name": "重复机器人", "webhook": "https://example.com"}),
    ],
)
def test_duplicate_profile_names_return_conflict(monkeypatch, create_route, update_route, save_name, get_name, payload) -> None:
    def duplicate(*_args):
        raise db.sqlite3.IntegrityError("UNIQUE constraint failed: profiles.name")

    monkeypatch.setattr(main, save_name, duplicate)
    monkeypatch.setattr(main, get_name, lambda _id: {"id": 1, "name": "已有配置", "webhook": "https://example.com"})
    for route, args in ((create_route, (payload,)), (update_route, (1, payload))):
        with pytest.raises(HTTPException) as caught:
            getattr(main, route)(*args)
        assert caught.value.status_code == 409
        assert "名称" in str(caught.value.detail)
