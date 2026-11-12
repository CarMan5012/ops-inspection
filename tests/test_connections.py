"""Connection selection and batch item behavior without live Grafana access."""

import sqlite3
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app.grafana_connections import ConnectionError, _rendered_dashboard_options, _rendered_time_range, check_target, dashboard_settings, grafana_base_url, search_dashboards
from app.repository import _SCREENSHOT_ITEM_FIELDS, save_screenshot_items_batch
from app.screenshot import _storage_state_path


def test_grafana_base_and_profile_cache_are_account_scoped():
    first = {"id": 11, "name": "生产", "auth_type": "form", "login_url": "https://example.com/grafana/login"}
    second = {**first, "id": 12}
    assert grafana_base_url(first) == "https://example.com/grafana"
    assert _storage_state_path(first) != _storage_state_path(second)


def test_dashboard_search_keeps_reverse_proxy_prefix():
    profile = {"id": 1, "login_url": "https://example.com/grafana/login"}
    requested = []

    class Response:
        status = 200
        ok = True

        def json(self):
            return [{"uid": "prod", "title": "生产看板", "url": "/d/prod/overview", "folderTitle": "生产"}]

    class Request:
        def get(self, url, timeout):
            requested.append(url)
            return Response()

    with patch(
        "app.grafana_connections._with_login",
        lambda _profile, action: action(SimpleNamespace(request=Request()), None),
    ):
        result = search_dashboards(profile, "生产")
    assert result["dashboards"][0]["url"] == "https://example.com/grafana/d/prod/overview"
    assert "query=%E7%94%9F%E4%BA%A7" in requested[0]


def test_check_target_passes_rendered_grafana_datasource_to_monitoring():
    url = "http://grafana:3000/d/prod/overview"
    events = {}
    runtime_keys = []

    class Page:
        def __init__(self):
            self.url = url

        def on(self, event, callback):
            events[event] = callback

        def goto(self, target, **_kwargs):
            assert target == url
            events["request"](SimpleNamespace(
                url="http://grafana:3000/api/ds/query",
                post_data_json={"queries": [{"datasource": {"uid": "prod-uid"}}]},
            ))
            return SimpleNamespace(status=200)

    class Request:
        def get(self, _url, **_kwargs):
            return SimpleNamespace(status=200, ok=True)

    with patch("app.grafana_connections._with_login", lambda _profile, action: action(SimpleNamespace(request=Request()), Page())), \
         patch("app.grafana_connections._is_on_login_page", return_value=False), \
         patch("app.grafana_connections.collect_dashboard_alerts", side_effect=lambda _context, _url, runtime_datasource_keys: (
             runtime_keys.append(runtime_datasource_keys.copy()) or {"status": "normal", "error": ""}
         )):
        result = check_target(url, None)

    assert result["ok"] is True
    assert runtime_keys == [{"prod-uid"}]


def test_dashboard_settings_uses_dashboard_metadata_and_rendered_options():
    profile = {"id": 1, "login_url": "https://example.com/grafana/login"}
    requested = []

    class Response:
        status = 200
        ok = True

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    class Request:
        def get(self, url, timeout):
            requested.append(url)
            return Response({"dashboard": {
                "title": "Kubernetes", "time": {"from": "now-6h", "to": "now"}, "refresh": "30s",
                "timepicker": {"quick_ranges": [{"display": "最近 6 小时", "from": "now-6h", "to": "now"}],
                               "refresh_intervals": ["30s", "1m"]},
                "templating": {"list": [
                    {"name": "DS", "label": "数据源", "type": "datasource", "query": "prometheus", "current": {"value": "prometheus"}},
                    {"name": "namespace", "label": "命名空间", "type": "query", "multi": True,
                     "current": {"value": ["default"]}, "options": [{"text": "default", "value": "default"}]},
                    {"name": "hidden", "type": "constant", "current": {"value": "internal"}},
                ]},
            }})

    rendered = {
        "DS": [{"label": "prometheus", "value": "prometheus"}],
        "namespace": [{"label": "default", "value": "default"}],
    }
    with patch("app.grafana_connections._with_login", lambda _profile, action: action(SimpleNamespace(request=Request()), object())), \
         patch("app.grafana_connections._rendered_dashboard_options", return_value=rendered):
        result = dashboard_settings(profile, "k8s")
    assert requested[0] == "https://example.com/grafana/api/dashboards/uid/k8s"
    assert [row["options"] for row in result["variables"]] == list(rendered.values())
    assert result["variables"][1]["default"] == ["default"]
    assert result["variables"][1]["multi"] is True
    assert len(result["variables"]) == 2
    assert result["time"] == {"from": "now-6h", "to": "now"}
    assert result["time_choices"] == [{"label": "最近 6 小时", "from": "now-6h", "to": "now"}]
    assert result["refresh_choices"] == ["30s", "1m"]


