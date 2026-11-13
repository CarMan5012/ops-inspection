"""Read Prometheus alerts through the Grafana data source used by a dashboard."""

from __future__ import annotations

import re
import time
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit


class MonitoringError(RuntimeError):
    pass


def _json_get(context: Any, url: str, deadline: float) -> Any:
    timeout = min(7_000, int((deadline - time.monotonic()) * 1000))
    if timeout <= 0:
        raise MonitoringError("读取监测数据超时")
    response = context.request.get(url, timeout=timeout)
    if not response.ok:
        raise MonitoringError(f"Grafana API 返回 HTTP {response.status}")
    try:
        return response.json()
    except ValueError as exc:
        raise MonitoringError("Grafana API 未返回 JSON，请检查登录权限") from exc


def _prometheus_payload(context: Any, url: str, deadline: float) -> dict[str, Any]:
    payload = _json_get(context, url, deadline)
    if not isinstance(payload, dict) or payload.get("status") != "success":
        detail = payload.get("error") if isinstance(payload, dict) else "响应格式错误"
        raise MonitoringError(f"Prometheus 查询失败：{detail or '未知错误'}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MonitoringError("Prometheus 响应缺少数据")
    return data


def _alert_details(value: Any) -> dict[str, str]:
    """Keep alert metadata useful for display without storing unbounded API data."""
    if not isinstance(value, dict):
        return {}
    return {
        str(key)[:80]: str(item)[:1000]
        for key, item in list(value.items())[:40]
    }


def prometheus_alert_url(source_url: Any) -> str:
    """Build a browser URL for Prometheus's native alerts page."""
    parts = urlsplit(str(source_url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/alerts", "", ""))


def _dashboard_variable_values(dashboard: dict[str, Any], page_url: str) -> dict[str, list[str]]:
    variables: dict[str, list[str]] = {}
    templating = dashboard.get("templating")
    if isinstance(templating, dict):
        for variable in templating.get("list", []):
            if not isinstance(variable, dict):
                continue
            name = str(variable.get("name") or "")
            if not name:
                continue
            current = variable.get("current")
            value = (current.get("value") or current.get("text")) if isinstance(current, dict) else None
            if isinstance(value, dict):
                value = value.get("uid") or value.get("name") or value.get("value")
            if not value:
                value = [option.get("value") for option in variable.get("options", [])
                         if isinstance(option, dict) and option.get("selected") and option.get("value") is not None]
            if not value and variable.get("type") == "datasource":
                value = [option.get("value") for option in variable.get("options", [])
                         if isinstance(option, dict) and option.get("value") is not None][:1]
            if isinstance(value, list):
                variables[name] = [str(item).strip() for item in value if str(item).strip()]
            elif isinstance(value, (str, int)) and str(value).strip():
                variables[name] = [str(value).strip()]
            else:
                variables[name] = []
    for key, values in parse_qs(urlsplit(page_url).query).items():
        if key.startswith("var-") and key[4:] in variables and values:
            variables[key[4:]] = [value.strip() for value in values if value.strip()]
    return variables


def _panel_datasource_uids(dashboard: dict[str, Any], page_url: str) -> set[str]:
    found: set[str] = set()
    variables = _dashboard_variable_values(dashboard, page_url)

    def visit(panel: dict[str, Any]) -> None:
        sources = [panel.get("datasource")]
        sources.extend(target.get("datasource") for target in panel.get("targets", []) if isinstance(target, dict))
        for source in sources:
            key = (source.get("uid") or source.get("name") or source.get("id")) if isinstance(source, dict) else source
            if isinstance(key, (str, int)) and str(key):
                key = str(key)
                variable = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}?", key)
                legacy_variable = re.fullmatch(r"\[\[([A-Za-z_][A-Za-z0-9_]*)\]\]", key)
                if variable or legacy_variable:
                    name = (variable or legacy_variable).group(1)
                    found.update(variables.get(name) or {"$unresolved"})
                elif key != "-- Mixed --":
                    found.add(key.strip())
        for child in panel.get("panels", []):
            if isinstance(child, dict):
                visit(child)

    for panel in dashboard.get("panels", []):
        if isinstance(panel, dict):
            visit(panel)
    return found


def _selected_datasource_keys(dashboard: dict[str, Any], page_url: str) -> set[str]:
    """Read Grafana's current datasource selector, including URL overrides."""
    variables = _dashboard_variable_values(dashboard, page_url)
    templating = dashboard.get("templating")
    rows = templating.get("list", []) if isinstance(templating, dict) else []
    return {
        value for variable in rows if isinstance(variable, dict) and variable.get("type") == "datasource"
        for value in variables.get(str(variable.get("name") or ""), [])
        if value != "$__all"
    }


def _selected_variable_keys(dashboard: dict[str, Any], page_url: str) -> set[str]:
    """Find datasource names selected through non-standard Grafana variables."""
    return {
        value for values in _dashboard_variable_values(dashboard, page_url).values()
        for value in values if value != "$__all"
    }


def _dashboard_api_base(page_url: str) -> tuple[str, str] | None:
    parts = urlsplit(page_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    match = re.search(r"(?P<prefix>.*?)/d(?:-solo)?/(?P<uid>[A-Za-z0-9_-]+)(?:/|$)", parts.path)
    if not match:
        return None
    return f"{parts.scheme}://{parts.netloc}{match.group('prefix')}", match.group("uid")


def collect_dashboard_alerts(
    context: Any, page_url: str, deadline: float | None = None,
    runtime_datasource_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Return a bounded snapshot; unavailable monitoring never means healthy."""
    location = _dashboard_api_base(page_url)
    if location is None:
        return {"status": "not_applicable", "alerts": [], "sources": [], "rule_count": 0}
    base, dashboard_uid = location
    deadline = min(deadline or float("inf"), time.monotonic() + 15)
    snapshot: dict[str, Any] = {
        "status": "unknown", "alerts": [], "sources": [], "rule_count": 0,
        "dashboard_uid": dashboard_uid, "error": "", "unconfigured_sources": [],
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        dashboard_data = _json_get(context, f"{base}/api/dashboards/uid/{quote(dashboard_uid)}", deadline)
        dashboard = dashboard_data.get("dashboard") if isinstance(dashboard_data, dict) else None
        if not isinstance(dashboard, dict):
            raise MonitoringError("无法读取 Grafana 看板配置")
        all_sources = _json_get(context, f"{base}/api/datasources", deadline)
        if not isinstance(all_sources, list):
            raise MonitoringError("无法读取 Grafana 数据源列表")
        prometheus_sources = [
            source for source in all_sources
            if isinstance(source, dict) and str(source.get("type") or "").lower() == "prometheus"
            and (source.get("uid") or source.get("id") is not None)
        ]
        used_uids = _panel_datasource_uids(dashboard, page_url)
        used_keys = {key.strip().casefold() for key in used_uids}

        def source_keys(source: dict[str, Any]) -> set[str]:
            return {str(source[key]).strip().casefold() for key in ("uid", "name", "id") if source.get(key) is not None}

        runtime_keys = {str(key).strip().casefold() for key in (runtime_datasource_keys or set()) if str(key).strip()}
        runtime_matches = [source for source in all_sources if isinstance(source, dict) and source_keys(source) & runtime_keys]
        selected = [source for source in prometheus_sources if source_keys(source) & runtime_keys]
        matched_from_runtime = bool(selected)
        if runtime_matches and not selected:
            snapshot["status"] = "not_applicable"
            return snapshot
        if not selected:
            selected = [source for source in prometheus_sources if source_keys(source) & used_keys]
        known_keys = {
            key for source in all_sources if isinstance(source, dict)
            for key in source_keys(source)
        }
        unresolved_keys = used_keys - known_keys
        matched_from_variables = False
        if not selected and (not used_keys or unresolved_keys):
            top_keys = {key.strip().casefold() for key in _selected_datasource_keys(dashboard, page_url)}
            selected = [source for source in prometheus_sources if source_keys(source) & top_keys]
            if not selected:
                variable_keys = {key.strip().casefold() for key in _selected_variable_keys(dashboard, page_url)}
                selected = [source for source in prometheus_sources if source_keys(source) & variable_keys]
            matched_from_variables = bool(selected)
        if not selected and not used_keys:
            defaults = [source for source in prometheus_sources if source.get("isDefault")]
            if len(defaults) == 1:
                selected = defaults
            elif len(prometheus_sources) == 1:
                selected = prometheus_sources
        if not selected:
            if not prometheus_sources or (used_keys and used_keys <= known_keys):
                snapshot["status"] = "not_applicable"
                return snapshot
            top_values = sorted(_selected_datasource_keys(dashboard, page_url))
            current_values = sorted(_selected_variable_keys(dashboard, page_url))
            references = "、".join(value[:60] for value in sorted(used_uids)[:3]) or "无"
            top_selection = "、".join(value[:60] for value in top_values[:3]) or "无"
            variable_selection = "、".join(value[:60] for value in current_values[:3]) or "无"
            available = "、".join(str(source.get("name") or source.get("uid") or source.get("id"))[:60]
                                 for source in prometheus_sources[:3]) or "无"
            raise MonitoringError(
                f"当前看板选择的数据源未在 Grafana 数据源列表中匹配"
                f"（面板引用：{references}；数据源变量：{top_selection}；其他变量当前值：{variable_selection}；可用 Prometheus：{available}）"
            )

        seen_alerts: set[tuple[tuple[str, str], ...]] = set()
        errors: list[str] = ["看板有无法解析的数据源变量"] if "$unresolved" in used_uids and not (matched_from_variables or matched_from_runtime) else []
        for source in selected:
            uid = str(source.get("uid") or source.get("id"))
            name = str(source.get("name") or uid)
            source_info = {
                "uid": uid, "name": name, "rule_count": 0,
                "alert_url": prometheus_alert_url(source.get("url")),
            }
            snapshot["sources"].append(source_info)
            try:
                paths = [f"uid/{quote(uid, safe='')}"] if source.get("uid") else []
                if source.get("id") is not None:
                    paths.append(str(source["id"]))
                for path_index, path in enumerate(paths):
                    proxy = f"{base}/api/datasources/proxy/{path}"
                    try:
                        rules = _prometheus_payload(context, f"{proxy}/api/v1/rules?type=alert", deadline)
                        alerts_data = _prometheus_payload(context, f"{proxy}/api/v1/alerts", deadline)
                        break
                    except Exception:
                        if path_index == len(paths) - 1:
                            raise
                groups = rules.get("groups")
                alerts = alerts_data.get("alerts")
                if not isinstance(groups, list) or not isinstance(alerts, list):
                    raise MonitoringError("Prometheus 告警数据格式错误")
                for group in groups:
                    for rule in group.get("rules", []) if isinstance(group, dict) else []:
                        if not isinstance(rule, dict) or rule.get("type") != "alerting":
                            continue
                        snapshot["rule_count"] += 1
                        source_info["rule_count"] += 1
                        if str(rule.get("health") or "").lower() not in {"ok", ""}:
                            errors.append(f"{name} 的规则 {rule.get('name') or '未命名'} 执行异常")
                if source_info["rule_count"] == 0:
                    snapshot["unconfigured_sources"].append(name)
                for alert in alerts:
                    if not isinstance(alert, dict):
                        continue
                    labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
                    key = (("source_uid", uid), *tuple(sorted((str(k), str(v)) for k, v in labels.items())))
                    if key in seen_alerts:
                        continue
                    seen_alerts.add(key)
                    annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
                    snapshot["alerts"].append({
                        "name": str(labels.get("alertname") or "未命名告警")[:160],
                        "state": str(alert.get("state") or "unknown")[:32],
                        "severity": str(labels.get("severity") or "")[:40],
                        "summary": str(annotations.get("summary") or annotations.get("description") or "")[:500],
                        "instance": str(labels.get("instance") or "")[:160],
                        "value": str(alert.get("value") or "")[:80],
                        "source": name,
                        "source_uid": uid,
                        "active_at": str(alert.get("activeAt") or "")[:80],
                        "labels": _alert_details(labels),
                        "annotations": _alert_details(annotations),
                    })
                    if len(snapshot["alerts"]) >= 200:
                        errors.append("告警超过 200 条，报告只显示前 200 条")
                        break
            except Exception as exc:
                errors.append(f"{name}：{exc}")
        if errors:
            snapshot["error"] = "；".join(errors[:5])
        from app.alert_ignores import apply_ignore_rules
        apply_ignore_rules(snapshot, page_url)
        actionable_alerts = [alert for alert in snapshot["alerts"] if not alert.get("ignored")]
        snapshot["status"] = (
            "alert" if actionable_alerts else "unknown" if errors else
            "unconfigured" if snapshot["unconfigured_sources"] else "normal"
        )
    except Exception as exc:
        snapshot["error"] = str(exc)
    return snapshot


def summarize_monitoring(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize captured data-source states without equating no alerts with no coverage."""
    statuses: list[str] = []
    alerts: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    errors: list[str] = []
    uncovered: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for item in results:
        status = str(item.get("monitor_status") or "not_applicable")
        if status == "not_applicable":
            continue
        statuses.append(status)
        try:
            data = json.loads(item.get("monitor_data") or "{}")
        except (ValueError, TypeError):
            data = {}
        if data.get("error"):
            errors.append(f"{item.get('item_name') or '截图项'}：{data['error']}")
        uncovered.extend(str(name) for name in data.get("unconfigured_sources", []))
        for alert in data.get("alerts", []):
            if not isinstance(alert, dict):
                continue
            labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
            source_key = str(alert.get("source_uid") or alert.get("source") or "")
            key = (
                (source_key, *tuple(sorted((str(k), str(v)) for k, v in labels.items())))
                if labels else
                (source_key, str(alert.get("name") or ""), str(alert.get("instance") or ""), str(alert.get("state") or ""))
            )
            if key not in seen:
                seen.add(key)
                (ignored if alert.get("ignored") else alerts).append(alert)
    status = (
        "alert" if alerts else "unknown" if "unknown" in statuses else
        "unconfigured" if "unconfigured" in statuses else
        "normal" if "normal" in statuses else "not_checked"
    )
    firing = sum(alert.get("state") == "firing" for alert in alerts)
    pending = sum(alert.get("state") == "pending" for alert in alerts)
    if status == "alert":
        rule_count = len({
            (str(alert.get("source_uid") or alert.get("source") or ""), str(alert.get("name") or ""))
            for alert in alerts
        })
        text = f"Prometheus 告警 {rule_count} 类 · {len(alerts)} 个实例（触发 {firing}{f'，待触发 {pending}' if pending else ''}）"
    elif status == "unknown":
        text = "Prometheus 告警读取失败，监测结果无法判断"
    elif status == "unconfigured":
        text = "关联的数据源没有已加载的告警规则"
    elif status == "normal":
        text = ("当前无未忽略的活跃告警" if ignored else
                "已配置的 Prometheus 告警规则当前无活跃告警")
    else:
        text = "本次未检查 Prometheus 告警"
    if errors:
        text += f"；{errors[0]}"
    if uncovered and status == "unconfigured":
        text += f"（{uncovered[0]}）"
    if uncovered and status == "alert":
        text += f"；{uncovered[0]} 未配置告警规则"
    if ignored:
        text += f"；已忽略 {len(ignored)} 条告警"
    return {"status": status, "text": text, "alerts": alerts, "ignored": ignored, "errors": errors}
