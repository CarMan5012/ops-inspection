import json
import sqlite3
import sys
import types

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from docx import Document

from app.monitoring import collect_dashboard_alerts, prometheus_alert_url, summarize_monitoring
from app.screenshot import _GrafanaQueryTracker
from app import report


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.ok = status == 200

    def json(self):
        return self.payload


class _Request:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return self.responses[url]


def test_grafana_query_tracker_reads_runtime_datasource_uid():
    events = {}
    page = types.SimpleNamespace(on=lambda name, callback: events.__setitem__(name, callback))
    tracker = _GrafanaQueryTracker(page)
    query = types.SimpleNamespace(
        url="http://grafana:3000/api/ds/query",
        post_data_json={"queries": [{"datasource": {"type": "prometheus", "uid": "prod-uid"}}]},
    )
    events["request"](query)
    assert tracker.datasource_keys == {"prod-uid"}


def test_grafana_dashboard_discovers_prometheus_and_reads_existing_alerts():
    base = "http://grafana:3000"
    responses = {
        f"{base}/api/dashboards/uid/prod": _Response({
            "dashboard": {"panels": [{"datasource": {"uid": "prom-main"}}]}
        }),
        f"{base}/api/datasources": _Response([
            {"uid": "prom-main", "name": "Prometheus", "type": "prometheus", "url": "http://prometheus:9090/"},
            {"uid": "logs", "name": "Logs", "type": "loki"},
        ]),
        f"{base}/api/datasources/proxy/uid/prom-main/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [
                {"type": "alerting", "name": "CPUHigh", "health": "ok"}
            ]}]}
        }),
        f"{base}/api/datasources/proxy/uid/prom-main/api/v1/alerts": _Response({
            "status": "success", "data": {"alerts": [{
                "labels": {"alertname": "CPUHigh", "instance": "server-1", "severity": "critical", "team": "ops"},
                "annotations": {"summary": "CPU 持续过高", "description": "请检查进程"},
                "state": "firing", "value": "96", "activeAt": "2026-10-10T10:00:00Z",
            }]}
        }),
    }
    request = _Request(responses)
    context = types.SimpleNamespace(request=request)

    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview")

    assert snapshot["status"] == "alert"
    assert snapshot["rule_count"] == 1
    assert snapshot["alerts"][0]["name"] == "CPUHigh"
    assert snapshot["alerts"][0]["source"] == "Prometheus"
    assert snapshot["alerts"][0]["source_uid"] == "prom-main"
    assert snapshot["alerts"][0]["active_at"] == "2026-10-10T10:00:00Z"
    assert snapshot["alerts"][0]["labels"]["team"] == "ops"
    assert snapshot["alerts"][0]["annotations"]["description"] == "请检查进程"
    assert snapshot["sources"][0]["alert_url"] == "http://prometheus:9090/alerts"
    assert len(request.urls) == 4
    summary = summarize_monitoring([{"monitor_status": "alert", "monitor_data": json.dumps(snapshot)}])
    assert summary["status"] == "alert"
    assert "触发 1" in summary["text"]


def test_prometheus_alert_url_preserves_prefix_and_rejects_unsafe_url():
    assert prometheus_alert_url("https://metrics.example.com/prometheus/") == "https://metrics.example.com/prometheus/alerts"
    assert prometheus_alert_url("javascript:alert(1)") == ""
    assert prometheus_alert_url("https://user:pass@metrics.example.com") == ""


def test_prometheus_link_redirects_from_saved_snapshot(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "list_screenshot_results", lambda _run_id: [{
        "id": 7, "monitor_data": json.dumps({"sources": [{
            "uid": "prom-main", "alert_url": "https://metrics.example.com/alerts"
        }]})
    }])
    response = main.api_open_prometheus_alerts(11, 7, "prom-main")
    assert response.status_code == 302
    assert response.headers["location"] == "https://metrics.example.com/alerts"