def test_grafana_time_menu_labels_keep_their_original_url_ranges():
    assert _rendered_time_range("最近 1 个小时") == {
        "label": "最近 1 个小时", "from": "now-1h", "to": "now",
    }
    assert _rendered_time_range("Last 6 hours") == {
        "label": "Last 6 hours", "from": "now-6h", "to": "now",
    }
    assert _rendered_time_range("上周") == {
        "label": "上周", "from": "now-1w/w", "to": "now-1w/w",
    }
    assert _rendered_time_range("任意输入") is None


def test_dashboard_settings_uses_rendered_grafana_options_for_every_variable():
    profile = {"id": 1, "login_url": "https://grafana.example.com/login"}
    page = object()
    requested = []

    class Request:
        def get(self, url, timeout):
            requested.append(url)
            if url.endswith("/api/dashboards/uid/k8s"):
                data = {"dashboard": {"templating": {"list": [
                    {"name": "DS", "label": "Prometheus 数据源", "type": "datasource", "query": "prometheus"},
                    {"name": "workload", "label": "工作负载", "type": "query", "includeAll": True,
                     "options": [{"text": "All", "value": "$__all"}]},
                    {"name": "namespace", "label": "命名空间", "type": "query", "includeAll": True,
                     "options": [{"text": "All", "value": "$__all"}]},
                ]}}}
            elif url.endswith("/api/datasources"):
                data = [{"name": "prometheus", "uid": "prod-uid"}]
            else:
                raise AssertionError(f"Unexpected fallback request: {url}")
            return SimpleNamespace(status=200, ok=True, json=lambda: data)

    rendered = {
        "DS": [{"label": "prometheus", "value": "prometheus"}],
        "workload": [{"label": "All", "value": "$__all"}, {"label": "api", "value": "api"}],
        "namespace": [{"label": "All", "value": "$__all"},
                      {"label": "default", "value": "default"}, {"label": "kuboard", "value": "kuboard"}],
    }
    source_maps = []
    with patch("app.grafana_connections._with_login", lambda _profile, action: action(SimpleNamespace(request=Request()), page)), \
         patch("app.grafana_connections._rendered_dashboard_options",
               side_effect=lambda _page, _url, variables: source_maps.append(variables[0]["value_map"].copy()) or rendered) as read_rendered:
        result = dashboard_settings(profile, "k8s", "?var-namespace=kuboard")
    values = {variable["name"]: variable["options"] for variable in result["variables"]}
    assert values == rendered
    assert read_rendered.call_args.args[1] == "https://grafana.example.com/d/k8s?var-namespace=kuboard"
    assert source_maps[0]["prometheus"] == "prod-uid"
    assert requested == ["https://grafana.example.com/api/dashboards/uid/k8s",
                         "https://grafana.example.com/api/datasources"]


