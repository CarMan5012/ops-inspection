"""Discover Grafana dashboards and verify a URL with its selected login."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from app.monitoring import _dashboard_api_base, collect_dashboard_alerts
from app.screenshot import (
    _GrafanaQueryTracker,
    _ensure_logged_in,
    _is_on_login_page,
    _new_context,
    _persist_storage_state,
    resolve_env_placeholders,
    safe_delete_auth_state,
)

logger = logging.getLogger("app.grafana_connections")


class ConnectionError(RuntimeError):
    pass


class _ExpiredSession(ConnectionError):
    pass


def grafana_base_url(profile: dict[str, Any]) -> str:
    login_url = resolve_env_placeholders(str(profile.get("login_url") or "").strip())
    parts = urlsplit(login_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConnectionError("请先填写有效的 Grafana 登录地址")
    path = parts.path.rstrip("/")
    for ending in ("/login", "/signin"):
        if path.lower().endswith(ending):
            path = path[: -len(ending)]
            break
    return f"{parts.scheme}://{parts.netloc}{path}"


def _with_login(profile: dict[str, Any] | None, action: Callable[[Any, Any], Any]) -> Any:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        try:
            for attempt in range(2):
                if attempt and profile:
                    safe_delete_auth_state(profile)
                context = _new_context(browser, profile, 1280, 720, 1)
                page = context.new_page()
                try:
                    _ensure_logged_in(context, page, profile, 30_000)
                    result = action(context, page)
                    _persist_storage_state(context, profile)
                    return result
                except _ExpiredSession:
                    if attempt or not profile:
                        raise ConnectionError("登录已失效，请检查认证配置")
                finally:
                    context.close()
        finally:
            browser.close()
    raise ConnectionError("无法连接 Grafana")


def search_dashboards(profile: dict[str, Any], query: str = "") -> dict[str, Any]:
    base = grafana_base_url(profile)

    def fetch(context: Any, _page: Any) -> list[dict[str, str]]:
        url = f"{base}/api/search?type=dash-db&limit=500&query={quote(query[:100])}"
        response = context.request.get(url, timeout=12_000)
        if response.status in {401, 302}:
            raise _ExpiredSession("Grafana 会话失效")
        if not response.ok:
            raise ConnectionError(f"Grafana 看板列表返回 HTTP {response.status}")
        try:
            rows = response.json()
        except ValueError as exc:
            raise _ExpiredSession("Grafana 未返回看板列表") from exc
        if not isinstance(rows, list):
            raise ConnectionError("Grafana 看板列表格式不正确")
        origin = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
        prefix = urlsplit(base).path.rstrip("/")
        dashboards = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("uid"):
                continue
            path = str(row.get("url") or f"/d/{row['uid']}")
            if not path.startswith("/") or path.startswith("//"):
                continue
            if prefix and not path.startswith(prefix + "/"):
                path = prefix + path
            dashboards.append({
                "uid": str(row["uid"]),
                "title": str(row.get("title") or row["uid"]),
                "url": origin + path,
                "folder": str(row.get("folderTitle") or ""),
            })
        return dashboards

    return {"base_url": base, "dashboards": _with_login(profile, fetch)}


def _rendered_dashboard_options(page: Any, url: str, variables: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Read Grafana's own rendered variable menus, regardless of datasource plugin."""
    if page is None or not variables:
        return {}
    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    visible_variables = [row for row in variables if row["type"] not in {"textbox", "constant", "adhoc"}]
    if not visible_variables:
        return {}
    visible_labels = [row["label"] for row in visible_variables]
    try:
        page.wait_for_function(
            """labels => [...document.querySelectorAll('span,label,div,a,button')].some(node =>
                node.children.length === 0 && node.getClientRects().length > 0 &&
                labels.includes((node.textContent || '').trim()))""",
            arg=visible_labels, timeout=8_000,
        )
    except PlaywrightTimeoutError:
        logger.warning(
            "Grafana 页面未显示变量控件：打开地址=%s，实际地址=%s，页面标题=%s",
            url, page.url, page.title(),
        )
        return {}
    runtime_params = parse_qs(urlsplit(page.url).query, keep_blank_values=True)
    found: dict[str, list[dict[str, str]]] = {}
    for index, variable in enumerate(visible_variables):
        name = variable["name"]
        label = variable["label"]
        try:
            candidates = page.get_by_test_id(f"Dashboard template variables submenu Label {label}").all()
            candidates += page.get_by_text(label, exact=True).all()
            label_node = next((candidate for candidate in candidates if candidate.is_visible()), None)
            if label_node is None:
                continue
            control = label_node.evaluate("""(element, marker) => {
                let node = element;
                for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
                    const trigger =
                        node.querySelector('[data-testid*="Dashboard template variables Variable Value DropDown value link text "]:not(input)')
                        || node.querySelector('[role="combobox"]')
                        || node.querySelector('button[aria-haspopup]')
                        || [...node.querySelectorAll('button')].find(button =>
                            !/clear|remove|close/i.test((button.getAttribute('aria-label') || '')));
                    if (trigger && trigger !== element && trigger.getClientRects().length) {
                        const selected = (trigger.innerText || trigger.textContent || '').trim();
                        const clickTarget = trigger.querySelector('[role="combobox"]') || trigger;
                        clickTarget.setAttribute('data-inspection-variable-trigger', marker);
                        return { found: true, selected };
                    }
                }
                return { found: false, selected: '' };
            }""", str(index))
            if not isinstance(control, dict):
                control = {"found": bool(control), "selected": ""}
            selected_text = re.sub(r"(?:\s*[×✕✖])+\s*$", "", str(control.get("selected") or "")).strip()
            selected_url_values = runtime_params.get(f"var-{name}", [])
            selected_value = selected_url_values[0] if len(selected_url_values) == 1 else (
                "$__all" if selected_text in {"All", "全部"} and variable["include_all"]
                else variable.get("value_map", {}).get(selected_text, selected_text)
            )
            if selected_url_values:
                variable["default"] = selected_url_values
            elif selected_value:
                # Saved dashboard.current may differ from what Grafana actually renders.
                variable["default"] = [selected_value]
            if selected_text and selected_url_values and len(selected_url_values) == 1:
                variable.setdefault("value_map", {})[selected_text] = selected_value
            if selected_value and selected_text:
                # Keep the live selection usable even if this Grafana version's
                # dropdown structure prevents reading the rest of the choices.
                found[name] = [{"label": selected_text, "value": selected_value}]
            if not control.get("found"):
                continue
            # A Playwright click sends the pointer events Grafana's dropdown expects.
            page.locator(f'[data-inspection-variable-trigger="{index}"]').click(timeout=3_000)
            known_values = variable.get("value_map", {})
            choices: dict[str, dict[str, str]] = {}
            menu_open = False
            for attempt in range(30):
                snapshot = page.evaluate("""() => {
                    const visible = node => node.getClientRects().length > 0 &&
                        getComputedStyle(node).visibility !== 'hidden';
                    const direct = [...document.querySelectorAll(
                        '[data-testid*="Dashboard template variables Variable Value DropDown option text "], [role="option"]'
                    )].filter(visible);
                    const menus = [...document.querySelectorAll(
                        '[data-testid="Variable options"], [aria-label="Variable options"], [role="listbox"], [role="menu"]'
                    )].filter(visible);
                    const menu = menus.at(-1);
                    const rows = direct.length ? direct : menu ? [...menu.querySelectorAll(
                        'label, li, [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]'
                    )].filter(visible) : [];
                    const texts = rows.map(node => (node.innerText || node.textContent || '').trim())
                        .filter(Boolean);
                    let scroll = rows[0] || menu;
                    while (scroll && scroll !== document.body && scroll !== document.documentElement &&
                        scroll.scrollHeight <= scroll.clientHeight + 2) scroll = scroll.parentElement;
                    let moved = false;
                    if (scroll && scroll !== document.body && scroll !== document.documentElement && rows.length) {
                        const before = scroll.scrollTop;
                        scroll.scrollTop += scroll.clientHeight;
                        moved = scroll.scrollTop > before;
                    }
                    return { open: !!menu || direct.length > 0, texts, moved };
                }""")
                menu_open = menu_open or snapshot["open"]
                for text in snapshot["texts"]:
                    if not text or text.startswith("Selected ("):
                        continue
                    value = (
                        "$__all" if text in {"All", "全部"} and variable["include_all"]
                        else known_values.get(text, text)
                    )
                    choices[value] = {"label": text, "value": value}
                if snapshot["moved"]:
                    page.wait_for_timeout(50)
                elif snapshot["texts"] or attempt == 29:
                    break
                else:
                    page.wait_for_timeout(100)
            if choices:
                found[name] = list(choices.values())
            else:
                logger.warning(
                    "Grafana 变量 %s 未读取到候选项（菜单已展开=%s），仅取得当前选中值；页面=%s",
                    name, menu_open, page.url,
                )
        except Exception as exc:
            logger.warning("读取 Grafana 页面变量 %s 的菜单失败：%s", name, str(exc).splitlines()[0])
            logger.debug("Grafana 变量菜单读取详情", exc_info=True)
        finally:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
    return found


