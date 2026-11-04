import sqlite3
import sys
import types
import pytest

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import kibana_connections


def test_search_kibana_dashboards_uses_connection_and_space(monkeypatch):
    requested = []

    class Response:
        status = 200
        ok = True

        def json(self):
            return {"saved_objects": [{"id": "abc-123", "attributes": {"title": "生产监控"}}]}

    class Request:
        def get(self, url, timeout):
            requested.append((url, timeout))
            return Response()

    monkeypatch.setattr(kibana_connections, "_with_login", lambda _profile, fetch: fetch(types.SimpleNamespace(request=Request()), None))
    result = kibana_connections.search_dashboards({"login_url": "https://elastic.example/s/prod/login"}, "生产")
    assert result["dashboards"] == [{"uid": "abc-123", "title": "生产监控", "url": "https://elastic.example/s/prod/app/dashboards#/view/abc-123", "folder": "Kibana 仪表盘"}]
    assert requested[0][0].startswith("https://elastic.example/s/prod/api/saved_objects/_find?")
    assert "type=dashboard" in requested[0][0]


def test_login_redirected_kibana_search_requests_relogin(monkeypatch):
    class Response:
        status = 200
        ok = True
        url = "https://elastic.example/s/prod/login"
        headers = {"content-type": "text/html; charset=utf-8"}

        def json(self):
            raise AssertionError("Login HTML must not be parsed as dashboard JSON")

    request = types.SimpleNamespace(get=lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(kibana_connections, "_with_login", lambda _profile, fetch: fetch(types.SimpleNamespace(request=request), None))
    with pytest.raises(kibana_connections._ExpiredSession):
        kibana_connections.search_dashboards({"login_url": "https://elastic.example/s/prod/login"})