def test_dashboard_settings_rejects_missing_rendered_options_instead_of_using_saved_options():
    profile = {"id": 1, "login_url": "https://grafana.example.com/login"}

    class Request:
        def get(self, url, timeout):
            assert url.endswith("/api/dashboards/uid/k8s")
            data = {"dashboard": {"templating": {"list": [{
                "name": "namespace", "label": "命名空间", "type": "query",
                "options": [{"text": "All", "value": "$__all"}, {"text": "stale", "value": "stale"}],
            }]}}}
            return SimpleNamespace(status=200, ok=True, json=lambda: data)

    with patch("app.grafana_connections._with_login", lambda _profile, action: action(SimpleNamespace(request=Request()), object())), \
         patch("app.grafana_connections._rendered_dashboard_options", return_value={}):
        try:
            dashboard_settings(profile, "k8s")
        except ConnectionError as exc:
            assert "命名空间" in str(exc)
        else:
            raise AssertionError("Missing runtime choices must not use saved dashboard options")


def test_dashboard_settings_does_not_require_datasource_menu_for_other_variables():
    profile = {"id": 1, "login_url": "https://grafana.example.com/login"}

    class Request:
        def get(self, url, timeout):
            assert url.endswith("/api/dashboards/uid/k8s")
            data = {"dashboard": {"templating": {"list": [
                {"name": "DS", "label": "Prometheus 数据源", "type": "datasource",
                 "current": {"value": "prometheus"}},
                {"name": "namespace", "label": "命名空间", "type": "query", "includeAll": True},
            ]}}}
            return SimpleNamespace(status=200, ok=True, json=lambda: data)

    with patch("app.grafana_connections._with_login", lambda _profile, action: action(SimpleNamespace(request=Request()), object())), \
         patch("app.grafana_connections._rendered_dashboard_options", return_value={
             "namespace": [{"label": "All", "value": "$__all"}, {"label": "kuboard", "value": "kuboard"}],
         }):
        result = dashboard_settings(profile, "k8s", "?var-DS=prometheus")
    assert [row["name"] for row in result["variables"]] == ["namespace"]
    assert result["variables"][0]["options"][1]["value"] == "kuboard"


def test_rendered_grafana_options_works_without_test_ids():
    clicked = []
    class EmptyLocator:
        def all(self):
            return []

    class LabelNode:
        def is_visible(self):
            return True

        def evaluate(self, _script, _marker):
            return {"found": True, "selected": "All ×"}

    class LabelLocator:
        def all(self):
            return [LabelNode()]

    class Page:
        url = "http://grafana/d/k8s"
        keyboard = SimpleNamespace(press=lambda key: None)

        def goto(self, url, **_kwargs):
            self.url = url

        def wait_for_function(self, _script, **_kwargs):
            return None

        def get_by_test_id(self, _name):
            return EmptyLocator()

        def get_by_text(self, _text, exact):
            return LabelLocator()

        def locator(self, _selector):
            return SimpleNamespace(click=lambda **_kwargs: clicked.append(_selector))

        def evaluate(self, _script):
            return {"open": True, "texts": ["All", "default", "kuboard"], "moved": False}

        def wait_for_timeout(self, _milliseconds):
            return None

    variable = {
        "name": "namespace", "label": "命名空间", "type": "query", "include_all": True,
        "options": [], "value_map": {"All": "stale-all"}, "default": ["stale"],
    }
    result = _rendered_dashboard_options(Page(), "http://grafana/d/k8s", [variable])
    assert result["namespace"] == [
        {"label": "All", "value": "$__all"},
        {"label": "default", "value": "default"},
        {"label": "kuboard", "value": "kuboard"},
    ]
    assert variable["default"] == ["$__all"]
    assert clicked == ['[data-inspection-variable-trigger="0"]']


