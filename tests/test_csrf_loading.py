from __future__ import annotations

import sqlite3
import sys
import types
from contextlib import contextmanager

from fastapi import Request, Response


if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import access_context, db, main


def _request(method: str, cookie: str = "") -> Request:
    headers = [(b"host", b"testserver"), (b"x-forwarded-proto", b"https")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if method == "POST":
        headers.append((b"origin", b"http://testserver"))
    return Request({
        "type": "http", "method": method, "scheme": "http",
        "path": "/ops/api/session/renew", "query_string": b"",
        "headers": headers, "server": ("testserver", 80),
        "client": ("127.0.0.1", 10000),
    })


def _run_middleware(request: Request, handle) -> Response:
    # These handlers complete synchronously, so no Windows event-loop socket is needed.
    coroutine = main.csrf_protect_middleware(request, handle)
    try:
        coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    raise AssertionError("middleware unexpectedly suspended")


def test_missing_csrf_cookie_is_reissued_without_secure_flag_on_http(monkeypatch):
    monkeypatch.setattr(main, "routing_state", lambda: ("initialized", "/ops"))
    monkeypatch.setattr(main, "verify_token", lambda token: token == "valid")
    monkeypatch.setattr(main, "cookie_path", lambda: "/ops")

    async def handle(_request):
        return Response(status_code=200)

    response = _run_middleware(_request("GET", "inspection_auth=valid"), handle)
    cookie = response.headers["set-cookie"]
    assert "csrf_token=" in cookie and "Path=/ops" in cookie
    assert "secure" not in cookie.lower()


def test_existing_csrf_cookie_does_not_repeat_session_verification(monkeypatch):
    monkeypatch.setattr(main, "routing_state", lambda: ("initialized", "/ops"))
    monkeypatch.setattr(main, "verify_token", lambda _token: (_ for _ in ()).throw(AssertionError("redundant verification")))

    async def handle(_request):
        return Response(status_code=200)

    response = _run_middleware(
        _request("GET", "inspection_auth=valid; csrf_token=present"), handle,
    )
    assert response.status_code == 200
    assert "set-cookie" not in response.headers


def test_missing_csrf_cookie_still_blocks_write(monkeypatch):
    monkeypatch.setattr(main, "routing_state", lambda: ("initialized", "/ops"))

    async def handle(_request):
        raise AssertionError("write handler must not run")

    response = _run_middleware(_request("POST", "inspection_auth=valid"), handle)
    assert response.status_code == 403
    assert b"Missing CSRF token in cookie" in response.body


def test_routing_state_reads_installation_and_context_in_one_connection(tmp_path, monkeypatch):
    path = tmp_path / "routing.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE installation_state (id INTEGER PRIMARY KEY, state TEXT)")
        connection.execute("CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO installation_state VALUES (1, 'initialized')")
        connection.execute("INSERT INTO system_settings VALUES ('access_context', '/custom')")

    opened = 0

    @contextmanager
    def connect():
        nonlocal opened
        opened += 1
        with sqlite3.connect(path) as connection:
            yield connection

    monkeypatch.setattr(db, "connect", connect)
    assert access_context.routing_state() == ("initialized", "/custom")
    assert opened == 1