def test_prometheus_link_resolves_old_snapshot_from_grafana(monkeypatch):
    from app import main
    from app import grafana_connections

    monkeypatch.setattr(main, "list_screenshot_results", lambda _run_id: [{
        "id": 7, "item_id": 3, "monitor_data": json.dumps({"sources": [{"uid": "prom-main"}]})
    }])
    monkeypatch.setattr(main, "get_screenshot_item", lambda _item_id: {
        "url": "http://grafana:3000/d/prod/overview", "auth_profile_id": None,
    })
    url = "http://grafana:3000/api/datasources/uid/prom-main"
    context = types.SimpleNamespace(request=_Request({url: _Response({
        "uid": "prom-main", "url": "https://metrics.example.com/prometheus",
    })}))
    monkeypatch.setattr(grafana_connections, "_with_login", lambda _profile, action: action(context, None))

    response = main.api_open_prometheus_alerts(11, 7, "prom-main")
    assert response.status_code == 302
    assert response.headers["location"] == "https://metrics.example.com/prometheus/alerts"


def test_same_rule_keeps_distinct_instances_in_summary():
    alerts = [
        {"source_uid": "prom-main", "source": "prometheus", "name": "Pod状态报警", "state": "firing",
         "labels": {"alertname": "Pod状态报警", "namespace": "production", "pod": "pod-a"}},
        {"source_uid": "prom-main", "source": "prometheus", "name": "Pod状态报警", "state": "firing",
         "labels": {"alertname": "Pod状态报警", "namespace": "staging", "pod": "pod-b"}},
    ]
    snapshot = {"status": "alert", "alerts": alerts}
    results = [{"monitor_status": "alert", "monitor_data": json.dumps(snapshot)}]

    summary = summarize_monitoring(results)

    assert len(summary["alerts"]) == 2
    assert "1 类" in summary["text"]
    assert "2 个实例" in summary["text"]
    assert "触发 2" in summary["text"]


def test_grafana_api_failure_is_unknown_not_normal():
    base = "http://grafana:3000"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({}, status=403)
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview")
    assert snapshot["status"] == "unknown"
    assert "403" in snapshot["error"]


def test_no_alert_rules_is_unconfigured_not_normal():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prom-main"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({
            "dashboard": {"panels": [{"datasource": {"uid": "prom-main"}}]}
        }),
        f"{base}/api/datasources": _Response([
            {"uid": "prom-main", "name": "Prometheus", "type": "prometheus"}
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": []}
        }),
        f"{proxy}/api/v1/alerts": _Response({
            "status": "success", "data": {"alerts": []}
        }),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview")
    assert snapshot["status"] == "unconfigured"


def test_non_prometheus_dashboard_does_not_raise_monitoring_alarm():
    base = "http://grafana:3000"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/demo": _Response({
            "dashboard": {"panels": [{"datasource": {"uid": "testdata"}}]}
        }),
        f"{base}/api/datasources": _Response([
            {"uid": "testdata", "name": "TestData", "type": "testdata"},
            {"uid": "prom-main", "name": "Prometheus", "type": "prometheus"},
        ]),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/demo/overview")
    assert snapshot["status"] == "not_applicable"


def test_dashboard_datasource_variable_uses_url_selection():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prom-live"
    request = _Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "templating": {"list": [{"type": "datasource", "name": "source", "current": {"value": "prom-old"}}]},
            "panels": [{"datasource": {"uid": "${source}"}}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "prom-old", "name": "Old", "type": "prometheus"},
            {"uid": "prom-live", "name": "Live", "type": "prometheus"},
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({"status": "success", "data": {"groups": [
            {"rules": [{"type": "alerting", "name": "CPUHigh"}]}
        ]}}),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    })
    snapshot = collect_dashboard_alerts(
        types.SimpleNamespace(request=request), f"{base}/d/prod/overview?var-source=prom-live"
    )
    assert snapshot["status"] == "normal"
    assert snapshot["sources"][0]["uid"] == "prom-live"
    assert not any("prom-old/api/v1" in url for url in request.urls)


def test_dashboard_auto_matches_numeric_datasource_reference():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prom-main"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({
            "dashboard": {"panels": [{"datasource": {"id": 7}}]}
        }),
        f"{base}/api/datasources": _Response([
            {"id": 7, "uid": "prom-main", "name": "Prometheus", "type": "prometheus"}
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [{"type": "alerting", "name": "CPUHigh"}]}]}
        }),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview")
    assert snapshot["status"] == "normal"
    assert snapshot["sources"][0]["uid"] == "prom-main"