def dashboard_settings(profile: dict[str, Any], uid: str, params: str = "") -> dict[str, Any]:
    """Return the dashboard's variable definitions for the screenshot editor."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", uid):
        raise ConnectionError("看板标识无效")
    if len(params) > 5000:
        raise ConnectionError("看板选项过长")
    base = grafana_base_url(profile)

    def fetch(context: Any, page: Any) -> dict[str, Any]:
        response = context.request.get(f"{base}/api/dashboards/uid/{quote(uid)}", timeout=12_000)
        if response.status in {401, 302}:
            raise _ExpiredSession("Grafana 会话失效")
        if not response.ok:
            raise ConnectionError(f"无法读取看板设置：HTTP {response.status}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectionError("Grafana 未返回看板设置") from exc
        dashboard = payload.get("dashboard") if isinstance(payload, dict) else None
        if not isinstance(dashboard, dict):
            raise ConnectionError("看板设置格式不正确")

        raw_variables = dashboard.get("templating") or {}
        raw_variables = raw_variables.get("list", []) if isinstance(raw_variables, dict) else []
        variables: list[dict[str, Any]] = []
        selected_params = parse_qs(params.lstrip("?"), keep_blank_values=True)
        for variable in raw_variables if isinstance(raw_variables, list) else []:
            if not isinstance(variable, dict):
                continue
            name = str(variable.get("name") or "")
            kind = str(variable.get("type") or "")
            if not name or kind in {"constant", "adhoc"} or variable.get("hide") == 2:
                continue
            current = variable.get("current") or {}
            value = current.get("value") if isinstance(current, dict) else None
            default = [str(entry) for entry in value] if isinstance(value, list) else ([str(value)] if value is not None else [])
            if selected_params.get(f"var-{name}"):
                default = selected_params[f"var-{name}"]
            variables.append({
                "name": name,
                "label": str(variable.get("label") or name),
                "type": kind,
                "multi": bool(variable.get("multi")),
                "include_all": bool(variable.get("includeAll")),
                "options": [],
                "value_map": {
                    str(option.get("text") or option["value"]): str(option["value"])
                    for option in variable.get("options") or []
                    if isinstance(option, dict) and option.get("value") is not None
                },
                "default": default,
                "dynamic": kind == "query",
            })
        if any(variable["type"] == "datasource" for variable in variables):
            # Grafana displays datasource names, while its var-* URL values can
            # be UIDs. The live menu supplies candidates; this API only maps
            # their displayed names to the values Grafana expects in the URL.
            try:
                sources_response = context.request.get(f"{base}/api/datasources", timeout=12_000)
                sources = sources_response.json() if sources_response.ok else []
                if isinstance(sources, list):
                    source_values = {
                        str(source["name"]): str(source["uid"])
                        for source in sources if isinstance(source, dict)
                        and source.get("name") and source.get("uid")
                    }
                    for variable in variables:
                        if variable["type"] == "datasource":
                            variable["value_map"].update(source_values)
            except Exception as exc:
                logger.info("Grafana 数据源名称与 UID 对应关系不可用：%s", str(exc).splitlines()[0])
        dashboard_url = f"{base}/d/{quote(uid)}"
        if params:
            dashboard_url += "?" + params.lstrip("?")
        rendered_options = _rendered_dashboard_options(page, dashboard_url, variables)
        selectable_variables = []
        for variable in variables:
            if variable["type"] in {"textbox"}:
                variable.pop("value_map", None)
                selectable_variables.append(variable)
                continue
            options = rendered_options.get(variable["name"])
            if not options:
                if variable["type"] == "datasource":
                    logger.info("Grafana 未提供数据源变量 %s 的菜单；面板实际数据源仍由查询请求识别", variable["name"])
                    continue
                raise ConnectionError(f"Grafana 页面未返回“{variable['label']}”的候选项，请检查后端日志中的实际页面地址")
            variable["options"] = options
            variable.pop("value_map", None)
            selectable_variables.append(variable)
        time_range = dashboard.get("time") or {}
        timepicker = dashboard.get("timepicker") or {}
        if not isinstance(timepicker, dict):
            timepicker = {}
        quick_ranges = timepicker.get("quick_ranges") or []
        refresh_intervals = timepicker.get("refresh_intervals") or []
        return {
            "uid": uid,
            "title": str(dashboard.get("title") or uid),
            "variables": selectable_variables,
            "time": {
                "from": str(time_range.get("from") or "now-1h") if isinstance(time_range, dict) else "now-1h",
                "to": str(time_range.get("to") or "now") if isinstance(time_range, dict) else "now",
            },
            "refresh": str(dashboard.get("refresh") or ""),
            "time_choices": [
                {"label": str(row["display"]), "from": str(row["from"]), "to": str(row["to"])}
                for row in quick_ranges[:100] if isinstance(row, dict)
                and row.get("display") and row.get("from") and row.get("to")
            ] if isinstance(quick_ranges, list) else [],
            "refresh_choices": [str(value) for value in refresh_intervals[:100] if isinstance(value, str) and value]
            if isinstance(refresh_intervals, list) else [],
        }

    return _with_login(profile, fetch)


def check_target(url: str, profile: dict[str, Any] | None) -> dict[str, Any]:
    target = urlsplit(url.strip())
    if target.scheme not in {"http", "https"} or not target.netloc:
        raise ConnectionError("请填写完整的 http:// 或 https:// 页面地址")

    def inspect(context: Any, page: Any) -> dict[str, Any]:
        query_tracker = _GrafanaQueryTracker(page) if _dashboard_api_base(url) else None
        response = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        if _is_on_login_page(page, profile):
            raise _ExpiredSession("页面跳到了登录页")
        if response is None or response.status >= 400:
            status = response.status if response else "无响应"
            raise ConnectionError(f"目标页面访问失败：HTTP {status}")
        monitor_status = "not_applicable"
        monitor_message = ""
        dashboard = _dashboard_api_base(page.url)
        if dashboard:
            base, dashboard_uid = dashboard
            api_response = context.request.get(
                f"{base}/api/dashboards/uid/{quote(dashboard_uid)}", timeout=10_000
            )
            if api_response.status in {401, 302}:
                raise _ExpiredSession("看板 API 登录已失效")
            if not api_response.ok:
                raise ConnectionError(f"看板无查看权限或不存在：HTTP {api_response.status}")
            if query_tracker and not query_tracker.seen:
                try:
                    page.wait_for_event(
                        "request", predicate=lambda request: _GrafanaQueryTracker._is_query(request.url),
                        timeout=5_000,
                    )
                except PlaywrightTimeoutError:
                    pass
            monitoring = collect_dashboard_alerts(
                context, page.url,
                runtime_datasource_keys=query_tracker.datasource_keys if query_tracker else None,
            )
            monitor_status = str(monitoring.get("status") or "unknown")
            monitor_message = str(monitoring.get("error") or "")
        return {
            "ok": True, "message": "页面可访问",
            "final_url": page.url,
            "monitor_status": monitor_status,
            "monitor_message": monitor_message,
        }

    try:
        return _with_login(profile, inspect)
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:240], "monitor_status": "unknown"}
