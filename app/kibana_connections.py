"""Find Kibana dashboards using the screenshot connection's browser session."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from app.grafana_connections import ConnectionError, _ExpiredSession, _with_login
from app.screenshot import resolve_env_placeholders


def _kibana_base_url(profile: dict[str, Any]) -> str:
    login_url = resolve_env_placeholders(str(profile.get("login_url") or "").strip())
    parts = urlsplit(login_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConnectionError("请先填写有效的 Kibana 登录地址")
    path = parts.path.rstrip("/")
    if path.lower().endswith(("/login", "/signin")):
        path = path.rsplit("/", 1)[0]
    elif "/app/" in path:
        path = path.split("/app/", 1)[0]
    return f"{parts.scheme}://{parts.netloc}{path}"


def search_dashboards(profile: dict[str, Any], query: str = "") -> dict[str, Any]:
    base = _kibana_base_url(profile)
    params_data: dict[str, Any] = {"type": "dashboard", "fields": "title", "per_page": 100, "page": 1}
    if query.strip():
        params_data.update({"search": f"*{query.strip()[:100]}*", "search_fields": "title"})
    params = urlencode(params_data)

    def fetch(context: Any, _page: Any) -> list[dict[str, str]]:
        response = context.request.get(f"{base}/api/saved_objects/_find?{params}", timeout=12_000)
        if response.status in {401, 302, 303, 307, 308}:
            raise _ExpiredSession("Kibana 会话失效")
        if not response.ok:
            raise ConnectionError(f"Kibana 仪表盘列表返回 HTTP {response.status}；请检查账号的看板读取权限")
        final_path = urlsplit(str(getattr(response, "url", ""))).path.rstrip("/").rsplit("/", 1)[-1].lower()
        content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
        if final_path in {"login", "signin"} or "text/html" in content_type:
            raise _ExpiredSession("Kibana 会话失效")
        try:
            payload = response.json()
        except Exception as exc:
            raise ConnectionError("Kibana 未返回仪表盘列表") from exc
        rows = payload.get("saved_objects") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ConnectionError("Kibana 仪表盘列表格式不正确")
        dashboards = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            uid = str(row["id"])
            title = str((row.get("attributes") or {}).get("title") or uid)
            dashboards.append({"uid": uid, "title": title,
                               "url": f"{base}/app/dashboards#/view/{quote(uid, safe='')}", "folder": "Kibana 仪表盘"})
        return dashboards

    return {"base_url": base, "dashboards": _with_login(profile, fetch)}