def test_stale_dashboard_datasource_does_not_assume_only_prometheus_is_selected():
    base = "http://grafana:3000"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({
            "dashboard": {"panels": [{"datasource": {"uid": "old-prometheus-uid"}}]}
        }),
        f"{base}/api/datasources": _Response([
            {"id": 7, "uid": "prom-main", "name": "Prometheus", "type": "prometheus"}
        ]),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview")
    assert snapshot["status"] == "unknown"
    assert "面板引用：old-prometheus-uid" in snapshot["error"]
    assert snapshot["sources"] == []


def test_top_grafana_datasource_selection_resolves_unmatched_panel_reference():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prod-uid"
    request = _Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "templating": {"list": [{"name": "DS", "type": "datasource", "current": {"value": "lab"}}]},
            "panels": [{"datasource": {"uid": "stale-uid"}}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "lab-uid", "name": "lab", "type": "prometheus"},
            {"uid": "prod-uid", "name": "prod", "type": "prometheus"},
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [{"type": "alerting", "name": "CPUHigh"}]}]}
        }),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    })
    snapshot = collect_dashboard_alerts(
        types.SimpleNamespace(request=request), f"{base}/d/prod/overview?var-DS=prod"
    )
    assert snapshot["status"] == "normal"
    assert [source["uid"] for source in snapshot["sources"]] == ["prod-uid"]
    assert not any("lab-uid/api/v1" in url for url in request.urls)


def test_legacy_grafana_datasource_variable_uses_current_url_value():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prod-uid"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "templating": {"list": [{"name": "DS", "type": "datasource", "current": {"value": "lab"}}]},
            "panels": [{"datasource": "[[DS]]"}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "lab-uid", "name": "lab", "type": "prometheus"},
            {"uid": "prod-uid", "name": "prod", "type": "prometheus"},
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [{"type": "alerting", "name": "CPUHigh"}]}]}
        }),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview?var-DS=prod")
    assert snapshot["status"] == "normal"
    assert [source["uid"] for source in snapshot["sources"]] == ["prod-uid"]


def test_top_grafana_datasource_uses_selected_option_without_current_value():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prod-uid"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "templating": {"list": [{"name": "DS", "type": "datasource", "options": [
                {"value": "lab", "selected": False}, {"value": "prod", "selected": True}
            ]}]},
            "panels": [{"datasource": {"uid": "${DS}"}}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "lab-uid", "name": "lab", "type": "prometheus"},
            {"uid": "prod-uid", "name": "prod", "type": "prometheus"},
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [{"type": "alerting", "name": "CPUHigh"}]}]}
        }),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview")
    assert snapshot["status"] == "normal"
    assert [source["uid"] for source in snapshot["sources"]] == ["prod-uid"]


def test_nonstandard_top_variable_matches_current_source_among_many():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prod-uid"
    request = _Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "templating": {"list": [{"name": "metrics", "type": "custom", "current": {"value": "lab"},
                                    "options": [{"value": "lab"}, {"value": "prod"}]}]},
            "panels": [{"datasource": {"uid": "$missing"}}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "lab-uid", "name": "lab", "type": "prometheus"},
            {"uid": "prod-uid", "name": "prod", "type": "prometheus"},
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [{"type": "alerting", "name": "CPUHigh"}]}]}
        }),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    })
    snapshot = collect_dashboard_alerts(
        types.SimpleNamespace(request=request), f"{base}/d/prod/overview?var-metrics=prod"
    )
    assert snapshot["status"] == "normal"
    assert [source["uid"] for source in snapshot["sources"]] == ["prod-uid"]
    assert not any("lab-uid/api/v1" in url for url in request.urls)


def test_rendered_grafana_query_selects_actual_source_when_dashboard_variable_is_unresolved():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prod-uid"
    request = _Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "panels": [{"datasource": {"uid": "$missing"}}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "lab-uid", "name": "lab", "type": "prometheus"},
            {"uid": "prod-uid", "name": "prod", "type": "prometheus"},
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [{"type": "alerting", "name": "CPUHigh"}]}]}
        }),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    })
    snapshot = collect_dashboard_alerts(
        types.SimpleNamespace(request=request), f"{base}/d/prod/overview",
        runtime_datasource_keys={"prod-uid"},
    )
    assert snapshot["status"] == "normal"
    assert [source["uid"] for source in snapshot["sources"]] == ["prod-uid"]
    assert not any("lab-uid/api/v1" in url for url in request.urls)


