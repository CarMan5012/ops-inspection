import sqlite3
import sys
import types

import pytest


def test_database_connection_commits_and_closes(monkeypatch, tmp_path):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import db

    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "test.db")
    with db.connect() as conn:
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample VALUES ('saved')")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    with sqlite3.connect(tmp_path / "test.db") as verify:
        assert verify.execute("SELECT value FROM sample").fetchone()[0] == "saved"


def test_screenshot_duration_recorded(monkeypatch, tmp_path):
    if "pysqlcipher3" not in sys.modules:
        sqlcipher = types.ModuleType("pysqlcipher3")
        sqlcipher.dbapi2 = sqlite3
        sys.modules["pysqlcipher3"] = sqlcipher
    from app import db, repository

    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(repository, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "test.db")
    db.init_db()

    run_id = repository.create_run(None, "测试任务")
    repository.add_screenshot_result({
        "run_id": run_id,
        "item_id": None,
        "item_name": "测试项",
        "section": "章节",
        "status": "success",
        "file_path": "/path/to/img.png",
        "error_message": "",
        "duration_seconds": 3.45,
    })
    results = repository.list_screenshot_results(run_id)
    assert len(results) == 1
    assert results[0]["duration_seconds"] == 3.45