def test_rendered_variables_keep_visible_current_value_when_menu_has_no_options():
    class Locator:
        def all(self):
            return []

    class Label:
        selected = ""

        def is_visible(self):
            return True

        def evaluate(self, _script, _marker):
            return {"found": True, "selected": self.selected}

    class Page:
        url = "http://grafana/d/k8s"
        keyboard = SimpleNamespace(press=lambda _key: None)

        def goto(self, url, **_kwargs):
            self.url = url

        def wait_for_function(self, *_args, **_kwargs):
            return None

        def get_by_test_id(self, _name):
            return Locator()

        def get_by_text(self, _text, exact):
            return SimpleNamespace(all=lambda: [Label()])

        def locator(self, _selector):
            return SimpleNamespace(click=lambda **_kwargs: None)

        def evaluate(self, _script):
            return {"open": False, "texts": [], "moved": False}

        def wait_for_timeout(self, _milliseconds):
            return None

    for name, kind, selected, expected in (
        ("DS", "datasource", "prometheus", "prometheus-uid"),
        ("environment", "query", "All ×", "$__all"),
    ):
        Label.selected = selected
        variable = {"name": name, "label": name, "type": kind,
                    "include_all": kind == "query", "options": [], "value_map": {}, "default": ["old"]}
        result = _rendered_dashboard_options(Page(), f"http://grafana/d/k8s?var-{name}={expected}", [variable])
        assert result == {name: [{"label": selected.removesuffix(" ×"), "value": expected}]}
        assert variable["default"] == [expected]


def test_batch_insert_rolls_back_all_items_on_failure():
    import app.repository as repository

    conn = sqlite3.connect(":memory:")
    columns = ", ".join(f"{field} TEXT" for field in _SCREENSHOT_ITEM_FIELDS if field != "name")
    conn.execute(f"CREATE TABLE screenshot_items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, {columns})")

    @contextmanager
    def fake_connect():
        with conn:
            yield conn

    valid = {field: "value" for field in _SCREENSHOT_ITEM_FIELDS}
    invalid = {**valid, "name": None}
    with patch.object(repository, "connect", fake_connect):
        try:
            save_screenshot_items_batch([valid, invalid])
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("The second invalid row should fail")
    assert conn.execute("SELECT COUNT(*) FROM screenshot_items").fetchone()[0] == 0
    conn.close()


def test_batch_endpoint_validates_then_passes_all_rows_to_one_transaction():
    from app.main import api_create_items_batch

    url = "https://grafana.example.com/d/prod/overview"
    captured = []
    with (
        patch("app.main.get_job", return_value={"id": 1}),
        patch("app.main.get_auth_profile", return_value={"id": 2}),
        patch("app.main.list_screenshot_items", return_value=[]),
        patch("app.main.save_screenshot_items_batch", side_effect=lambda rows: (captured.extend(rows), [101])[1]),
    ):
        result = api_create_items_batch(1, {"items": [{"url": url, "name": "生产", "auth_profile_id": 2}]})
    assert result["count"] == 1
    assert captured[0]["auth_profile_id"] == 2
    assert captured[0]["item_type"] == "grafana"
    assert captured[0]["section"] == "生产"

    with (
        patch("app.main.get_job", return_value={"id": 1}),
        patch("app.main.get_auth_profile", return_value={"id": 2}),
        patch("app.main.list_screenshot_items", return_value=[{"url": url, "auth_profile_id": 2}]),
    ):
        try:
            api_create_items_batch(1, {"items": [{"url": url, "name": "重复", "auth_profile_id": 2}]})
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("Duplicate URL and profile must be rejected")


def test_batch_kibana_dashboard_uses_kibana_capture_checks():
    from app.main import api_create_items_batch

    captured = []
    with (
        patch("app.main.get_job", return_value={"id": 1}),
        patch("app.main.list_screenshot_items", return_value=[]),
        patch("app.main.save_screenshot_items_batch", side_effect=lambda rows: (captured.extend(rows), [102])[1]),
    ):
        api_create_items_batch(1, {"items": [{
            "url": "https://elastic.example/s/prod/app/dashboards#/view/abc-123",
            "name": "Kibana 生产看板",
        }]})
    assert captured[0]["item_type"] == "kibana"