def test_rendered_non_prometheus_query_does_not_select_stale_prometheus_reference():
    base = "http://grafana:3000"
    request = _Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "panels": [{"datasource": {"uid": "prom-old"}}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "prom-old", "name": "Prometheus", "type": "prometheus"},
            {"uid": "logs", "name": "Loki", "type": "loki"},
        ]),
    })
    snapshot = collect_dashboard_alerts(
        types.SimpleNamespace(request=request), f"{base}/d/prod/overview",
        runtime_datasource_keys={"logs"},
    )
    assert snapshot["status"] == "not_applicable"
    assert snapshot["sources"] == []


def test_dashboard_auto_matches_formatted_datasource_variable():
    base = "http://grafana:3000"
    proxy = f"{base}/api/datasources/proxy/uid/prom-main"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({"dashboard": {
            "templating": {"list": [{"type": "datasource", "name": "DS"}]},
            "panels": [{"datasource": {"uid": "${DS:raw}"}}],
        }}),
        f"{base}/api/datasources": _Response([
            {"uid": "prom-main", "name": "Prometheus", "type": "prometheus"}
        ]),
        f"{proxy}/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [{"type": "alerting", "name": "CPUHigh"}]}]}
        }),
        f"{proxy}/api/v1/alerts": _Response({"status": "success", "data": {"alerts": []}}),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview?var-DS=Prometheus")
    assert snapshot["status"] == "normal"


def test_older_grafana_proxy_falls_back_to_numeric_datasource_id():
    base = "http://grafana:3000"
    context = types.SimpleNamespace(request=_Request({
        f"{base}/api/dashboards/uid/prod": _Response({
            "dashboard": {"panels": [{"datasource": {"uid": "prom-live"}}]}
        }),
        f"{base}/api/datasources": _Response([
            {"id": 7, "uid": "prom-live", "name": "Prometheus", "type": "prometheus"}
        ]),
        f"{base}/api/datasources/proxy/uid/prom-live/api/v1/rules?type=alert": _Response(
            {"message": "Unable to load datasource meta data"}, status=500
        ),
        f"{base}/api/datasources/proxy/7/api/v1/rules?type=alert": _Response({
            "status": "success", "data": {"groups": [{"rules": [
                {"type": "alerting", "name": "CPUHigh"}
            ]}]}
        }),
        f"{base}/api/datasources/proxy/7/api/v1/alerts": _Response({
            "status": "success", "data": {"alerts": []}
        }),
    }))
    snapshot = collect_dashboard_alerts(context, f"{base}/d/prod/overview")
    assert snapshot["status"] == "normal"
    assert snapshot["rule_count"] == 1


def test_report_keeps_failed_item_and_reason(tmp_path, monkeypatch):
    import app.repository as repository

    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    job = {"id": 1, "name": "生产任务", "report_title": "生产报告"}
    run = {"id": 88, "started_at": "2026-10-10T09:00:00", "status": "failed"}
    results = [{
        "id": 1, "item_name": "Grafana 生产看板", "section": "业务指标",
        "status": "failed", "file_path": "", "error_message": "页面加载超时",
        "monitor_status": "unknown", "monitor_data": "{}",
    }]

    path = report.generate_docx(job, run, results)
    paragraphs = [paragraph.text for paragraph in Document(path).paragraphs]
    assert any("截图失败：Grafana 生产看板。原因：页面加载超时" in text for text in paragraphs)
    assert not any("Prometheus 告警读取失败" in text for text in paragraphs)


def test_template_report_keeps_failed_item_and_reason(tmp_path, monkeypatch):
    import app.repository as repository

    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    job = {"id": 1, "name": "生产任务", "report_title": "生产报告"}
    run = {"id": 89, "started_at": "2026-10-10T16:00:00", "status": "failed"}
    results = [{
        "id": 2, "item_name": "服务器资源", "section": "服务器资源",
        "status": "failed", "file_path": "", "error_message": "Grafana 502",
        "monitor_status": "unknown", "monitor_data": "{}",
    }]

    path = report.generate_docx(job, run, results)
    paragraphs = [paragraph.text for paragraph in Document(path).paragraphs]
    assert any("截图失败：服务器资源。原因：Grafana 502" in text for text in paragraphs)
