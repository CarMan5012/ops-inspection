from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.auth import COOKIE_NAME


OPENAPI_TAGS = [
    {"name": "系统与总览", "description": "登录会话、首页总览和基础运行指标。"},
    {"name": "巡检任务", "description": "巡检任务的创建、修改、删除和立即运行。"},
    {"name": "截图项", "description": "截图项配置、测试截图和截图采集参数。"},
    {"name": "运行记录", "description": "巡检运行历史、截图结果和报告下载信息。"},
    {"name": "认证配置", "description": "目标系统登录账号、登录测试和会话状态管理。"},
    {"name": "邮件配置", "description": "SMTP 发信配置和测试发送。"},
    {"name": "周期报告", "description": "周报、月报配置和手动生成。"},
    {"name": "存储清理", "description": "数据目录占用统计、保留策略和手动清理。"},
]


SUMMARY_BY_ROUTE: dict[tuple[str, str], str] = {
    ("GET", "/session"): "获取当前登录会话",
    ("GET", "/dashboard"): "获取总览数据",
    ("GET", "/jobs"): "获取巡检任务列表",
    ("POST", "/jobs"): "创建巡检任务",
    ("GET", "/jobs/{job_id}"): "获取巡检任务详情",
    ("PUT", "/jobs/{job_id}"): "更新巡检任务",
    ("DELETE", "/jobs/{job_id}"): "删除巡检任务",
    ("POST", "/jobs/{job_id}/run"): "立即运行巡检任务",
    ("POST", "/jobs/{job_id}/items"): "创建截图项",
    ("PUT", "/items/{item_id}"): "更新截图项",
    ("DELETE", "/items/{item_id}"): "删除截图项",
    ("POST", "/items/{item_id}/test"): "测试截图项",
    ("GET", "/runs"): "获取运行记录列表",
    ("GET", "/runs/{run_id}"): "获取运行记录详情",
    ("GET", "/auth-profiles"): "获取认证配置列表",
    ("POST", "/auth-profiles"): "创建认证配置",
    ("PUT", "/auth-profiles/{auth_id}"): "更新认证配置",
    ("DELETE", "/auth-profiles/{auth_id}"): "删除认证配置",
    ("POST", "/auth-profiles/{auth_id}/test"): "测试认证登录",
    ("GET", "/mail-profiles"): "获取邮件配置列表",
    ("POST", "/mail-profiles"): "创建邮件配置",
    ("PUT", "/mail-profiles/{mail_id}"): "更新邮件配置",
    ("DELETE", "/mail-profiles/{mail_id}"): "删除邮件配置",
    ("GET", "/periodic-reports"): "获取周期报告配置和记录",
    ("POST", "/periodic-reports/{report_type}/run"): "立即生成周期报告",
    ("GET", "/storage"): "获取存储占用和清理配置",
    ("PUT", "/storage/settings"): "更新存储清理配置",
    ("POST", "/storage/estimate"): "预估可清理数据",
    ("POST", "/storage/cleanup"): "立即执行存储清理",
    ("GET", "/storage/cleanup-runs"): "获取清理执行记录",
}


def configure_openapi(app: FastAPI, api_prefix: str, app_name: str) -> None:
    normalized_prefix = _normalize_prefix(api_prefix)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=f"{app_name} API",
            version="1.0.0",
            description=(
                "运检巡检系统后端接口文档。"
                "本文档只展示 JSON API，不包含前端页面路由。"
                "接口默认使用登录 Cookie 鉴权，登录后台后可直接在同源 Swagger 中调试。"
            ),
            routes=app.routes,
            tags=OPENAPI_TAGS,
        )

        schema["paths"] = _filter_and_enrich_paths(schema.get("paths") or {}, normalized_prefix)
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["OpsSessionCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": COOKIE_NAME,
            "description": "后台登录后写入的会话 Cookie。",
        }

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def _filter_and_enrich_paths(paths: dict[str, Any], api_prefix: str) -> dict[str, Any]:
    api_paths: dict[str, Any] = {}
    for path, path_item in paths.items():
        if not _is_api_path(path, api_prefix):
            continue

        relative_path = _strip_prefix(path, api_prefix)
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            method_name = method.upper()
            operation["tags"] = [_tag_for_path(relative_path)]
            operation["summary"] = SUMMARY_BY_ROUTE.get(
                (method_name, relative_path),
                operation.get("summary") or f"{method_name} {relative_path}",
            )
            operation.setdefault("security", [{"OpsSessionCookie": []}])
        api_paths[path] = path_item
    return api_paths


def _normalize_prefix(api_prefix: str) -> str:
    prefix = (api_prefix or "/api").strip()
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if len(prefix) > 1:
        prefix = prefix.rstrip("/")
    return prefix


def _is_api_path(path: str, api_prefix: str) -> bool:
    return path == api_prefix or path.startswith(api_prefix + "/")


def _strip_prefix(path: str, api_prefix: str) -> str:
    if path == api_prefix:
        return "/"
    return path[len(api_prefix):] or "/"


def _tag_for_path(relative_path: str) -> str:
    if relative_path in {"/session", "/dashboard"}:
        return "系统与总览"
    if relative_path.startswith("/jobs/") and relative_path.endswith("/items"):
        return "截图项"
    if relative_path.startswith("/items"):
        return "截图项"
    if relative_path.startswith("/jobs"):
        return "巡检任务"
    if relative_path.startswith("/runs"):
        return "运行记录"
    if relative_path.startswith("/auth-profiles"):
        return "认证配置"
    if relative_path.startswith("/mail-profiles"):
        return "邮件配置"
    if relative_path.startswith("/periodic-reports"):
        return "周期报告"
    if relative_path.startswith("/storage"):
        return "存储清理"
    return "系统与总览"
