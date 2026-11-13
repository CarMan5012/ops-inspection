from __future__ import annotations

from pathlib import Path
from typing import Any
import base64
import re

from fastapi import APIRouter, BackgroundTasks, Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import COOKIE_NAME, admin_username, check_password, create_idle_token, get_session_ttl_seconds, renew_idle_session, require_login, verify_token
from app.db import init_db, sqlite3 as db_sqlite3
from app.repository import (
    dashboard_data,
    create_run,
    create_queued_test_run,
    finish_active_run,
    delete_auth_profile,
    delete_job,
    delete_screenshot_item,
    get_auth_profile,
    get_job,
    get_run,
    get_screenshot_item,
    list_auth_profiles,
    list_jobs,
    list_dingtalk_profiles,
    get_dingtalk_profile,
    save_dingtalk_profile,
    delete_dingtalk_profile,
    list_runs,
    list_runs_page,
    list_screenshot_items,
    list_screenshot_results,
    save_auth_profile,
    save_job,
    save_screenshot_item,
    save_screenshot_items_batch,
    get_system_setting,
    set_system_setting,
)
from app.runner import run_job, test_capture_item
from app.screenshot import render_watermark_preview_png
from app.job_execution import JobAlreadyRunning, acquire_job_lease, recover_interrupted_runs
from app.scheduler import reload_jobs, shutdown_scheduler, start_scheduler
from app.settings import settings
from app.storage_paths import resolve_artifact_path
from app.watermark import DEFAULT_TIME_FORMAT, MAX_WATERMARK_TEXT_LENGTH, WATERMARK_DEFAULTS, validate_time_format, validate_watermark_alignment, watermark_options
from app.ui_config import CLEANUP_SCHEDULE_MODES, JOB_FORM_DEFAULTS, ITEM_FORM_DEFAULTS, RUN_DEFAULT_PAGE_SIZE, RUN_PAGE_SIZES, WATERMARK_RULES, frontend_config
from app.report import list_template_sections
from app.report_templates import (
    MAX_TEMPLATE_BYTES, create_template, delete_template, get_template,
    inspect_template, list_templates, template_path,
)
from app.taskbar_templates import (
    MAX_IMAGE_BYTES as MAX_TASKBAR_BYTES, all_templates as all_taskbar_templates,
    create_uploaded as create_taskbar_template, delete_uploaded as delete_taskbar_template,
    selected_path as selected_taskbar_path,
)
from app.openapi import configure_openapi
from app.login_crypto import LoginPayloadError, decrypt_login_payload, get_login_public_jwk
from app.login_rate_limit import consume_login_attempt, clear_login_attempts
from app.installation import change_admin_password, change_admin_username, complete_setup, ensure_setup_code, installation_state, verify_setup_code
from app.access_context import cookie_path, normalize_access_context, public_api_prefix, public_base_path, routing_state
import os
import logging
import sys
from logging.handlers import RotatingFileHandler
from app.logging_config import AppLogFormatter, is_color_supported


log_level_str = "INFO"
log_level = logging.INFO

app_logger = logging.getLogger("app")
app_logger.setLevel(log_level)

if not app_logger.handlers:
    # 控制台支持彩色高亮，文件保持纯净对齐
    console_color = is_color_supported(sys.stdout)
    console_formatter = AppLogFormatter(use_color=console_color, timezone=settings.default_timezone)
    file_formatter = AppLogFormatter(use_color=False, timezone=settings.default_timezone)

    # 控制台标准输出
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(console_formatter)
    app_logger.addHandler(stream_handler)

    # 文件持久化滚动日志
    try:
        settings.ensure_dirs()
        log_file = settings.log_dir / "app.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(file_formatter)
        app_logger.addHandler(file_handler)
    except Exception as e:
        # 避免写文件失败导致应用无法启动，做降级处理
        stream_handler.stream.write(f"警告: 初始化文件日志失败: {e}\n")

    app_logger.propagate = False
    app_logger.info(f"应用日志系统初始化完成 (级别: {log_level_str}, 控制台高亮: {'已启用' if console_color else '未启用'})")

app = FastAPI(
    title=settings.app_name,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    openapi_tags=[],
)
configure_openapi(app, settings.api_prefix, settings.app_name)


@app.middleware("http")
async def csrf_protect_middleware(request: Request, call_next):
    external_path = request.scope["path"]
    state, base = routing_state()
    if state == "pending":
        if external_path == "/":
            return RedirectResponse("/setup", status_code=303)
        if external_path not in {"/setup", "/setup/verify-code"} and not external_path.startswith("/static/"):
            return Response(status_code=404)
        internal_path = settings.frontend_base_path + external_path
    else:
        if external_path == "/" and base:
            return RedirectResponse(base + "/", status_code=303)
        if base and external_path == base:
            return RedirectResponse(base + "/", status_code=303)
        if base and not external_path.startswith(base + "/"):
            return Response(status_code=404)
        relative_path = external_path[len(base):] if base else external_path
        internal_path = settings.frontend_base_path + relative_path
    request.scope["path"] = internal_path
    request.scope["raw_path"] = internal_path.encode("utf-8")

    # 豁免登出接口，防止由于会话超时、Token 丢失导致登出操作被 CSRF 403 拦截，确保用户能顺利退回登录页
    path = request.url.path
    logout_path = settings.frontend_base_path + "/logout"
    if path.rstrip("/") == logout_path.rstrip("/"):
        return await call_next(request)

    # 只针对非安全写入方法进行校验
    if request.method not in ("GET", "HEAD", "OPTIONS", "TRACE"):
        # 1. 校验 Origin / Referer 同源性
        scheme = request.url.scheme
        netloc = request.url.netloc
        forwarded_proto = request.headers.get("x-forwarded-proto", scheme)
        forwarded_host = request.headers.get("x-forwarded-host", netloc)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        def same_origin(value: str) -> bool:
            from urllib.parse import urlsplit

            try:
                parsed = urlsplit(value)
                # 剥离端口号进行比对，防止 Nginx 的 $host（无端口）与浏览器的 Origin（含端口）不一致
                parsed_host = parsed.netloc.split(":")[0]
                f_host_clean = forwarded_host.split(":")[0]
                
                # 允许 http 和 https 协议兼容（针对反向代理链路中的 SSL 卸载/Termination 情况）
                scheme_ok = (
                    parsed.scheme.lower() == forwarded_proto.lower()
                    or {parsed.scheme.lower(), forwarded_proto.lower()} <= {"http", "https"}
                )
                return scheme_ok and parsed_host.lower() == f_host_clean.lower()
            except Exception:
                return False

        origin_ok = False
        if origin:
            if same_origin(origin):
                origin_ok = True
        elif referer:
            if same_origin(referer):
                origin_ok = True

        if not origin_ok:
            app_logger.warning("CSRF Forbidden: Origin or Referer check failed")
            return HTMLResponse(content="Forbidden: Origin or Referer check failed", status_code=403)

        # 2. 校验 CSRF Token
        cookie_csrf = request.cookies.get("csrf_token")
        if not cookie_csrf:
            app_logger.warning("CSRF Forbidden: Missing CSRF token in cookie")
            return HTMLResponse(content="Forbidden: Missing CSRF token in cookie", status_code=403)

        request_csrf = request.headers.get("x-csrf-token")

        # 若 Header 缺省且是 Form 表单提交，从 Form 中安全提取
        if not request_csrf:
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                # 复制备份 body，防止 request.form() 挂起
                body = await request.body()
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}
                request._receive = receive

                form_data = await request.form()
                request_csrf = form_data.get("csrf_token")

        if not request_csrf:
            app_logger.warning("CSRF Forbidden: Missing CSRF token in request")
            return HTMLResponse(content="Forbidden: Missing CSRF token in request", status_code=403)

        import hmac
        if not hmac.compare_digest(request_csrf, cookie_csrf):
            app_logger.warning("CSRF Forbidden: CSRF token mismatch")
            return HTMLResponse(content="Forbidden: CSRF token mismatch", status_code=403)

    response = await call_next(request)

    # A missing CSRF cookie can be restored by a safe request. Normal requests
    # already carry it and must not repeat the expensive session verification.
    token = request.cookies.get(COOKIE_NAME)
    if request.method in ("GET", "HEAD") and token and not request.cookies.get("csrf_token") and verify_token(token):
        import secrets
        response.set_cookie(
            "csrf_token",
            secrets.token_urlsafe(32),
            httponly=False,
            samesite="lax",
            secure=request.url.scheme == "https",
            path=cookie_path(),
            max_age=get_session_ttl_seconds(),
        )

    return response


api_router = APIRouter(prefix=settings.api_prefix)

templates = Jinja2Templates(directory="app/templates")
_login_logo_svg = (Path(__file__).parent / "static/images/logo.svg").read_text(encoding="utf-8")
LOGIN_LOGO_DATA_URI = "data:image/png;base64," + _login_logo_svg.split("base64,", 1)[1].split('"', 1)[0]
app.mount("/static", StaticFiles(directory="app/static"), name="static")
if settings.frontend_base_path != "/":
    app.mount(f"{settings.frontend_base_path}/static", StaticFiles(directory="app/static"), name="static_prefix")


@app.get("/favicon.ico", include_in_schema=False)
def get_root_favicon():
    return FileResponse("app/static/favicon.ico")


@app.get("/logo.svg", include_in_schema=False)
def get_root_logo():
    return FileResponse("app/static/images/logo.svg", media_type="image/svg+xml")





def url_for_frontend(path: str) -> str:
    base = public_base_path()
    if not base:
        if not path.startswith("/"):
            return "/" + path
        return path

    cleaned_path = path.lstrip("/")
    if not cleaned_path:
        return base + "/"
    if cleaned_path.startswith("#") or cleaned_path.startswith("?"):
        return base + "/" + cleaned_path
    return base + "/" + cleaned_path


def frontend_redirect(path: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url_for_frontend(path), status_code=status_code)


def display_name() -> str:
    return get_system_setting("app_name", settings.app_name).strip() or settings.app_name


def system_preferences() -> dict[str, str]:
    return {
        "app_name": display_name(),
        "admin_username": admin_username(),
        "access_context": public_base_path(),
        "holiday_primary_url": get_system_setting("holiday_primary_url", settings.holiday_primary_url),
        "holiday_backup_url": get_system_setting("holiday_backup_url", settings.holiday_backup_url),
    }


def validate_holiday_url(value: str) -> str:
    from urllib.parse import urlsplit

    value = value.strip()
    if not value or len(value) > 500 or value.count("{year}") != 1:
        raise HTTPException(status_code=400, detail="节假日地址必须包含一个 {year} 占位符")
    if "{" in value.replace("{year}", "") or "}" in value.replace("{year}", ""):
        raise HTTPException(status_code=400, detail="节假日地址包含无效占位符")
    try:
        parts = urlsplit(value)
        valid = parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username and not parts.password and not parts.fragment
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(status_code=400, detail="节假日地址必须是有效的 HTTP 或 HTTPS URL")
    return value


templates.env.globals["url_for_frontend"] = url_for_frontend

frontend_router = APIRouter(prefix=settings.frontend_base_path if settings.frontend_base_path != "/" else "")


@frontend_router.get("/favicon.ico", include_in_schema=False)
def get_ops_favicon():
    return FileResponse("app/static/favicon.ico")


@frontend_router.get("/logo.svg", include_in_schema=False)
def get_ops_logo():
    return FileResponse("app/static/images/logo.svg", media_type="image/svg+xml")




def status_label(value: Any) -> str:
    labels = {
        "queued": "排队中",
        "running": "运行中",
        "success": "成功",
        "partial_success": "部分成功",
        "failed": "失败",
        "interrupted": "已中断",
    }
    return labels.get(str(value or ""), str(value or "-"))


def dingtalk_status_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    labels = {
        "skipped": "未发送",
        "sent": "已发送",
        "success": "已发送",
    }
    if raw.startswith("failed:"):
        return "发送失败：" + raw.removeprefix("failed:").strip()
    return labels.get(raw, raw)


def short_date(value: Any) -> str:
    if not value:
        return "-"
    return str(value).replace("T", " ").split("+")[0][:19]


templates.env.filters["status_label"] = status_label
templates.env.filters["dingtalk_status_label"] = dingtalk_status_label
templates.env.filters["short_date"] = short_date

from app.utils import (
    build_totp_uri,
    decrypt_secret,
    generate_totp_secret,
    mask_value,
    resolve_auth_credential,
    verify_totp_code,
)

templates.env.globals["mask_value"] = mask_value
templates.env.globals["resolve_auth_credential"] = resolve_auth_credential
templates.env.globals["decrypt_secret"] = decrypt_secret


def job_form(
    name: str = Form(...),
    environment: str = Form("生产环境"),
    enabled: str | None = Form(None),
    cron_expression: str = Form("0 9 * * *"),
    time_range_label: str = Form("最近24小时"),
    report_title: str = Form("自动化巡检报告"),
    browser_width: int = Form(3840),
    browser_height: int = Form(2160),
    browser_scale_factor: float = Form(1.5),
    headless: str | None = Form(None),
    schedule_mode: str = Form("simple"),
    frequency: str = Form("daily"),
    morning_enabled: str | None = Form(None),
    morning_time: str = Form("09:05"),
    afternoon_enabled: str | None = Form(None),
    afternoon_time: str = Form("17:05"),
    dingtalk_enabled: str | None = Form(None),
    dingtalk_webhook: str | None = Form(""),
    dingtalk_secret: str | None = Form(""),
    dingtalk_keyword: str | None = Form(""),
) -> dict[str, Any]:
    from app.schedule_utils import parse_simple_inspection_schedule

    def parse_bool(v: Any) -> bool:
        return bool(v) if v is not None else False

    payload = {
        "frequency": frequency,
        "morning_enabled": parse_bool(morning_enabled),
        "morning_time": morning_time,
        "afternoon_enabled": parse_bool(afternoon_enabled),
        "afternoon_time": afternoon_time
    }

    if schedule_mode == "simple":
        try:
            cron_expr, label_expr, schedule_config = parse_simple_inspection_schedule(payload)
        except Exception as e:
            raise ValueError(str(e))
    else:
        cron_expr = cron_expression
        label_expr = "高级 Cron"
        schedule_config = ""

    return {
        "name": name,
        "environment": environment,
        "enabled": 1 if enabled else 0,
        "cron_expression": cron_expr,
        "time_range_label": time_range_label,
        "report_title": report_title,
        "mail_profile_id": None,
        "send_mail": 0,
        "send_mail_on_complete": 0,
        "send_mail_on_error": 0,
        "browser_width": browser_width,
        "browser_height": browser_height,
        "browser_scale_factor": browser_scale_factor,
        "headless": 1,
        "schedule_mode": schedule_mode,
        "schedule_label": label_expr,
        "schedule_config": schedule_config,
        "dingtalk_enabled": 1 if dingtalk_enabled else 0,
        "dingtalk_webhook": (dingtalk_webhook or "").strip(),
        "dingtalk_secret": (dingtalk_secret or "").strip(),
        "dingtalk_keyword": (dingtalk_keyword or "").strip(),
    }


def item_form(
    auth_profile_id: str | None = Form(None),
    name: str = Form(...),
    item_type: str = Form("web"),
    url: str = Form(...),
    section: str = Form("巡检截图"),
    capture_mode: str = Form("viewport"),
    css_selector: str = Form(""),
    wait_selector: str = Form(""),
    wait_seconds: float = Form(3),
    timeout_seconds: int = Form(60),
    total_timeout_seconds: int = Form(240),
    retry_count: int = Form(2),
    browser_width: str | None = Form(None),
    browser_height: str | None = Form(None),
    sort_order: int = Form(100),
    enabled: str | None = Form(None),
    real_browser_capture: str | None = Form(None),
    watermark_enabled: str | None = Form(None),
    watermark_time_enabled: str | None = Form(None),
    watermark_time_format: str = Form(DEFAULT_TIME_FORMAT),
    watermark_text: str = Form(WATERMARK_DEFAULTS["watermark_text"]),
    watermark_opacity: int = Form(WATERMARK_DEFAULTS["watermark_opacity"]),
    watermark_font_size: int = Form(WATERMARK_DEFAULTS["watermark_font_size"]),
    watermark_gap_x: int = Form(WATERMARK_DEFAULTS["watermark_gap_x"]),
    watermark_gap_y: int = Form(WATERMARK_DEFAULTS["watermark_gap_y"]),
    watermark_angle: int = Form(WATERMARK_DEFAULTS["watermark_angle"]),
    watermark_alignment: str = Form(WATERMARK_DEFAULTS["watermark_alignment"]),
    taskbar_enabled: str | None = Form(None),
) -> dict[str, Any]:
    real_capture_enabled = 1 if real_browser_capture else 0
    normalized_capture_mode = capture_mode
    normalized_css_selector = css_selector
    if real_capture_enabled:
        normalized_capture_mode = "viewport"
        normalized_css_selector = ""
    try:
        validated_time_format = validate_time_format(watermark_time_format)
        validated_alignment = validate_watermark_alignment(watermark_alignment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if watermark_enabled and not watermark_time_enabled and not watermark_text.strip():
        raise HTTPException(status_code=400, detail="请启用时间水印或填写自定义水印内容")
    if len(watermark_text) > MAX_WATERMARK_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail="水印文字不能超过 200 字")

    return {
        "job_id": None,
        "auth_profile_id": parse_optional_int(auth_profile_id),
        "name": name,
        "item_type": item_type,
        "url": url,
        "section": section,
        "capture_mode": normalized_capture_mode,
        "css_selector": normalized_css_selector,
        "wait_selector": wait_selector,
        "wait_seconds": wait_seconds,
        "timeout_seconds": timeout_seconds,
        "total_timeout_seconds": max(1, total_timeout_seconds),
        "retry_count": retry_count,
        "browser_width": parse_optional_int(browser_width),
        "browser_height": parse_optional_int(browser_height),
        "sort_order": sort_order,
        "enabled": 1 if enabled else 0,
        "real_browser_capture": real_capture_enabled,
        "watermark_enabled": 1 if watermark_enabled else 0,
        "watermark_time_enabled": 1 if watermark_time_enabled else 0,
        "watermark_time_format": validated_time_format,
        "watermark_text": watermark_text,
        "watermark_opacity": max(0, min(255, int(watermark_opacity))),
        "watermark_font_size": max(10, min(72, int(watermark_font_size))),
        "watermark_gap_x": max(20, min(800, int(watermark_gap_x))),
        "watermark_gap_y": max(20, min(800, int(watermark_gap_y))),
        "watermark_angle": max(-90, min(90, int(watermark_angle))),
        "watermark_alignment": validated_alignment,
        "taskbar_enabled": 1 if taskbar_enabled else 0,
    }


def auth_form(
    name: str = Form(...),
    auth_type: str = Form("form"),
    login_url: str = Form(""),
    username_value: str = Form(""),
    password: str = Form(""),
    username_selector: str = Form('input[name="user"], input[name="username"], input[type="email"], input[placeholder*="username"], input[placeholder*="email"], input[placeholder*="user"]'),
    password_selector: str = Form('input[name="password"], input[type="password"], input[placeholder*="password"]'),
    submit_selector: str = Form('button[type="submit"], button:has-text("Log in"), button:has-text("Login"), button:has-text("登录"), input[type="submit"]'),
    success_selector: str = Form(""),
    storage_state_path: str = Form(""),
) -> dict[str, Any]:
    return {
        "name": name,
        "auth_type": auth_type,
        "login_url": login_url,
        "username": username_value,
        "password": password,
        "username_value": username_value,
        "username_source": "plain",
        "password_source": "plain",
        "username_env": "",
        "password_env": "",
        "username_selector": username_selector,
        "password_selector": password_selector,
        "submit_selector": submit_selector,
        "success_selector": success_selector,
        "storage_state_path": storage_state_path,
    }


def _ensure_chrome_policies() -> None:
    import platform
    if platform.system().lower() == "linux":
        try:
            for p in ["/etc/opt/chrome/policies/managed", "/etc/chromium/policies/managed"]:
                os.makedirs(p, exist_ok=True)
                fpath = os.path.join(p, "managed_policies.json")
                if not os.path.exists(fpath):
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write('{"TranslateEnabled": false, "PasswordManagerEnabled": false}\n')
        except Exception:
            pass

@app.on_event("startup")
def on_startup() -> None:
    _ensure_chrome_policies()
    init_db()
    app.title = display_name()
    setup_code = ensure_setup_code()
    if setup_code:
        print(f"SETUP_CODE={setup_code}", flush=True)
    try:
        recovered = recover_interrupted_runs(include_deleted_jobs=True, queued_grace_seconds=0)
        if recovered:
            app_logger.warning("已将 %s 条中断的巡检记录标记为已中断", recovered)
    except Exception:
        app_logger.exception("修复中断的巡检记录失败")
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    shutdown_scheduler()


@frontend_router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> Response:
    if installation_state() != "pending":
        return frontend_redirect("/login")
    import secrets
    import shlex
    import socket

    csrf_token = secrets.token_urlsafe(32)
    log_target = shlex.quote(socket.gethostname()) if Path("/.dockerenv").exists() else "<容器名或ID>"
    response = templates.TemplateResponse(
        request,
        "setup.html",
        {"request": request, "csrf_token": csrf_token,
         "error": request.query_params.get("error", ""),
         "default_holiday_primary_url": settings.holiday_primary_url,
         "default_holiday_backup_url": settings.holiday_backup_url,
         "setup_log_command": f"docker logs {log_target} 2>&1 | grep '^SETUP_CODE='",
         "login_logo_data_uri": LOGIN_LOGO_DATA_URI},
    )
    is_secure = request.url.scheme == "https"
    response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="lax",
                        secure=is_secure, path="/",
                        max_age=get_session_ttl_seconds())
    response.headers["Cache-Control"] = "no-store"
    return response


@frontend_router.post("/setup/verify-code")
def setup_verify_code(request: Request, setup_code: str = Form("")) -> dict[str, bool]:
    client_host = request.client.host if request.client else None
    retry_after = consume_login_attempt(client_host)
    if retry_after:
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后重试", headers={"Retry-After": str(retry_after)})
    if not verify_setup_code(setup_code.strip()):
        raise HTTPException(status_code=400, detail="初始化码不正确")
    return {"ok": True}


@frontend_router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    setup_code: str = Form(""),
    app_name: str = Form("运维巡检平台"),
    access_context: str = Form(""),
    holiday_primary_url: str = Form(""),
    holiday_backup_url: str = Form(""),
) -> RedirectResponse:
    import re

    if installation_state() != "pending":
        return frontend_redirect("/login")
    client_host = request.client.host if request.client else None
    retry_after = consume_login_attempt(client_host)
    if retry_after:
        response = frontend_redirect("/setup?error=rate")
        response.headers["Retry-After"] = str(retry_after)
        return response
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        return frontend_redirect("/setup?error=username")
    if len(password) < 12 or len(password) > 128 or password != password_confirm:
        return frontend_redirect("/setup?error=password")
    name = app_name.strip()
    if not name or len(name) > 40 or any(ord(char) < 32 for char in name):
        return frontend_redirect("/setup?error=app_name")
    try:
        access_context = normalize_access_context(access_context)
    except ValueError:
        return frontend_redirect("/setup?error=access_context")
    try:
        primary = validate_holiday_url(holiday_primary_url or settings.holiday_primary_url)
        backup = validate_holiday_url(holiday_backup_url or settings.holiday_backup_url)
    except HTTPException:
        return frontend_redirect("/setup?error=holiday_url")
    if not complete_setup(username, password, setup_code.strip(), preferences={
        "app_name": name,
        "access_context": access_context,
        "holiday_primary_url": primary,
        "holiday_backup_url": backup,
    }):
        return frontend_redirect("/setup?error=code")
    clear_login_attempts(client_host)
    app.title = name
    app.openapi_schema = None
    return frontend_redirect("/login")


@frontend_router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if installation_state() == "pending":
        return frontend_redirect("/setup")
    mfa_enabled = get_system_setting(MFA_ENABLED_KEY, "0").strip().lower() in {"1", "true", "yes", "on", "enabled"}
    
    # 每次渲染登录页面时，主动生成并下发一个新的强随机 csrf_token，确保页面表单与 Cookie 状态保持一致
    import secrets
    csrf_token = secrets.token_urlsafe(32)
    
    response = templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": request.query_params.get("error", ""),
            "mfa_enabled": mfa_enabled,
            "login_public_key": get_login_public_jwk(),
            "login_logo_data_uri": LOGIN_LOGO_DATA_URI,
            "app_name": display_name(),
            "csrf_token": csrf_token,
        },
    )
    
    # 动态识别请求协议，自适应设置 secure 属性
    is_secure = request.url.scheme == "https"
    current_cookie_path = cookie_path()
    # The setup form uses a root-scoped CSRF cookie. After choosing /ops (or
    # another context), keeping it would send two cookies with this name and
    # Starlette could read the stale root value during the first login.
    if current_cookie_path != "/":
        response.delete_cookie("csrf_token", path="/")
    
    response.set_cookie(
        "csrf_token",
        csrf_token,
        httponly=False,
        samesite="lax",
        secure=is_secure,
        path=current_cookie_path,
        max_age=get_session_ttl_seconds(),
    )
    
    # 检查请求中是否带有旧的且已经失效的会话 Cookie。如果是，主动从浏览器中清理它，避免干扰
    token = request.cookies.get(COOKIE_NAME)
    if token and not verify_token(token):
        response.delete_cookie(COOKIE_NAME, path=current_cookie_path)
        
    # 添加防缓存头部，避免浏览器或代理缓存登录页面
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    return response


@frontend_router.post("/login")
def login(
    request: Request,
    login_payload: str = Form(""),
) -> RedirectResponse:
    if installation_state() == "pending":
        return frontend_redirect("/setup")
    client_host = request.client.host if request.client else None
    retry_after = consume_login_attempt(client_host)
    if retry_after:
        response = frontend_redirect("/login?error=rate")
        response.headers["Retry-After"] = str(retry_after)
        return response
    if not login_payload:
        return frontend_redirect("/login?error=1")
    try:
        payload = decrypt_login_payload(login_payload)
    except LoginPayloadError:
        return frontend_redirect("/login?error=1")
    username = payload["username"]
    password = payload["password"]
    mfa_code = payload["mfa_code"]

    if not check_password(username, password):
        return frontend_redirect("/login?error=1")
    if get_system_setting(MFA_ENABLED_KEY, "0").strip().lower() in {"1", "true", "yes", "on", "enabled"}:
        secret = current_mfa_secret()
        if not secret or not verify_totp_code(secret, mfa_code):
            return frontend_redirect("/login?error=mfa")
    clear_login_attempts(client_host)
    response = frontend_redirect("/")
    is_secure = request.url.scheme == "https"
    current_cookie_path = cookie_path()
    import secrets
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        "csrf_token",
        csrf_token,
        httponly=False,
        samesite="lax",
        secure=is_secure,
        path=current_cookie_path,
        max_age=get_session_ttl_seconds(),
    )
    response.set_cookie(
        COOKIE_NAME,
        create_idle_token(username),
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path=current_cookie_path,
        max_age=get_session_ttl_seconds(),
    )
    # 添加防缓存头部，防止代理缓存登录状态
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@frontend_router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        from app.auth import revoke_token
        revoke_token(token)
    response = frontend_redirect("/login")
    current_cookie_path = cookie_path()
    response.delete_cookie(COOKIE_NAME, path=current_cookie_path)
    response.delete_cookie("csrf_token", path=current_cookie_path)
    # 添加防缓存头部
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@frontend_router.get("/app-config.json")
def get_app_config() -> dict[str, str]:
    return {
        "apiBase": public_api_prefix(),
        "frontendBase": public_base_path() or "/",
        "artifactBase": url_for_frontend("artifact")
    }


SWAGGER_SETTING_KEY = "swagger_enabled"
MFA_ENABLED_KEY = "mfa_enabled"
MFA_SECRET_KEY = "mfa_totp_secret"
MFA_PENDING_SECRET_KEY = "mfa_totp_pending_secret"
MFA_PENDING_LAST_SLICE_KEY = "last_used_pending_totp_slice"
DINGTALK_EMOJI_ENABLED_KEY = "dingtalk_emoji_enabled"


def security_settings() -> dict[str, Any]:
    from app.utils import decrypt_secret

    db_webhook = get_system_setting("dingtalk_webhook", "")
    if db_webhook:
        try:
            db_webhook = decrypt_secret(db_webhook)
        except Exception:
            pass

    db_secret = get_system_setting("dingtalk_secret", "")
    if db_secret:
        try:
            db_secret = decrypt_secret(db_secret)
        except Exception:
            pass

    db_keyword = get_system_setting("dingtalk_keyword", "")

    return {
        "mfa_enabled": get_system_setting(MFA_ENABLED_KEY, "0").strip().lower() in {"1", "true", "yes", "on", "enabled"},
        "session_ttl_minutes": get_session_ttl_seconds() // 60,
        "mfa_configured": bool(get_system_setting(MFA_SECRET_KEY, "")),
        "dingtalk_emoji_enabled": get_system_setting(DINGTALK_EMOJI_ENABLED_KEY, "0").strip().lower() in {"1", "true", "yes", "on", "enabled"},
        "dingtalk_webhook": db_webhook,
        "dingtalk_secret": db_secret,
        "has_dingtalk_secret": bool(db_secret),
        "dingtalk_keyword": db_keyword,
    }


def current_mfa_secret() -> str:
    return decrypt_secret(get_system_setting(MFA_SECRET_KEY, ""))


def swagger_docs_enabled() -> bool:
    return get_system_setting(SWAGGER_SETTING_KEY, "0").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def require_swagger_access(request: Request) -> None:
    require_login(request)
    if not swagger_docs_enabled():
        raise HTTPException(status_code=404, detail="接口文档未开启")


@frontend_router.get("/openapi.json", include_in_schema=False)
def openapi_json(_: None = Depends(require_swagger_access)) -> dict[str, Any]:
    from copy import deepcopy

    schema = deepcopy(app.openapi())
    schema["paths"] = {
        public_api_prefix() + path[len(settings.api_prefix):]: item
        for path, item in schema["paths"].items()
    }
    return schema


@frontend_router.get("/docs", include_in_schema=False)
def swagger_docs(_: None = Depends(require_swagger_access)) -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=url_for_frontend("/openapi.json"),
        title=f"{display_name()} API 文档",
    )


@frontend_router.get("/redoc", include_in_schema=False)
def redoc_docs(_: None = Depends(require_swagger_access)) -> HTMLResponse:
    return get_redoc_html(
        openapi_url=url_for_frontend("/openapi.json"),
        title=f"{display_name()} API 文档",
    )


@frontend_router.get("/swagger", include_in_schema=False)
def swagger_page(_: None = Depends(require_swagger_access)) -> RedirectResponse:
    return frontend_redirect("/docs")


def require_api_login(request: Request) -> None:
    if verify_token(request.cookies.get(COOKIE_NAME)):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


api_status_label = status_label


def bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def text_value(payload: dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def int_value(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail="参数格式错误") from e


def float_value(payload: dict[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail="参数格式错误") from e


def clamp_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail="参数格式错误") from e
    return max(min_value, min(max_value, parsed))


def optional_clamped_int(value: Any, min_value: int, max_value: int) -> int | None:
    if value in (None, ""):
        return None
    return clamp_int(value, min_value, min_value, max_value)


def public_auth_profile(profile: dict[str, Any]) -> dict[str, Any]:
    data = dict(profile)
    for key in ("password", "password_secret"):
        data.pop(key, None)
    data["has_password"] = bool(profile.get("password") or profile.get("password_secret"))
    data["has_username"] = bool(profile.get("username") or profile.get("username_value"))
    return data


def public_dingtalk_profile(profile: dict[str, Any]) -> dict[str, Any]:
    from app.utils import decrypt_secret
    data = dict(profile)
    raw_webhook = data.get("webhook", "")
    if raw_webhook:
        try:
            raw_webhook = decrypt_secret(raw_webhook)
        except Exception:
            pass
    data["webhook"] = raw_webhook
    data["has_secret"] = bool(profile.get("secret"))
    data["secret"] = ""
    return data


def api_dingtalk_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": text_value(payload, "name", "未命名机器人").strip(),
        "webhook": text_value(payload, "webhook", "").strip(),
        "secret": text_value(payload, "secret", "").strip(),
        "keyword": text_value(payload, "keyword", "").strip(),
        "emoji_enabled": int(bool_value(payload.get("emoji_enabled"), True)),
    }


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    data = dict(job)
    data["has_dingtalk_webhook"] = bool(job.get("dingtalk_webhook"))
    data["has_dingtalk_secret"] = bool(job.get("dingtalk_secret"))
    data["dingtalk_webhook"] = ""
    data["dingtalk_secret"] = ""
    return data


def public_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_job(job) for job in jobs]


@api_router.get("/swagger-settings", dependencies=[Depends(require_api_login)])
def api_get_swagger_settings() -> dict[str, Any]:
    return {"enabled": swagger_docs_enabled()}


@api_router.put("/swagger-settings", dependencies=[Depends(require_api_login)])
def api_update_swagger_settings(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool_value(payload.get("enabled"), False)
    set_system_setting(SWAGGER_SETTING_KEY, "1" if enabled else "0")
    return {"ok": True, "enabled": bool(enabled)}


@api_router.get("/security-settings", dependencies=[Depends(require_api_login)])
def api_get_security_settings() -> dict[str, Any]:
    data = security_settings()
    data["mfa_secret"] = ""
    data["mfa_otpauth_url"] = ""
    data["qr_uri"] = ""
    return data


@api_router.get("/system-preferences", dependencies=[Depends(require_api_login)])
def api_get_system_preferences() -> dict[str, str]:
    return system_preferences()


@api_router.put("/system-preferences", dependencies=[Depends(require_api_login)])
def api_save_system_preferences(payload: dict[str, Any], background_tasks: BackgroundTasks,
                                request: Request, response: Response) -> dict[str, Any]:
    name = text_value(payload, "app_name", "").strip()
    if not name or len(name) > 40 or any(ord(char) < 32 for char in name):
        raise HTTPException(status_code=400, detail="系统名称需为 1–40 个可见字符")
    primary = validate_holiday_url(text_value(payload, "holiday_primary_url", ""))
    backup = validate_holiday_url(text_value(payload, "holiday_backup_url", ""))
    old_cookie_path = cookie_path()
    try:
        access_context = normalize_access_context(text_value(payload, "access_context", public_base_path()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from app.db import connect
    with connect() as conn:
        for key, value in (("app_name", name), ("access_context", access_context),
                           ("holiday_primary_url", primary), ("holiday_backup_url", backup)):
            conn.execute(
                "REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, value),
            )
    from app.holiday_calendar import refresh_current_year
    background_tasks.add_task(refresh_current_year)
    if old_cookie_path != cookie_path():
        is_secure = request.url.scheme == "https"
        token = request.cookies.get(COOKIE_NAME)
        csrf_token = request.cookies.get("csrf_token")
        response.delete_cookie(COOKIE_NAME, path=old_cookie_path)
        response.delete_cookie("csrf_token", path=old_cookie_path)
        if token:
            response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=is_secure,
                                path=cookie_path(), max_age=get_session_ttl_seconds())
        if csrf_token:
            response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="lax", secure=is_secure,
                                path=cookie_path(), max_age=get_session_ttl_seconds())
    app.title = name
    app.openapi_schema = None
    return {"ok": True, **system_preferences()}


@api_router.put("/admin/username", dependencies=[Depends(require_api_login)])
def api_change_admin_username(request: Request, payload: dict[str, Any]) -> dict[str, bool]:
    import re

    username = text_value(payload, "username", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username) or username == admin_username():
        raise HTTPException(status_code=400, detail="新账号需为 3–32 位字母、数字、下划线、点或连字符，且不能与旧账号相同")
    client_host = request.client.host if request.client else None
    retry_after = consume_login_attempt(client_host)
    if retry_after:
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后重试", headers={"Retry-After": str(retry_after)})
    if not change_admin_username(text_value(payload, "current_password", ""), username):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    clear_login_attempts(client_host)
    return {"ok": True}


@api_router.put("/admin/password", dependencies=[Depends(require_api_login)])
def api_change_admin_password(request: Request, payload: dict[str, Any]) -> dict[str, bool]:
    current_password = text_value(payload, "current_password", "")
    new_password = text_value(payload, "new_password", "")
    if len(new_password) < 12 or len(new_password) > 128 or new_password == current_password:
        raise HTTPException(status_code=400, detail="新密码需为 12–128 位，且不能与旧密码相同")
    client_host = request.client.host if request.client else None
    retry_after = consume_login_attempt(client_host)
    if retry_after:
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后重试", headers={"Retry-After": str(retry_after)})
    if not change_admin_password(current_password, new_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    clear_login_attempts(client_host)
    return {"ok": True}


@api_router.put("/security-settings", dependencies=[Depends(require_api_login)])
def api_update_security_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current_settings = security_settings()
    was_mfa_enabled = current_settings["mfa_enabled"]
    mfa_enabled = bool_value(payload.get("mfa_enabled"), was_mfa_enabled)
    verify_code = text_value(payload, "mfa_code", "")
    current_code = text_value(payload, "mfa_current_code", "")
    current_secret = current_mfa_secret()
    pending_secret = decrypt_secret(get_system_setting(MFA_PENDING_SECRET_KEY, ""))
    if was_mfa_enabled and not mfa_enabled:
        if not current_secret or not verify_totp_code(current_secret, current_code):
            raise HTTPException(status_code=400, detail="当前 MFA 验证码不正确")
    if was_mfa_enabled and pending_secret and verify_code:
        if not current_secret or not verify_totp_code(current_secret, current_code):
            raise HTTPException(status_code=400, detail="当前 MFA 验证码不正确")
    secret_to_verify = pending_secret if verify_code and pending_secret else current_secret

    if mfa_enabled and not secret_to_verify:
        raise HTTPException(status_code=400, detail="请先生成并绑定 MFA 认证")

    if mfa_enabled and (verify_code or not was_mfa_enabled):
        replay_key = MFA_PENDING_LAST_SLICE_KEY if verify_code and pending_secret else "last_used_totp_slice"
        if not verify_totp_code(secret_to_verify, verify_code, replay_key=replay_key):
            raise HTTPException(status_code=400, detail="MFA 验证码不正确")

    if mfa_enabled and verify_code and pending_secret:
        from app.utils import encrypt_secret
        set_system_setting(MFA_SECRET_KEY, encrypt_secret(pending_secret))
        set_system_setting(MFA_PENDING_SECRET_KEY, "")
        used_slice = get_system_setting(MFA_PENDING_LAST_SLICE_KEY, "")
        if used_slice:
            set_system_setting("last_used_totp_slice", used_slice)
        set_system_setting(MFA_PENDING_LAST_SLICE_KEY, "")

    set_system_setting(MFA_ENABLED_KEY, "1" if mfa_enabled else "0")

    # 更新全局钉钉消息表情开关
    dingtalk_emoji_enabled = bool_value(payload.get("dingtalk_emoji_enabled"), current_settings["dingtalk_emoji_enabled"])
    set_system_setting(DINGTALK_EMOJI_ENABLED_KEY, "1" if dingtalk_emoji_enabled else "0")

    # 更新全局钉钉机器人配置（并进行加密保护）
    # 注意：若提交时字段为空，则保留数据库已有的值（防止误清空已保存的 Webhook）
    from app.utils import encrypt_secret

    global_webhook = text_value(payload, "dingtalk_webhook", "").strip()
    if global_webhook:
        set_system_setting("dingtalk_webhook", encrypt_secret(global_webhook))
    elif "dingtalk_webhook" in payload and not global_webhook:
        # 用户明确填写了空字符串（intent to clear），才清空；否则保留原有值
        # 前端需要传 dingtalk_webhook_clear=true 来主动清除
        if payload.get("dingtalk_webhook_clear"):
            set_system_setting("dingtalk_webhook", "")
        # 字段存在但为空且没有明确清除标记 → 保留原有值，什么都不做

    global_secret = text_value(payload, "dingtalk_secret", "").strip()
    if global_secret:
        set_system_setting("dingtalk_secret", encrypt_secret(global_secret))
    elif payload.get("dingtalk_secret_clear"):
        set_system_setting("dingtalk_secret", "")

    if "dingtalk_keyword" in payload:
        global_keyword = text_value(payload, "dingtalk_keyword", "").strip()
        set_system_setting("dingtalk_keyword", global_keyword)

    return {"ok": True, **security_settings()}


@api_router.post("/security-settings/mfa-qr", dependencies=[Depends(require_api_login)])
def api_get_current_mfa_qr(payload: dict[str, Any]) -> dict[str, Any]:
    secret = current_mfa_secret()
    if not secret:
        raise HTTPException(status_code=400, detail="未配置两步验证密钥")
    if not verify_totp_code(secret, text_value(payload, "mfa_current_code", "")):
        raise HTTPException(status_code=400, detail="当前 MFA 验证码不正确")
    otpauth_url = build_totp_uri(secret, admin_username(), display_name())
    return {
        "ok": True,
        "mfa_secret": secret,
        "mfa_otpauth_url": otpauth_url,
        "secret": secret,
        "qr_uri": otpauth_url,
    }


@api_router.post("/security-settings/mfa-secret", dependencies=[Depends(require_api_login)])
def api_generate_mfa_secret() -> dict[str, Any]:
    from app.utils import encrypt_secret

    secret = generate_totp_secret()
    set_system_setting(MFA_PENDING_SECRET_KEY, encrypt_secret(secret))
    set_system_setting(MFA_PENDING_LAST_SLICE_KEY, "")
    otpauth_url = build_totp_uri(secret, admin_username(), display_name())
    return {
        "ok": True,
        "mfa_enabled": security_settings()["mfa_enabled"],
        "mfa_configured": bool(current_mfa_secret()),
        "mfa_secret": secret,
        "mfa_otpauth_url": otpauth_url,
        "secret": secret,
        "qr_uri": otpauth_url,
    }


@api_router.post("/security-settings/reset-all-jobs", dependencies=[Depends(require_api_login)])
def api_reset_all_jobs(payload: dict[str, Any]) -> dict[str, Any]:
    from app.repository import get_system_setting, connect
    from app.scheduler import reload_jobs
    import shutil
    import os

    # 1. 强校验全局 MFA 是否启用
    mfa_enabled = get_system_setting(MFA_ENABLED_KEY, "0").strip().lower() in {"1", "true", "yes", "on", "enabled"}
    if not mfa_enabled:
        raise HTTPException(
            status_code=403,
            detail="此操作属于高危操作，必须开启 MFA 二步验证后才能使用。请前往【安全设置】开启 MFA 认证后重试。"
        )

    # 2. 强校验 MFA TOTP 验证码
    verify_code = text_value(payload, "mfa_code", "")
    current_secret = current_mfa_secret()
    if not verify_totp_code(current_secret, verify_code):
        raise HTTPException(status_code=400, detail="MFA 验证码不正确，无法执行此高危操作。")

    app_logger.warning("用户通过了 MFA 安全校验，开始执行【一键清空运行历史数据】操作（保留任务配置）...")

    # 3. 只清理运行历史相关表，保留 report_jobs / screenshot_items 任务配置
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in ("run_records", "screenshot_results", "periodic_report_runs"):
            conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        conn.execute("PRAGMA foreign_keys = ON")

    # All generated screenshots, daily reports and periodic ZIPs live here.
    if settings.report_dir.exists():
        for item in settings.report_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except Exception as exc:
                app_logger.error(f"物理删除 {item} 失败: {exc}", exc_info=True)

    # 5. 刷新后台任务调度
    reload_jobs()

    app_logger.info("一键清空运行历史完成！任务配置保留，历史记录和物理文件已清除。")
    return {"ok": True, "message": "所有运行历史记录和截图文件已彻底清空。巡检任务配置保留，自增 ID 从 #1 重新开始。"}


def template_id_from_payload(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        template_id = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="报告模板无效") from exc
    if template_id <= 0 or get_template(template_id) is None:
        raise HTTPException(status_code=400, detail="所选报告模板不存在")
    return template_id


def job_template_sections(job: dict[str, Any]) -> list[str]:
    if not job.get("template_id"):
        return []
    template = get_template(int(job["template_id"]))
    if not template:
        return []
    return list_template_sections(template_path(template))


def api_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from app.schedule_utils import parse_simple_inspection_schedule
    from app.date_schedule import label as calendar_label, normalize_schedule
    from app.schedule_migration import _job_config
    import json

    schedule_mode = text_value(payload, "schedule_mode", "simple")
    cron_expr = ""
    schedule_label = ""
    schedule_config = ""

    if schedule_mode == "simple":
        try:
            _, _, old_config = parse_simple_inspection_schedule(payload)
            config = _job_config(old_config)
            schedule_mode = "calendar"
            schedule_label = calendar_label(config)
            schedule_config = json.dumps(config, ensure_ascii=False)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif schedule_mode == "calendar":
        try:
            config = normalize_schedule(payload, enforce_daily_slots=True)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        cron_expr = ""
        schedule_label = calendar_label(config)
        schedule_config = json.dumps(config, ensure_ascii=False)
    else:
        raise HTTPException(status_code=400, detail="旧定时方式已停用，请选择日期计划")

    send_dingtalk_on_complete = bool_value(payload.get("send_dingtalk_on_complete"), True)
    send_dingtalk_on_error = bool_value(payload.get("send_dingtalk_on_error"), True)

    return {
        "name": text_value(payload, "name", "未命名巡检任务"),
        "environment": text_value(payload, "environment", JOB_FORM_DEFAULTS["environment"]),
        "enabled": bool_value(payload.get("enabled"), True),
        "cron_expression": cron_expr,
        "time_range_label": text_value(payload, "time_range_label", JOB_FORM_DEFAULTS["time_range_label"]),
        "report_title": text_value(payload, "report_title", JOB_FORM_DEFAULTS["report_title"]),
        "template_id": template_id_from_payload(payload.get("template_id")),
        "mail_profile_id": None,
        "send_mail": 0,
        "send_mail_on_complete": 0,
        "send_mail_on_error": 0,
        "browser_width": clamp_int(payload.get("browser_width"), JOB_FORM_DEFAULTS["browser_width"], WATERMARK_RULES["image_width"]["min"], WATERMARK_RULES["image_width"]["max"]),
        "browser_height": clamp_int(payload.get("browser_height"), JOB_FORM_DEFAULTS["browser_height"], WATERMARK_RULES["image_height"]["min"], WATERMARK_RULES["image_height"]["max"]),
        "browser_scale_factor": float_value(payload, "browser_scale_factor", JOB_FORM_DEFAULTS["browser_scale_factor"]),
        "headless": True,
        "schedule_mode": schedule_mode,
        "schedule_label": schedule_label,
        "schedule_config": schedule_config,
        "dingtalk_enabled": bool_value(payload.get("dingtalk_enabled"), False),
        "dingtalk_profile_id": int_value(payload, "dingtalk_profile_id", None) if payload.get("dingtalk_profile_id") else None,
        "send_dingtalk_on_complete": 1 if send_dingtalk_on_complete else 0,
        "send_dingtalk_on_error": 1 if send_dingtalk_on_error else 0,
        "dingtalk_webhook": text_value(payload, "dingtalk_webhook", ""),
        "dingtalk_secret": text_value(payload, "dingtalk_secret", ""),
        "dingtalk_keyword": text_value(payload, "dingtalk_keyword", ""),
    }


def taskbar_key_from_payload(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    try:
        selected_taskbar_path(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return key


def api_item_payload(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    real_browser_capture = bool_value(payload.get("real_browser_capture"), False)
    capture_mode = text_value(payload, "capture_mode", "viewport")
    css_selector = text_value(payload, "css_selector", "")
    if real_browser_capture:
        capture_mode = "viewport"
        css_selector = ""

    watermark_text = text_value(payload, "watermark_text", WATERMARK_DEFAULTS["watermark_text"])
    legacy_time = "watermark_time_enabled" not in payload and "{time}" in watermark_text
    if legacy_time:
        watermark_text = watermark_text.replace("{time}", "").strip()
    if len(watermark_text) > MAX_WATERMARK_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail="水印文字不能超过 200 字")
    try:
        time_format = validate_time_format(text_value(payload, "watermark_time_format", DEFAULT_TIME_FORMAT))
        watermark_alignment = validate_watermark_alignment(text_value(payload, "watermark_alignment", WATERMARK_DEFAULTS["watermark_alignment"]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    watermark_time_enabled = bool_value(payload.get("watermark_time_enabled"), legacy_time or "watermark_text" not in payload)
    if bool_value(payload.get("watermark_enabled"), False) and not watermark_time_enabled and not watermark_text:
        raise HTTPException(status_code=400, detail="请启用时间水印或填写自定义水印内容")

    return {
        "job_id": job_id,
        "auth_profile_id": parse_optional_int(str(payload.get("auth_profile_id") or "")),
        "name": text_value(payload, "name", "未命名截图项"),
        "item_type": text_value(payload, "item_type", ITEM_FORM_DEFAULTS["item_type"]),
        "url": text_value(payload, "url", ""),
        "section": text_value(payload, "section", ITEM_FORM_DEFAULTS["section"]),
        "capture_mode": capture_mode,
        "css_selector": css_selector,
        "wait_selector": text_value(payload, "wait_selector", ""),
        "wait_seconds": float_value(payload, "wait_seconds", ITEM_FORM_DEFAULTS["wait_seconds"]),
        "timeout_seconds": int_value(payload, "timeout_seconds", ITEM_FORM_DEFAULTS["timeout_seconds"]),
        "total_timeout_seconds": max(1, int_value(payload, "total_timeout_seconds", ITEM_FORM_DEFAULTS["total_timeout_seconds"])),
        "retry_count": int_value(payload, "retry_count", ITEM_FORM_DEFAULTS["retry_count"]),
        "browser_width": optional_clamped_int(payload.get("browser_width"), WATERMARK_RULES["image_width"]["min"], WATERMARK_RULES["image_width"]["max"]),
        "browser_height": optional_clamped_int(payload.get("browser_height"), WATERMARK_RULES["image_height"]["min"], WATERMARK_RULES["image_height"]["max"]),
        "sort_order": int_value(payload, "sort_order", ITEM_FORM_DEFAULTS["sort_order"]),
        "enabled": bool_value(payload.get("enabled"), True),
        "real_browser_capture": 1 if real_browser_capture else 0,
        "watermark_enabled": bool_value(payload.get("watermark_enabled"), bool(WATERMARK_DEFAULTS["watermark_enabled"])),
        "watermark_time_enabled": 1 if watermark_time_enabled else 0,
        "watermark_time_format": time_format,
        "watermark_text": watermark_text,
        "watermark_opacity": clamp_int(payload.get("watermark_opacity"), WATERMARK_DEFAULTS["watermark_opacity"], WATERMARK_RULES["opacity"]["min"], WATERMARK_RULES["opacity"]["max"]),
        "watermark_font_size": clamp_int(payload.get("watermark_font_size"), WATERMARK_DEFAULTS["watermark_font_size"], 10, 72),
        "watermark_gap_x": clamp_int(payload.get("watermark_gap_x"), WATERMARK_DEFAULTS["watermark_gap_x"], 20, 800),
        "watermark_gap_y": clamp_int(payload.get("watermark_gap_y"), WATERMARK_DEFAULTS["watermark_gap_y"], 20, 800),
        "watermark_angle": clamp_int(payload.get("watermark_angle"), WATERMARK_DEFAULTS["watermark_angle"], WATERMARK_RULES["angle"]["min"], WATERMARK_RULES["angle"]["max"]),
        "watermark_alignment": watermark_alignment,
        "taskbar_enabled": 1 if bool_value(payload.get("taskbar_enabled"), True) else 0,
        "taskbar_template_key": taskbar_key_from_payload(payload.get("taskbar_template_key")),
    }


def assign_item_report_section(data: dict[str, Any], job: dict[str, Any], template_sections: list[str] | None = None) -> None:
    """Auto layout follows the screenshot name; templates use their own headings."""
    if not job.get("template_id"):
        data["section"] = data["name"]
        return
    sections = template_sections if template_sections is not None else job_template_sections(job)
    if data.get("section") not in sections:
        raise HTTPException(status_code=400, detail="请选择 Word 模板中的报告章节")


def api_auth_payload(payload: dict[str, Any]) -> dict[str, Any]:
    username = text_value(payload, "username_value", text_value(payload, "username", ""))
    return {
        "name": text_value(payload, "name", "未命名认证"),
        "auth_type": text_value(payload, "auth_type", "form"),
        "login_url": text_value(payload, "login_url", ""),
        "username": username,
        "password": text_value(payload, "password", ""),
        "username_source": text_value(payload, "username_source", "plain"),
        "password_source": text_value(payload, "password_source", "plain"),
        "username_value": username,
        "password_secret": "",
        "username_env": text_value(payload, "username_env", ""),
        "password_env": text_value(payload, "password_env", ""),
        "username_selector": text_value(payload, "username_selector", 'input[name="user"], input[name="username"], input[type="email"], input[placeholder*="username"], input[placeholder*="email"], input[placeholder*="user"]'),
        "password_selector": text_value(payload, "password_selector", 'input[name="password"], input[type="password"], input[placeholder*="password"]'),
        "submit_selector": text_value(payload, "submit_selector", 'button[type="submit"], button:has-text("Log in"), button:has-text("Login"), button:has-text("登录"), input[type="submit"]'),
        "success_selector": text_value(payload, "success_selector", ""),
        "storage_state_path": text_value(payload, "storage_state_path", ""),
    }


def save_named_profile_or_http_error(save: Any, data: dict[str, Any], profile_id: int | None = None) -> int:
    try:
        return save(data) if profile_id is None else save(data, profile_id)
    except db_sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="配置名称已存在，请更换名称") from exc
        raise HTTPException(status_code=400, detail="配置数据不合法") from exc


def clear_auth_state_cache(auth_profile: dict[str, Any]) -> None:
    from app.screenshot import safe_delete_auth_state
    safe_delete_auth_state(auth_profile)


def dashboard_metrics(jobs: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "jobs_total": len(jobs),
        "jobs_enabled": sum(1 for job in jobs if int(job.get("enabled") or 0)),
        "runs_success": sum(1 for run in runs if run.get("status") == "success"),
        "runs_failed": sum(1 for run in runs if run.get("status") in {"failed", "interrupted"}),
        "runs_running": sum(1 for run in runs if run.get("status") in {"queued", "running"}),
    }


def artifact_filename(file_path: str) -> str:
    return str(file_path or "").replace("\\", "/").rsplit("/", 1)[-1]


def daily_report_filename(file_path: str) -> str:
    """Do not expose old per-run manual suffixes as the daily download name."""
    return re.sub(r"-手动巡检-\d+(?=\.docx$)", "", artifact_filename(file_path), flags=re.I)


def get_artifact_url(kind: str, run_id: int | str, filename: str) -> str:
    return url_for_frontend(f"/artifact/{kind}/{run_id}/{filename}")


def enrich_run(run: dict[str, Any], base_path: str | None = None) -> dict[str, Any]:
    data = dict(run)
    data["status_label"] = api_status_label(data.get("status"))
    data["dingtalk_status_label"] = dingtalk_status_label(data.get("dingtalk_status"))
    if data.get("report_path"):
        filename = artifact_filename(data["report_path"])
        data["report_filename"] = daily_report_filename(data["report_path"])
        local_path = resolve_artifact_path(data["report_path"])
        if local_path.exists():
            if base_path is None:
                data["report_url"] = get_artifact_url("reports", data["id"], filename)
            else:
                data["report_url"] = f"{base_path}/artifact/reports/{data['id']}/{filename}"
    return data


@api_router.get("/session", dependencies=[Depends(require_api_login)])
def api_session() -> dict[str, Any]:
    return {"authenticated": True, "app_name": display_name(), "idle_timeout_seconds": get_session_ttl_seconds()}


@api_router.get("/ui-config", dependencies=[Depends(require_api_login)])
def api_ui_config() -> dict[str, Any]:
    return frontend_config()


@api_router.post("/session/renew", dependencies=[Depends(require_api_login)])
def api_renew_session(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME, "")
    if len(token.split(":")) == 6:
        if not renew_idle_session(token):
            raise HTTPException(status_code=401, detail="登录已过期")
    else:
        # 旧版固定时长令牌在首次真实操作时迁移为闲置会话。
        token = create_idle_token(admin_username())
    is_secure = request.url.scheme == "https"
    for name, value, http_only in (
        (COOKIE_NAME, token, True),
        ("csrf_token", request.cookies.get("csrf_token", ""), False),
    ):
        response.set_cookie(
            name, value, httponly=http_only, samesite="lax", secure=is_secure,
            path=cookie_path(), max_age=get_session_ttl_seconds(),
        )
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "idle_timeout_seconds": get_session_ttl_seconds()}


@api_router.get("/dashboard", dependencies=[Depends(require_api_login)])
def api_dashboard() -> dict[str, Any]:
    jobs, raw_runs, auth_profiles = dashboard_data()
    base_path = public_base_path()
    runs = [enrich_run(run, base_path) for run in raw_runs]
    return {
        "app_name": display_name(),
        "jobs": public_jobs(jobs),
        "runs": runs,
        "metrics": dashboard_metrics(jobs, runs),
        "auth_profiles": [public_auth_profile(profile) for profile in auth_profiles],
    }


@api_router.get("/calendar-status", dependencies=[Depends(require_api_login)])
def api_calendar_status() -> dict[str, Any]:
    from app.holiday_calendar import status
    return status()


@api_router.get("/jobs", dependencies=[Depends(require_api_login)])
def api_list_jobs() -> dict[str, Any]:
    jobs = list_jobs()
    return {"jobs": public_jobs(jobs), "metrics": dashboard_metrics(jobs, list_runs(20))}


@api_router.get("/report-templates", dependencies=[Depends(require_api_login)])
def api_list_report_templates() -> dict[str, Any]:
    return {"templates": [
        {**template, "sections": list_template_sections(template_path(template))}
        for template in list_templates()
    ]}


@api_router.post("/report-templates", dependencies=[Depends(require_api_login)])
async def api_upload_report_template(name: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read(MAX_TEMPLATE_BYTES + 1)
    try:
        template = create_template(name, file.filename or "", content)
    except db_sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail="模板名称已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "template": {**template, "sections": inspect_template(content)}}


@api_router.get("/report-templates/{template_id}/download", dependencies=[Depends(require_api_login)])
def api_download_report_template(template_id: int) -> FileResponse:
    template = get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    path = template_path(template)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="模板文件不存在")
    return FileResponse(path, filename=template["original_name"], media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@api_router.delete("/report-templates/{template_id}", dependencies=[Depends(require_api_login)])
def api_delete_report_template(template_id: int) -> dict[str, Any]:
    try:
        delete_template(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@api_router.get("/taskbar-templates", dependencies=[Depends(require_api_login)])
def api_list_taskbar_templates() -> dict[str, Any]:
    return {"templates": [
        {**template, "image_url": f"{public_api_prefix()}/taskbar-templates/{template['key']}/image"}
        for template in all_taskbar_templates()
    ]}


@api_router.post("/taskbar-templates", dependencies=[Depends(require_api_login)])
async def api_upload_taskbar_template(name: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read(MAX_TASKBAR_BYTES + 1)
    try:
        template = create_taskbar_template(name, file.filename or "", content)
    except db_sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail="底栏名称已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "template": template}


@api_router.get("/taskbar-templates/{key}/image", dependencies=[Depends(require_api_login)])
def api_taskbar_template_image(key: str) -> FileResponse:
    try:
        path = selected_taskbar_path(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@api_router.delete("/taskbar-templates/{template_id}", dependencies=[Depends(require_api_login)])
def api_delete_taskbar_template(template_id: int) -> dict[str, Any]:
    try:
        delete_taskbar_template(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@api_router.post("/jobs", dependencies=[Depends(require_api_login)])
def api_create_job(payload: dict[str, Any]) -> dict[str, Any]:
    data = api_job_payload(payload)
    try:
        job_id = save_job(data)
    except db_sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e) and "report_jobs.name" in str(e):
            raise HTTPException(status_code=400, detail="任务名称已存在")
        raise HTTPException(status_code=400, detail=f"数据库约束校验失败: {e}")
    reload_jobs()
    return {"ok": True, "job": public_job(require_value(get_job(job_id), "任务不存在"))}


def calculate_job_next_runs(job: dict[str, Any], limit: int = 3) -> list[str]:
    """精确计算任务未来指定次数的真实执行时间（格式: YYYY-MM-DD HH:MM:SS）"""
    job_id = int(job.get("id") or 0)
    schedule_mode = job.get("schedule_mode") or "calendar"
    tz_str = settings.default_timezone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_str)
    now_dt = datetime.now(tz)

    # 1. 日历计划模式 (包含每天、工作日、周、月、随机分钟等)
    if schedule_mode == "calendar":
        try:
            from app.date_schedule import from_job, upcoming
            config = from_job(job)
            if config:
                times = upcoming(config, now_dt, limit=limit, job_id=job_id)
                return [t.strftime("%Y-%m-%d %H:%M") for t in times]
        except Exception as e:
            logger.warning("计算任务 %s 日历未来执行时间失败: %s", job_id, e)

    # 2. Cron 表达式模式
    cron_expr = job.get("cron_expression")
    if cron_expr:
        try:
            from apscheduler.triggers.cron import CronTrigger
            trigger = CronTrigger.from_crontab(str(cron_expr).strip(), timezone=tz)
            times = []
            curr = now_dt
            for _ in range(limit):
                nxt = trigger.get_next_fire_time(curr if curr != now_dt else None, curr)
                if not nxt:
                    break
                times.append(nxt)
                curr = nxt
            return [t.strftime("%Y-%m-%d %H:%M") for t in times]
        except Exception as e:
            logger.warning("计算任务 %s Cron 未来执行时间失败: %s", job_id, e)

    return []


@api_router.post("/watermark/preview", dependencies=[Depends(require_api_login)])
def api_watermark_preview(payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    text = text_value(payload, "watermark_text", WATERMARK_DEFAULTS["watermark_text"])
    if len(text) > MAX_WATERMARK_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail="预览文字不能超过 200 字")
    try:
        time_format = validate_time_format(text_value(payload, "watermark_time_format", DEFAULT_TIME_FORMAT))
        alignment = validate_watermark_alignment(text_value(payload, "watermark_alignment", WATERMARK_DEFAULTS["watermark_alignment"]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item = {
        "watermark_enabled": bool_value(payload.get("watermark_enabled"), True),
        "watermark_time_enabled": bool_value(payload.get("watermark_time_enabled"), True),
        "watermark_time_format": time_format,
        "watermark_text": text,
        "watermark_opacity": clamp_int(payload.get("watermark_opacity"), WATERMARK_DEFAULTS["watermark_opacity"], WATERMARK_RULES["opacity"]["min"], WATERMARK_RULES["opacity"]["max"]),
        "watermark_font_size": clamp_int(payload.get("watermark_font_size"), WATERMARK_DEFAULTS["watermark_font_size"], 10, 72),
        "watermark_gap_x": clamp_int(payload.get("watermark_gap_x"), WATERMARK_DEFAULTS["watermark_gap_x"], 20, 800),
        "watermark_gap_y": clamp_int(payload.get("watermark_gap_y"), WATERMARK_DEFAULTS["watermark_gap_y"], 20, 800),
        "watermark_angle": clamp_int(payload.get("watermark_angle"), WATERMARK_DEFAULTS["watermark_angle"], WATERMARK_RULES["angle"]["min"], WATERMARK_RULES["angle"]["max"]),
        "watermark_alignment": alignment,
    }
    image_width = clamp_int(payload.get("image_width"), JOB_FORM_DEFAULTS["browser_width"], WATERMARK_RULES["image_width"]["min"], WATERMARK_RULES["image_width"]["max"])
    image_height = clamp_int(payload.get("image_height"), JOB_FORM_DEFAULTS["browser_height"], WATERMARK_RULES["image_height"]["min"], WATERMARK_RULES["image_height"]["max"])
    png = render_watermark_preview_png(item, image_width, image_height)
    return {"image": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}


@api_router.get("/jobs/{job_id}", dependencies=[Depends(require_api_login)])
def api_get_job(job_id: int) -> dict[str, Any]:
    job = require_value(get_job(job_id), "任务不存在")
    items = list_screenshot_items(job_id)
    next_runs = calculate_job_next_runs(job, limit=3)
    latest = next((run for run in list_runs(50) if run.get("job_id") == job_id), None)
    return {
        "job": public_job(job),
        "items": items,
        "next_runs": next_runs,
        "last_status": api_status_label(latest.get("status") if latest else ""),
        "auth_profiles": [public_auth_profile(profile) for profile in list_auth_profiles()],
        "template_sections": job_template_sections(job),
        "item_defaults": WATERMARK_DEFAULTS,
        "watermark_time_formats": watermark_options(),
    }


@api_router.put("/jobs/{job_id}", dependencies=[Depends(require_api_login)])
def api_update_job(job_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    old = require_value(get_job(job_id), "任务不存在")
    data = api_job_payload(payload)
    if not data.get("dingtalk_webhook"):
        data["dingtalk_webhook"] = old.get("dingtalk_webhook") or ""
    if not data.get("dingtalk_secret"):
        data["dingtalk_secret"] = old.get("dingtalk_secret") or ""
    try:
        save_job(data, job_id)
    except db_sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e) and "report_jobs.name" in str(e):
            raise HTTPException(status_code=400, detail="任务名称已存在")
        raise HTTPException(status_code=400, detail=f"数据库约束校验失败: {e}")
    reload_jobs()
    return {"ok": True, "job": public_job(require_value(get_job(job_id), "任务不存在"))}


@api_router.delete("/jobs/{job_id}", dependencies=[Depends(require_api_login)])
def api_delete_job(job_id: int) -> dict[str, Any]:
    delete_job_when_idle(job_id)
    return {"ok": True}


def delete_job_when_idle(job_id: int) -> None:
    try:
        lease = acquire_job_lease(job_id)
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        require_value(get_job(job_id), "任务不存在")
        delete_job(job_id)
        reload_jobs()
    finally:
        lease.close()


@api_router.post("/jobs/{job_id}/run", dependencies=[Depends(require_api_login)])
def api_run_job(job_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    job = require_value(get_job(job_id), "任务不存在")
    run_id = queue_inspection_job(job, background_tasks)
    return {"ok": True, "run_id": run_id}


def queue_inspection_job(job: dict[str, Any], background_tasks: BackgroundTasks) -> int:
    try:
        lease = acquire_job_lease(int(job["id"]))
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run_id: int | None = None
    try:
        run_id = create_run(int(job["id"]), str(job["name"]), status="queued")
        background_tasks.add_task(run_job, int(job["id"]), run_id, execution_lease=lease)
        return run_id
    except Exception as exc:
        try:
            if run_id is not None:
                try:
                    finish_active_run(run_id, "failed", f"任务提交失败：{type(exc).__name__}: {exc}")
                except Exception:
                    app_logger.exception("任务提交失败后写入巡检记录 #%s 失败", run_id)
        finally:
            lease.close()
        raise


@api_router.post("/jobs/{job_id}/items", dependencies=[Depends(require_api_login)])
def api_create_item(job_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    job = require_value(get_job(job_id), "任务不存在")
    data = api_item_payload(payload, job_id)
    assign_item_report_section(data, job)
    validate_item_connection(data)
    item_id = save_screenshot_item(data)
    return {"ok": True, "item": get_screenshot_item(item_id)}


@api_router.post("/jobs/{job_id}/items/batch", dependencies=[Depends(require_api_login)])
def api_create_items_batch(job_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import urlsplit

    job = require_value(get_job(job_id), "任务不存在")
    template_sections = job_template_sections(job) if job.get("template_id") else []
    rows = payload.get("items")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise HTTPException(status_code=400, detail="请添加 1 到 100 个页面")
    existing = {
        (str(row.get("url") or "").strip(), row.get("auth_profile_id"))
        for row in list_screenshot_items(job_id)
    }
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail=f"第 {index} 行格式错误")
        url = str(row.get("url") or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
            raise HTTPException(status_code=400, detail=f"第 {index} 行页面地址无效")
        auth_id = parse_optional_int(str(row.get("auth_profile_id") or ""))
        if auth_id and not get_auth_profile(auth_id):
            raise HTTPException(status_code=400, detail=f"第 {index} 行认证配置不存在")
        key = (url, auth_id)
        if key in existing:
            raise HTTPException(status_code=400, detail=f"第 {index} 行页面已在任务中或重复添加")
        existing.add(key)
        name = str(row.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail=f"第 {index} 行请填写截图名称")
        kibana_dashboard = (
            parts.path.rstrip("/").lower().endswith(("/app/dashboards", "/app/kibana"))
            and parts.fragment.lower().startswith(("/view/", "/dashboard/"))
        )
        data = api_item_payload({
            "name": name, "url": url, "auth_profile_id": auth_id,
            "item_type": "grafana" if "/d/" in parts.path or "/d-solo/" in parts.path else "kibana" if kibana_dashboard else "web",
            "section": str(row.get("section") or "巡检截图").strip() or "巡检截图",
            "real_browser_capture": bool_value(row.get("real_browser_capture"), True),
            "taskbar_enabled": True,
            "timeout_seconds": 120,
            "total_timeout_seconds": 240,
            "retry_count": 2,
            "wait_seconds": 3,
            "sort_order": 100 + index,
        }, job_id)
        assign_item_report_section(data, job, template_sections)
        validated.append(data)
    ids = save_screenshot_items_batch(validated)
    return {"ok": True, "count": len(ids), "item_ids": ids}


def validate_item_connection(data: dict[str, Any]) -> None:
    from urllib.parse import urlsplit

    url = str(data.get("url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
        raise HTTPException(status_code=400, detail="请填写有效的页面地址")
    auth_id = data.get("auth_profile_id")
    if auth_id and not get_auth_profile(int(auth_id)):
        raise HTTPException(status_code=400, detail="所选网站连接不存在")


@api_router.put("/items/{item_id}", dependencies=[Depends(require_api_login)])
def api_update_item(item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    current = require_value(get_screenshot_item(item_id), "截图项不存在")
    job = require_value(get_job(int(current["job_id"])), "任务不存在")
    data = api_item_payload(payload, int(current["job_id"]))
    assign_item_report_section(data, job)
    validate_item_connection(data)
    save_screenshot_item(data, item_id)
    return {"ok": True, "item": get_screenshot_item(item_id)}


@api_router.delete("/items/{item_id}", dependencies=[Depends(require_api_login)])
def api_delete_item(item_id: int) -> dict[str, Any]:
    require_value(get_screenshot_item(item_id), "截图项不存在")
    delete_screenshot_item(item_id)
    return {"ok": True}


@api_router.post("/items/{item_id}/test", dependencies=[Depends(require_api_login)])
def api_test_item(item_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    run_id = queue_test_item(item_id, background_tasks)
    return {"ok": True, "run_id": run_id}


def queue_test_item(item_id: int, background_tasks: BackgroundTasks) -> int:
    item = require_value(get_screenshot_item(item_id), "截图项不存在")
    run_id = create_queued_test_run(int(item["job_id"]), item_id, str(item["name"]))
    if run_id is None:
        raise HTTPException(status_code=409, detail="该截图项已有测试在排队或运行")
    try:
        background_tasks.add_task(test_capture_item, item_id, run_id)
    except Exception as exc:
        finish_active_run(run_id, "failed", f"测试提交失败：{type(exc).__name__}: {exc}")
        raise
    return run_id


@api_router.get("/runs", dependencies=[Depends(require_api_login)])
def api_list_runs(page: int = 1, page_size: int = RUN_DEFAULT_PAGE_SIZE) -> dict[str, Any]:
    if page_size not in RUN_PAGE_SIZES:
        page_size = RUN_DEFAULT_PAGE_SIZE
    if page < 1:
        page = 1
    runs_list, total = list_runs_page(page, page_size)
    base_path = public_base_path()
    return {
        "runs": [enrich_run(run, base_path) for run in runs_list],
        "total": total,
        "page": page,
        "page_size": page_size
    }


def _daily_archive_selection(
    start_date: str | None = None, end_date: str | None = None, months: str | None = None,
) -> tuple[list[tuple[str, Path, int]], str]:
    from app.batch_reports import (
        archive_name_for_months, archive_name_for_range,
        daily_reports, monthly_reports, parse_months, parse_range,
    )

    try:
        if months is not None:
            if start_date is not None or end_date is not None:
                raise ValueError("请选择月份或自定义日期，不要同时填写")
            selected_months = parse_months(months)
            reports = monthly_reports(selected_months)
            filename = archive_name_for_months(selected_months)
        else:
            if not start_date or not end_date:
                raise ValueError("请选择开始日期和结束日期")
            start, end = parse_range(start_date, end_date)
            reports = daily_reports(start, end)
            filename = archive_name_for_range(start, end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not reports:
        raise HTTPException(status_code=404, detail="所选月份内没有可下载的 Word 报告" if months is not None else "所选日期内没有可下载的 Word 报告")
    return reports, filename


@api_router.get("/runs/reports/archive-info", dependencies=[Depends(require_api_login)])
def api_daily_archive_info(
    start_date: str | None = None, end_date: str | None = None, months: str | None = None,
) -> dict[str, Any]:
    reports, filename = _daily_archive_selection(start_date, end_date, months)
    return {"count": len(reports), "filename": filename}


@api_router.get("/runs/reports/archive", dependencies=[Depends(require_api_login)])
def api_download_daily_reports(
    start_date: str | None = None, end_date: str | None = None, months: str | None = None,
) -> StreamingResponse:
    from urllib.parse import quote
    from app.batch_reports import build_archive

    reports, filename = _daily_archive_selection(start_date, end_date, months)
    archive = build_archive(reports)
    archive_size = archive.seek(0, 2)
    archive.seek(0)

    def chunks():
        try:
            while chunk := archive.read(1024 * 1024):
                yield chunk
        finally:
            archive.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=inspection-reports.zip; filename*=UTF-8''{quote(filename, safe='')}",
            "Content-Length": str(archive_size),
        },
    )


@api_router.get("/runs/{run_id}", dependencies=[Depends(require_api_login)])
def api_get_run(run_id: int) -> dict[str, Any]:
    run = enrich_run(require_value(get_run(run_id), "运行记录不存在"))
    results = [with_file_url(dict(item)) for item in list_screenshot_results(run_id)]
    return {"run": run, "results": results}


@api_router.post("/runs/{run_id}/regenerate-report", dependencies=[Depends(require_api_login)])
def api_regenerate_daily_report(run_id: int) -> dict[str, Any]:
    from app.job_execution import JobAlreadyRunning
    from app.report import ReportValidationError
    from app.runner import regenerate_daily_report

    try:
        report_path = regenerate_daily_report(run_id)
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail="任务正在执行，请稍后重试") from exc
    except (ValueError, ReportValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "filename": daily_report_filename(report_path)}


@api_router.get("/alert-ignores", dependencies=[Depends(require_api_login)])
def api_list_alert_ignores() -> dict[str, Any]:
    from app.alert_ignores import list_ignore_rules
    return {"rules": list_ignore_rules()}


@api_router.post("/alert-ignores", dependencies=[Depends(require_api_login)])
def api_create_alert_ignore(payload: dict[str, Any]) -> dict[str, Any]:
    import json
    from datetime import datetime, timezone
    from app.alert_ignores import create_ignore_rule, instance_identity, instance_label, source_identity

    try:
        run_id = int(payload.get("run_id"))
        result_id = int(payload.get("result_id"))
        alert_index = int(payload.get("alert_index"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="请选择有效的告警记录") from None
    result = next((row for row in list_screenshot_results(run_id) if row["id"] == result_id), None)
    if result is None:
        raise HTTPException(status_code=404, detail="告警记录不存在")
    try:
        snapshot = json.loads(result.get("monitor_data") or "{}")
        alert = snapshot["alerts"][alert_index]
    except (TypeError, ValueError, KeyError, IndexError):
        raise HTTPException(status_code=404, detail="告警记录不存在") from None
    if not isinstance(alert, dict) or alert_index < 0:
        raise HTTPException(status_code=400, detail="告警记录无效")
    scope = str(payload.get("scope") or "")
    reason = str(payload.get("reason") or "").strip()
    if scope not in {"rule", "instance"}:
        raise HTTPException(status_code=400, detail="忽略范围无效")
    if not reason or len(reason) > 200:
        raise HTTPException(status_code=400, detail="忽略原因需为 1–200 个字符")
    item = get_screenshot_item(result.get("item_id")) if result.get("item_id") else None
    source_key, source_uid, source_name = source_identity(
        alert, snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else [],
        str(item.get("url") or "") if item else "",
    )
    alert_name = str(alert.get("name") or "").strip()
    if not source_key or not source_uid or not alert_name:
        raise HTTPException(status_code=400, detail="该历史告警缺少数据源信息，无法建立安全的忽略规则")
    instance = instance_identity(alert) if scope == "instance" else ""
    if scope == "instance" and not instance:
        raise HTTPException(status_code=400, detail="该告警没有可识别的实例，请改用整类告警范围")
    expires_at = None
    if payload.get("expires_at"):
        try:
            expiry = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                raise ValueError
            expires_at = expiry.astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="到期时间必须是未来的有效时间") from None
    rule = create_ignore_rule(
        source_key=source_key, source_uid=source_uid, source_name=source_name,
        alert_name=alert_name, scope=scope, instance=instance,
        instance_label=instance_label(alert) if scope == "instance" else "",
        reason=reason, expires_at=expires_at,
    )
    return {"ok": True, "rule": rule}


@api_router.delete("/alert-ignores/{rule_id}", dependencies=[Depends(require_api_login)])
def api_delete_alert_ignore(rule_id: int) -> dict[str, Any]:
    from app.alert_ignores import disable_ignore_rule
    if not disable_ignore_rule(rule_id):
        raise HTTPException(status_code=404, detail="忽略规则不存在")
    return {"ok": True}


@api_router.get("/runs/{run_id}/prometheus/{result_id}/{source_uid}", dependencies=[Depends(require_api_login)])
def api_open_prometheus_alerts(run_id: int, result_id: int, source_uid: str) -> RedirectResponse:
    """Open the native Prometheus UI, including for records made before URLs were saved."""
    import json
    from urllib.parse import quote, urlsplit
    from app.monitoring import _dashboard_api_base, prometheus_alert_url

    result = next(
        (row for row in list_screenshot_results(run_id) if row["id"] == result_id), None
    )
    if result is None:
        raise HTTPException(status_code=404, detail="巡检结果不存在")
    try:
        monitoring = json.loads(result.get("monitor_data") or "{}")
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="该记录没有 Prometheus 数据") from None
    if not isinstance(monitoring, dict) or not isinstance(monitoring.get("sources"), list):
        raise HTTPException(status_code=404, detail="该记录没有 Prometheus 数据")
    source = next(
        (entry for entry in monitoring.get("sources", [])
         if isinstance(entry, dict) and str(entry.get("uid")) == source_uid), None
    )
    if source is None:
        raise HTTPException(status_code=404, detail="该记录没有对应的 Prometheus 数据源")

    saved_link = str(source.get("alert_url") or "")
    if saved_link:
        parts = urlsplit(saved_link)
        if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password:
            return RedirectResponse(saved_link, status_code=302)

    item = get_screenshot_item(result.get("item_id"))
    if item is None:
        raise HTTPException(status_code=404, detail="原截图项已删除，无法查找 Prometheus 地址")

    from app.grafana_connections import ConnectionError, _with_login

    location = _dashboard_api_base(str(item.get("url") or ""))
    if location is None:
        raise HTTPException(status_code=404, detail="原截图项不是 Grafana 看板")
    base, _ = location
    profile = get_auth_profile(item.get("auth_profile_id")) if item.get("auth_profile_id") else None

    def read_source(context: Any, _page: Any) -> str:
        response = context.request.get(
            f"{base}/api/datasources/uid/{quote(source_uid, safe='')}", timeout=10_000
        )
        if not response.ok:
            raise ConnectionError(f"读取 Prometheus 地址失败：Grafana 返回 HTTP {response.status}")
        source_data = response.json()
        if not isinstance(source_data, dict) or str(source_data.get("uid")) != source_uid:
            raise ConnectionError("Grafana 返回的数据源不匹配")
        link = prometheus_alert_url(source_data.get("url"))
        if not link:
            raise ConnectionError("Grafana 数据源没有可供浏览器打开的 Prometheus 地址")
        return link

    try:
        link = _with_login(profile, read_source)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"无法打开 Prometheus：{exc}") from exc
    return RedirectResponse(link, status_code=302)


@api_router.get("/auth-profiles", dependencies=[Depends(require_api_login)])
def api_list_auth_profiles() -> dict[str, Any]:
    return {"profiles": [public_auth_profile(profile) for profile in list_auth_profiles()]}


@api_router.get("/auth-profiles/{auth_id}/dashboards", dependencies=[Depends(require_api_login)])
def api_search_grafana_dashboards(auth_id: int, query: str = "") -> dict[str, Any]:
    from app.grafana_connections import ConnectionError, search_dashboards

    profile = require_value(get_auth_profile(auth_id), "认证配置不存在")
    try:
        return search_dashboards(profile, query)
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.get("/auth-profiles/{auth_id}/kibana-dashboards", dependencies=[Depends(require_api_login)])
def api_search_kibana_dashboards(auth_id: int, query: str = "") -> dict[str, Any]:
    from app.kibana_connections import search_dashboards
    from app.grafana_connections import ConnectionError

    profile = require_value(get_auth_profile(auth_id), "认证配置不存在")
    try:
        return search_dashboards(profile, query)
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.get("/auth-profiles/{auth_id}/dashboards/{uid}/settings", dependencies=[Depends(require_api_login)])
def api_grafana_dashboard_settings(auth_id: int, uid: str, params: str = "") -> dict[str, Any]:
    from app.grafana_connections import ConnectionError, dashboard_settings

    profile = require_value(get_auth_profile(auth_id), "认证配置不存在")
    try:
        return dashboard_settings(profile, uid, params)
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/connections/check-target", dependencies=[Depends(require_api_login)])
def api_check_connection_target(payload: dict[str, Any]) -> dict[str, Any]:
    from app.grafana_connections import check_target

    auth_id = parse_optional_int(str(payload.get("auth_profile_id") or ""))
    profile = require_value(get_auth_profile(auth_id), "认证配置不存在") if auth_id else None
    return check_target(str(payload.get("url") or ""), profile)


@api_router.post("/auth-profiles", dependencies=[Depends(require_api_login)])
def api_create_auth_profile(payload: dict[str, Any]) -> dict[str, Any]:
    data = api_auth_payload(payload)
    auth_id = save_named_profile_or_http_error(save_auth_profile, data)
    return {"ok": True, "profile": public_auth_profile(require_value(get_auth_profile(auth_id), "认证配置不存在"))}


@api_router.put("/auth-profiles/{auth_id}", dependencies=[Depends(require_api_login)])
def api_update_auth_profile(auth_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    old = require_value(get_auth_profile(auth_id), "认证配置不存在")
    data = api_auth_payload(payload)
    changed = any(
        str(data.get(field) or "") != str(old.get(field) or "")
        for field in (
            "auth_type", "login_url", "username_value", "username",
            "username_selector", "password_selector", "submit_selector",
            "success_selector", "username_source", "password_source",
            "username_env", "password_env", "storage_state_path"
        )
    )
    if data.get("password"):
        changed = True
    else:
        data["password_secret"] = old.get("password_secret")
    if not data.get("username_value"):
        data["username"] = old.get("username")
        data["username_value"] = old.get("username_value")
    save_named_profile_or_http_error(save_auth_profile, data, auth_id)
    if changed:
        clear_auth_state_cache(old)
    return {"ok": True, "profile": public_auth_profile(require_value(get_auth_profile(auth_id), "认证配置不存在"))}


@api_router.delete("/auth-profiles/{auth_id}", dependencies=[Depends(require_api_login)])
def api_delete_auth_profile(auth_id: int) -> dict[str, Any]:
    old = require_value(get_auth_profile(auth_id), "认证配置不存在")
    clear_auth_state_cache(old)
    delete_auth_profile(auth_id)
    return {"ok": True}


@api_router.post("/auth-profiles/{auth_id}/test", dependencies=[Depends(require_api_login)])
def api_test_auth_profile(auth_id: int) -> dict[str, Any]:
    from app.screenshot import test_auth_profile_login

    require_value(get_auth_profile(auth_id), "认证配置不存在")
    result = test_auth_profile_login(auth_id)
    return result


@api_router.get("/dingtalk-profiles", dependencies=[Depends(require_api_login)])
def api_list_dingtalk_profiles() -> dict[str, Any]:
    return {"profiles": [public_dingtalk_profile(profile) for profile in list_dingtalk_profiles()]}


@api_router.post("/dingtalk-profiles", dependencies=[Depends(require_api_login)])
def api_create_dingtalk_profile(payload: dict[str, Any]) -> dict[str, Any]:
    data = api_dingtalk_payload(payload)
    if not data["webhook"]:
        raise HTTPException(status_code=400, detail="Webhook 不能为空")
    bot_id = save_named_profile_or_http_error(save_dingtalk_profile, data)
    return {"ok": True, "profile": public_dingtalk_profile(require_value(get_dingtalk_profile(bot_id), "钉钉机器人不存在"))}


@api_router.put("/dingtalk-profiles/{profile_id}", dependencies=[Depends(require_api_login)])
def api_update_dingtalk_profile(profile_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    old = require_value(get_dingtalk_profile(profile_id), "钉钉机器人不存在")
    data = api_dingtalk_payload(payload)
    if not data["webhook"]:
        data["webhook"] = old.get("webhook") or ""
    if not data["secret"]:
        data["secret"] = old.get("secret") or ""
    save_named_profile_or_http_error(save_dingtalk_profile, data, profile_id)
    return {"ok": True, "profile": public_dingtalk_profile(require_value(get_dingtalk_profile(profile_id), "钉钉机器人不存在"))}


@api_router.delete("/dingtalk-profiles/{profile_id}", dependencies=[Depends(require_api_login)])
def api_delete_dingtalk_profile(profile_id: int) -> dict[str, Any]:
    require_value(get_dingtalk_profile(profile_id), "钉钉机器人不存在")
    delete_dingtalk_profile(profile_id)
    return {"ok": True}


@api_router.post("/dingtalk-profiles/{profile_id}/test", dependencies=[Depends(require_api_login)])
def api_test_dingtalk_profile(profile_id: int) -> dict[str, Any]:
    profile = require_value(get_dingtalk_profile(profile_id), "钉钉机器人不存在")
    from app.utils import decrypt_secret
    from app.dingtalk import send_dingtalk_msg
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.settings import settings
    local_tz = ZoneInfo(settings.default_timezone)
    now_str = datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")

    webhook = decrypt_secret(profile.get("webhook", "")) if profile.get("webhook") else ""
    secret = decrypt_secret(profile.get("secret", "")) if profile.get("secret") else ""
    keyword = profile.get("keyword", "") or ""

    title = "巡检系统钉钉机器人测试"
    emoji = "🔔 " if profile.get("emoji_enabled") == 1 else ""
    text = f"### {emoji}钉钉通知连通性测试\n\n- **机器人**: {profile.get('name')}\n- **测试时间**: {now_str}\n- **状态**: 消息通道连接正常\n\n> 自动化巡检系统测试消息"
    result = send_dingtalk_msg(webhook, secret, keyword, title, text)
    if result == "sent":
        return {"ok": True, "message": "测试消息已成功发送至钉钉群！"}
    return {"ok": False, "error": f"钉钉发送失败: {result}"}


@api_router.get("/periodic-reports", dependencies=[Depends(require_api_login)])
def api_periodic_reports() -> dict[str, Any]:
    from app.repository import list_periodic_report_runs, list_periodic_report_settings, list_dingtalk_profiles
    from app.scheduler import _periodic_next
    from zoneinfo import ZoneInfo
    from datetime import datetime
    import json

    now_tz = datetime.now(ZoneInfo(settings.default_timezone))
    settings_list = []
    for item in list_periodic_report_settings():
        s = dict(item)
        next_run_str = ""
        if s.get("enabled"):
            try:
                cfg = json.loads(s.get("schedule_config") or "{}")
                times = cfg.get("times") or []
                candidates = []
                for slot in times:
                    nxt = _periodic_next(s, slot, now_tz)
                    if nxt:
                        candidates.append(nxt)
                if candidates:
                    candidates.sort()
                    next_run_str = candidates[0].strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        s["next_run"] = next_run_str
        settings_list.append(s)

    runs = []
    for item in list_periodic_report_runs(50):
        run = dict(item)
        zip_path = run.get("zip_path")
        if zip_path and os.path.exists(zip_path):
            size_bytes = os.path.getsize(zip_path)
            run["size_str"] = f"{size_bytes / 1024:.2f} KB" if size_bytes < 1024 * 1024 else f"{size_bytes / (1024 * 1024):.2f} MB"
            run["filename"] = Path(zip_path).name
            run["download_url"] = url_for_frontend(f"/periodic-reports/download/{run['id']}")
        else:
            run["size_str"] = "-"
            run["filename"] = ""
        runs.append(run)

    return {
        "settings": settings_list,
        "runs": runs,
        "dingtalk_profiles": [public_dingtalk_profile(p) for p in list_dingtalk_profiles()],
    }


@api_router.post("/periodic-reports/{report_type}/run", dependencies=[Depends(require_api_login)])
def api_run_periodic_report(report_type: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    from app.periodic_reporter import trigger_periodic_report
    from app.repository import get_periodic_report_setting

    if report_type not in ("weekly", "monthly") or not get_periodic_report_setting(report_type):
        raise HTTPException(status_code=404, detail="周期报告配置不存在")
    background_tasks.add_task(trigger_periodic_report, report_type, True)
    return {"ok": True}


@api_router.delete("/periodic-reports/runs/{run_id}", dependencies=[Depends(require_api_login)])
def api_delete_periodic_report_run(run_id: int) -> dict[str, Any]:
    _remove_periodic_report_run(run_id)
    return {"ok": True}


def _remove_periodic_report_run(run_id: int) -> None:
    from app.repository import get_periodic_report_run, delete_periodic_report_run
    from app.storage_paths import is_safe_path, resolve_artifact_path
    run = get_periodic_report_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="周期报告记录不存在")
    if run.get("status") in {"running", "waiting"}:
        raise HTTPException(status_code=409, detail="报告仍在生成，完成后才能删除")
    z_path = run.get("zip_path")
    if z_path:
        archive = resolve_artifact_path(z_path)
        if not is_safe_path(archive):
            raise HTTPException(status_code=403, detail="报告文件路径无效，记录已保留")
        try:
            archive.unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="报告文件删除失败，记录已保留") from exc
    delete_periodic_report_run(run_id)



@api_router.put("/periodic-reports/{report_type}", dependencies=[Depends(require_api_login)])
def api_update_periodic_report(report_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    import json

    from app.repository import get_periodic_report_setting, save_periodic_report_setting
    from app.scheduler import reload_jobs

    if report_type not in ("weekly", "monthly"):
        raise HTTPException(status_code=404, detail="周期报告配置不存在")
    current = get_periodic_report_setting(report_type)
    if not current:
        raise HTTPException(status_code=404, detail="周期报告配置不存在")
    if not bool_value(payload.get("include_docx"), True) and not bool_value(payload.get("include_screenshots"), False):
        raise HTTPException(status_code=400, detail="请至少选择 Word 报告或截图")

    schedule_mode = text_value(payload, "schedule_mode", "calendar")
    if schedule_mode not in ("simple", "calendar"):
        raise HTTPException(status_code=400, detail="旧定时方式已停用，请选择日期计划")
    schedule_mode = "calendar"
    cron_expr = ""
    raw_times = payload.get("times")
    if not isinstance(raw_times, list) or not 1 <= len(raw_times) <= 4:
        raise HTTPException(status_code=400, detail="请设置 1 到 4 个生成时间")
    times = []
    for entry in raw_times:
        try:
            hour = int(entry["hour"])
            minute = int(entry["minute"])
            ampm = str(entry["ampm"])
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail="生成时间格式错误") from exc
        if not 1 <= hour <= 12 or not 0 <= minute <= 59 or ampm not in ("am", "pm"):
            raise HTTPException(status_code=400, detail="生成时间超出范围")
        times.append({"ampm": ampm, "hour": hour, "minute": minute})
    if len({(entry["ampm"], entry["hour"], entry["minute"]) for entry in times}) != len(times):
        raise HTTPException(status_code=400, detail="生成时间不能重复")

    if report_type == "monthly":
        labels = []
        for entry in times:
            labels.append(f"{'上午' if entry['ampm'] == 'am' else '下午'}{entry['hour']}点{entry['minute']:02d}分")
        schedule_label = f"每月最后一天 {', '.join(labels)}"
        schedule_config = json.dumps({"frequency": "monthly_last", "times": times}, ensure_ascii=False)
    else:
        day_of_week = text_value(payload, "day_of_week", "1")
        if day_of_week not in {str(day) for day in range(1, 8)}:
            raise HTTPException(status_code=400, detail="执行星期无效")
        schedule_label = f"每周{'一二三四五六日'[int(day_of_week) - 1]} " + "、".join(f"{'上午' if entry['ampm'] == 'am' else '下午'}{entry['hour']}点{entry['minute']:02d}分" for entry in times)
        schedule_config = json.dumps({"frequency": "weekly", "day_of_week": day_of_week, "times": times}, ensure_ascii=False)

    data = dict(current)
    data.update({
        "enabled": int(bool_value(payload.get("enabled"), bool(current.get("enabled")))),
        "name": text_value(payload, "name", str(current.get("name") or "")),
        "schedule_mode": schedule_mode,
        "schedule_label": schedule_label,
        "cron_expression": cron_expr,
        "schedule_config": schedule_config,
        "mail_profile_id": None,
        "enable_email": 0,
        "recipients_override": "",
        "dingtalk_enabled": int(bool_value(payload.get("dingtalk_enabled"), False)),
        "dingtalk_profile_id": parse_optional_int(str(payload.get("dingtalk_profile_id") or "")),
        "include_docx": int(bool_value(payload.get("include_docx"), True)),
        "include_screenshots": int(bool_value(payload.get("include_screenshots"), False)),
        "send_empty_report": int(bool_value(payload.get("send_empty_report"), False)),
        "wait_for_daily_jobs": int(bool_value(payload.get("wait_for_daily_jobs"), False)),
        "retry_until_time": text_value(payload, "retry_until_time", "23:50"),
        "retry_interval_minutes": int_value(payload, "retry_interval_minutes", 10),
        "send_on_timeout": int(bool_value(payload.get("send_on_timeout"), True)),
    })
    save_periodic_report_setting(report_type, data)
    reload_jobs()
    return {"ok": True}


@frontend_router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def index(request: Request) -> HTMLResponse:
    import json

    base_path = public_base_path()
    manifest_path = Path("app/static/vue/.vite/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["index.html"]
    return templates.TemplateResponse(
        "vue.html",
        {
            "request": request,
            "url_for_frontend": lambda path: f"{base_path}/{path.lstrip('/')}",
            "api_prefix": base_path + "/api",
            "frontend_base_path": base_path or "/",
            "app_name": display_name(),
            "time_zone": settings.default_timezone,
            "vue_js": entry["file"],
            "vue_css": entry["css"][0],
        },
    )


@frontend_router.get("/legacy", dependencies=[Depends(require_login)])
def legacy_index() -> RedirectResponse:
    return frontend_redirect("/")


@frontend_router.get("/jobs/new", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def new_job(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "job_form.html",
        {
            "request": request,
            "job": {},
            "action": url_for_frontend("/jobs/new"),
            "error": request.query_params.get("error", "")
        },
    )


@frontend_router.post("/jobs/new", dependencies=[Depends(require_login)])
def create_job(form: dict[str, Any] = Depends(job_form)) -> RedirectResponse:
    from app.schedule_migration import _job_config
    from app.date_schedule import label
    from urllib.parse import quote
    try:
        if form.get("schedule_mode") != "simple":
            raise ValueError("旧定时方式已停用，请在新页面设置日期计划")
        config = _job_config(form["schedule_config"])
        import json
        form.update(schedule_mode="calendar", schedule_config=json.dumps(config, ensure_ascii=False), schedule_label=label(config), cron_expression="")
    except ValueError as e:
        return frontend_redirect(f"/jobs/new?error={quote(str(e))}")

    try:
        job_id = save_job(form)
    except db_sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e) and "report_jobs.name" in str(e):
            return frontend_redirect(f"/jobs/new?error={quote('任务名称已存在')}")
        return frontend_redirect(f"/jobs/new?error={quote(f'数据库错误: {e}')}")
    reload_jobs()
    return frontend_redirect(f"/jobs/{job_id}")


@frontend_router.get("/jobs/{job_id}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def job_detail(request: Request, job_id: int) -> HTMLResponse:
    from app.scheduler import scheduler
    db_job = get_job(job_id)
    job = dict(db_job) if db_job else None
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")

    next_runs = calculate_job_next_runs(job, limit=3)

    items = list_screenshot_items(job_id)

    runs_list = list_runs(50)
    last_status = "暂无运行"
    for run in runs_list:
        if run.get("job_id") == job_id:
            from app.main import status_label
            last_status = status_label(run.get("status"))
            break

    return templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "items": items,
            "auth_profiles": list_auth_profiles(),
            "last_status": last_status,
            "item_count": len(items),
            "next_runs": next_runs,
        },
    )


@frontend_router.get("/jobs/{job_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_job(request: Request, job_id: int) -> HTMLResponse:
    job = require_value(get_job(job_id), "任务不存在")
    return templates.TemplateResponse(
        "job_form.html",
        {
            "request": request,
            "job": job,
            "action": url_for_frontend(f"/jobs/{job_id}/edit"),
            "error": request.query_params.get("error", "")
        },
    )


@frontend_router.post("/jobs/{job_id}/edit", dependencies=[Depends(require_login)])
def update_job(job_id: int, form: dict[str, Any] = Depends(job_form)) -> RedirectResponse:
    from app.schedule_migration import _job_config
    from app.date_schedule import label
    from urllib.parse import quote
    old = require_value(get_job(job_id), "任务不存在")
    try:
        if form.get("schedule_mode") != "simple":
            raise ValueError("旧定时方式已停用，请在新页面设置日期计划")
        config = _job_config(form["schedule_config"])
        import json
        form.update(schedule_mode="calendar", schedule_config=json.dumps(config, ensure_ascii=False), schedule_label=label(config), cron_expression="")
    except ValueError as e:
        return frontend_redirect(f"/jobs/{job_id}/edit?error={quote(str(e))}")

    if not form.get("dingtalk_webhook"):
        form["dingtalk_webhook"] = old.get("dingtalk_webhook") or ""
    if not form.get("dingtalk_secret"):
        form["dingtalk_secret"] = old.get("dingtalk_secret") or ""
    form["template_id"] = old.get("template_id")
    try:
        save_job(form, job_id)
    except db_sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e) and "report_jobs.name" in str(e):
            return frontend_redirect(f"/jobs/{job_id}/edit?error={quote('任务名称已存在')}")
        return frontend_redirect(f"/jobs/{job_id}/edit?error={quote(f'数据库错误: {e}')}")
    reload_jobs()
    return frontend_redirect(f"/jobs/{job_id}")


@frontend_router.post("/jobs/{job_id}/delete", dependencies=[Depends(require_login)])
def remove_job(job_id: int) -> RedirectResponse:
    delete_job_when_idle(job_id)
    return frontend_redirect("/")


@frontend_router.post("/jobs/{job_id}/run", dependencies=[Depends(require_login)])
def run_job_now(job_id: int, background_tasks: BackgroundTasks) -> RedirectResponse:
    job = require_value(get_job(job_id), "任务不存在")
    run_id = queue_inspection_job(job, background_tasks)
    return frontend_redirect(f"/runs/{run_id}?watch=1")


@frontend_router.get("/jobs/{job_id}/items/new", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def new_item(request: Request, job_id: int) -> HTMLResponse:
    job = require_value(get_job(job_id), "任务不存在")
    return templates.TemplateResponse(
        "item_form.html",
        {
            "request": request,
            "item": {
                "job_id": job_id,
                "enabled": 1,
                "capture_mode": "viewport",
                "item_type": "web",
                "section": "巡检截图",
                **WATERMARK_DEFAULTS,
            },
            "job_id": job_id,
            "auth_profiles": list_auth_profiles(),
            "template_sections": job_template_sections(job),
            "template_selected": bool(job.get("template_id")),
            "api_prefix": public_api_prefix(),
            "preview_browser_width": job.get("browser_width") or 3840,
            "preview_browser_height": job.get("browser_height") or 2160,
            "action": url_for_frontend(f"/jobs/{job_id}/items/new"),
        },
    )


@frontend_router.post("/jobs/{job_id}/items/new", dependencies=[Depends(require_login)])
def create_item(job_id: int, form: dict[str, Any] = Depends(item_form)) -> RedirectResponse:
    job = require_value(get_job(job_id), "任务不存在")
    form["job_id"] = job_id
    assign_item_report_section(form, job)
    item_id = save_screenshot_item(form)
    return frontend_redirect(f"/jobs/{job_id}")


@frontend_router.get("/items/{item_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_item(request: Request, item_id: int) -> HTMLResponse:
    item = require_value(get_screenshot_item(item_id), "截图项不存在")
    job = require_value(get_job(item["job_id"]), "任务不存在")
    return templates.TemplateResponse(
        "item_form.html",
        {
            "request": request,
            "item": item,
            "api_prefix": public_api_prefix(),
            "preview_browser_width": job.get("browser_width") or 3840,
            "preview_browser_height": job.get("browser_height") or 2160,
            "job_id": item["job_id"],
            "auth_profiles": list_auth_profiles(),
            "template_sections": job_template_sections(job),
            "template_selected": bool(job.get("template_id")),
            "action": url_for_frontend(f"/items/{item_id}/edit"),
        },
    )


@frontend_router.post("/items/{item_id}/edit", dependencies=[Depends(require_login)])
def update_item(item_id: int, form: dict[str, Any] = Depends(item_form)) -> RedirectResponse:
    current = require_value(get_screenshot_item(item_id), "截图项不存在")
    job = require_value(get_job(int(current["job_id"])), "任务不存在")
    form["job_id"] = current["job_id"]
    form["taskbar_template_key"] = current.get("taskbar_template_key") or ""
    assign_item_report_section(form, job)
    save_screenshot_item(form, item_id)
    return frontend_redirect(f"/jobs/{current['job_id']}")


@frontend_router.post("/items/{item_id}/delete", dependencies=[Depends(require_login)])
def remove_item(item_id: int) -> RedirectResponse:
    item = require_value(get_screenshot_item(item_id), "截图项不存在")
    delete_screenshot_item(item_id)
    return frontend_redirect(f"/jobs/{item['job_id']}")


@frontend_router.post("/items/{item_id}/test", dependencies=[Depends(require_login)])
def test_item(item_id: int, background_tasks: BackgroundTasks) -> RedirectResponse:
    run_id = queue_test_item(item_id, background_tasks)
    return frontend_redirect(f"/runs/{run_id}?watch=1")


@frontend_router.get("/auth-profiles", dependencies=[Depends(require_login)])
def auth_profiles(request: Request) -> RedirectResponse:
    params = request.query_params
    query_str = f"?{params}" if params else ""
    return frontend_redirect(f"/#/auth{query_str}")


@frontend_router.get("/auth-profiles/new", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def new_auth_profile(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "auth_form.html",
        {"request": request, "profile": {}, "action": url_for_frontend("/auth-profiles/new")},
    )


@frontend_router.post("/auth-profiles/new", dependencies=[Depends(require_login)])
def create_auth_profile(form: dict[str, Any] = Depends(auth_form)) -> RedirectResponse:
    from urllib.parse import quote
    try:
        save_named_profile_or_http_error(save_auth_profile, form)
    except HTTPException as exc:
        return frontend_redirect(f"/auth-profiles/new?error={quote(str(exc.detail))}")
    return frontend_redirect("/auth-profiles")


@frontend_router.get("/auth-profiles/{auth_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_auth_profile(request: Request, auth_id: int) -> HTMLResponse:
    profile = require_value(get_auth_profile(auth_id), "认证配置不存在")
    return templates.TemplateResponse(
        "auth_form.html",
        {"request": request, "profile": profile, "action": url_for_frontend(f"/auth-profiles/{auth_id}/edit")},
    )


@frontend_router.post("/auth-profiles/{auth_id}/edit", dependencies=[Depends(require_login)])
def update_auth_profile(auth_id: int, form: dict[str, Any] = Depends(auth_form)) -> RedirectResponse:
    from urllib.parse import quote
    old = get_auth_profile(auth_id)
    changed = False
    if old:
        # 若留空代表不修改，沿用旧凭据
        if not form.get("password"):
            form["password_secret"] = old.get("password_secret")

        if not form.get("username_value"):
            form["username_value"] = old.get("username_value")
            form["username"] = old.get("username")

        # 沿用未提交的高级选择器；如果页面提交了新值，则保存新配置。
        for field in ["username_selector", "password_selector", "submit_selector", "success_selector"]:
            if not form.get(field):
                form[field] = old.get(field) or form.get(field)

        changed = any(
            str(form.get(field) or "") != str(old.get(field) or "")
            for field in (
                "auth_type", "login_url", "username_value", "username",
                "username_selector", "password_selector", "submit_selector",
                "success_selector", "username_source", "password_source",
                "username_env", "password_env", "storage_state_path"
            )
        )
        if form.get("password"):
            changed = True

    try:
        save_named_profile_or_http_error(save_auth_profile, form, auth_id)
    except HTTPException as exc:
        return frontend_redirect(f"/auth-profiles/{auth_id}/edit?error={quote(str(exc.detail))}")
    if old and changed:
        clear_auth_state_cache(dict(old))
    return frontend_redirect("/auth-profiles")


@frontend_router.post("/auth-profiles/{auth_id}/delete", dependencies=[Depends(require_login)])
def remove_auth_profile(auth_id: int) -> RedirectResponse:
    old = get_auth_profile(auth_id)
    if old:
        clear_auth_state_cache(dict(old))
    delete_auth_profile(auth_id)
    return frontend_redirect("/auth-profiles")


@frontend_router.post("/auth-profiles/{auth_id}/test", dependencies=[Depends(require_login)])
def test_auth_profile_route(auth_id: int) -> RedirectResponse:
    from app.screenshot import test_auth_profile_login
    from urllib.parse import quote
    res = test_auth_profile_login(auth_id)
    if res["success"]:
        return frontend_redirect(f"/auth-profiles?success={quote(res['message'])}")
    return frontend_redirect(f"/auth-profiles?error={quote(res['message'])}")


@frontend_router.post("/auth-profiles/{auth_id}/test-async", dependencies=[Depends(require_login)])
def test_auth_profile_route_async(auth_id: int) -> dict[str, Any]:
    from app.screenshot import test_auth_profile_login
    return test_auth_profile_login(auth_id)


@frontend_router.get("/runs", dependencies=[Depends(require_login)])
def runs(request: Request) -> RedirectResponse:
    params = request.query_params
    query_str = f"?{params}" if params else ""
    return frontend_redirect(f"/#/runs{query_str}")


@frontend_router.get("/help", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def help_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "help.html",
        {"request": request, "title": "配置说明与指南"},
    )



@frontend_router.get("/runs/{run_id}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def run_detail(request: Request, run_id: int) -> HTMLResponse:
    run = require_value(get_run(run_id), "运行记录不存在")
    results = [with_file_url(item) for item in list_screenshot_results(run_id)]
    if run.get("report_path"):
        filename = artifact_filename(run["report_path"])
        local_path = resolve_artifact_path(run["report_path"])
        if local_path.exists():
            run["report_url"] = get_artifact_url("reports", run_id, filename)
    return templates.TemplateResponse(
        "run_detail.html",
        {
            "request": request,
            "run": run,
            "results": results,
            "watch": request.query_params.get("watch", ""),
        },
    )


@frontend_router.get("/artifact/{kind}/{run_id}/{filename}", dependencies=[Depends(require_login)])
def artifact(request: Request, kind: str, run_id: int, filename: str):
    if kind not in {"screenshots", "reports"}:
        raise HTTPException(status_code=404, detail="未知文件类型")

    # 日期目录由多次运行共享，只允许下载该运行记录实际关联的文件。
    if kind == "reports":
        run = get_run(run_id)
        stored_paths = [run.get("report_path")] if run else []
    else:
        stored_paths = [item.get("file_path") for item in list_screenshot_results(run_id)]
    target_path = next(
        (
            path for stored in stored_paths if stored
            for path in [resolve_artifact_path(stored)]
            if path.name == filename and path.is_file() and is_under(path.resolve(), settings.data_dir.resolve())
        ),
        None,
    )

    if target_path is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    accept = request.headers.get("accept", "")
    raw = request.query_params.get("raw", "")
    download_requested = request.query_params.get("download") == "1"

    is_image = filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'))
    if "text/html" in accept and not raw and not download_requested and is_image:
        import html
        escaped_filename = html.escape(filename)
        escaped_raw_url = html.escape(request.url.path + "?raw=true")
        favicon_url = html.escape(url_for_frontend("favicon.ico"))
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>查看图片 - {escaped_filename}</title>
    <link rel="shortcut icon" href="{favicon_url}" type="image/x-icon">
    <style>
        body {{
            margin: 0;
            background: #090c15;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            color: #f1f5f9;
            font-family: system-ui, -apple-system, sans-serif;
            overflow: hidden;
        }}
        .container {{
            max-width: 90vw;
            max-height: 80vh;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: #020617;
        }}
        img {{
            max-width: 100%;
            max-height: 80vh;
            display: block;
            margin: 0 auto;
            transition: transform 0.2s ease;
        }}
        .toolbar {{
            margin-top: 20px;
            display: flex;
            gap: 12px;
        }}
        .btn {{
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.12);
            color: #f8fafc;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            transition: all 0.2s;
        }}
        .btn:hover {{
            background: rgba(255, 255, 255, 0.15);
            border-color: rgba(255, 255, 255, 0.3);
        }}
    </style>
</head>
<body>
    <div class="container">
        <img id="preview" src="{escaped_raw_url}" alt="截图预览">
    </div>
    <div class="toolbar">
        <button class="btn" onclick="rotateImage()">↻ 旋转</button>
        <a class="btn" href="{html.escape(request.url.path + '?download=1')}">⬇ 下载原图</a>
        <button class="btn" onclick="window.close()">✕ 关闭</button>
    </div>
    <script>
        let angle = 0;
        function rotateImage() {{
            angle = (angle + 90) % 360;
            document.getElementById('preview').style.transform = `rotate(${{angle}}deg)`;
        }}
    </script>
</body>
</html>"""
        return HTMLResponse(content=html_content)

    download_name = daily_report_filename(filename) if kind == "reports" else filename
    return FileResponse(target_path, filename=download_name if download_requested or not is_image else None)


def require_value(value: Any, message: str) -> Any:
    if value is None:
        raise HTTPException(status_code=404, detail=message)
    return value


def with_file_url(item: dict[str, Any]) -> dict[str, Any]:
    created_at = item.get("created_at")
    if created_at:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        try:
            timestamp = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            item["created_at_local"] = timestamp.astimezone(ZoneInfo(settings.default_timezone)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    file_path = item.get("file_path") or ""
    run_id = item.get("run_id")
    if file_path and run_id:
        filename = artifact_filename(file_path)
        local_path = resolve_artifact_path(file_path)
        if local_path.exists():
            item["file_url"] = get_artifact_url("screenshots", run_id, filename)
            try:
                size_bytes = local_path.stat().st_size
                item["file_size_bytes"] = size_bytes
                if size_bytes < 1024:
                    item["file_size_human"] = f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    item["file_size_human"] = f"{size_bytes / 1024:.1f} KB"
                else:
                    item["file_size_human"] = f"{size_bytes / (1024 * 1024):.2f} MB"
            except Exception:
                pass
            try:
                from PIL import Image
                with Image.open(local_path) as img:
                    item["dimensions"] = f"{img.width}×{img.height}"
                    item["image_width"] = img.width
                    item["image_height"] = img.height
            except Exception:
                pass
    return item


def is_under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail="参数格式错误") from e


# ==============================================================================
# 周期报告与数据库重置路由
# ==============================================================================

@frontend_router.post("/settings/reset", dependencies=[Depends(require_login)])
def reset_database() -> None:
    raise HTTPException(status_code=410, detail="旧版全量重置接口已停用，请在系统设置中使用需要 MFA 验证的历史清理功能")


@frontend_router.get("/periodic-reports", dependencies=[Depends(require_login)])
def periodic_reports_list(request: Request) -> RedirectResponse:
    params = request.query_params
    query_str = f"?{params}" if params else ""
    return frontend_redirect(f"/#/periodic{query_str}")


@frontend_router.get("/periodic-reports/{report_type}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_periodic_report(request: Request, report_type: str) -> HTMLResponse:
    from app.repository import get_periodic_report_setting
    from app.utils import decrypt_secret
    setting = get_periodic_report_setting(report_type)
    if not setting:
        raise HTTPException(status_code=404, detail="周期报告配置不存在")

    # 解密敏感字段以在表单中回显
    for f in ("dingtalk_webhook", "dingtalk_secret"):
        if setting.get(f):
            try:
                setting[f] = decrypt_secret(setting[f])
            except Exception:
                pass

    return templates.TemplateResponse(
        "periodic_form.html",
        {
            "request": request,
            "setting": setting,
            "action": url_for_frontend(f"/periodic-reports/{report_type}/edit")
        }
    )


@frontend_router.post("/periodic-reports/{report_type}/edit", dependencies=[Depends(require_login)])
def update_periodic_report(
    report_type: str,
    enabled: str | None = Form(None),
    name: str = Form(...),
    schedule_mode: str = Form("simple"),
    cron_expression: str = Form(""),
    include_docx: str | None = Form(None),
    include_screenshots: str | None = Form(None),
    send_empty_report: str | None = Form(None),
    wait_for_daily_jobs: str | None = Form(None),
    retry_until_time: str = Form("23:50"),
    retry_interval_minutes: int = Form(10),
    frequency: str = Form("weekly"),
    day_of_week: str | None = Form(None),
    day_of_month: str | None = Form(None),
    ampm: list[str] = Form([]),
    hour: list[int] = Form([]),
    minute: list[int] = Form([]),
    send_on_timeout: int = Form(1),
    dingtalk_enabled: str | None = Form(None),
    dingtalk_webhook: str = Form(""),
    dingtalk_secret: str = Form(""),
    dingtalk_keyword: str = Form(""),
) -> RedirectResponse:
    from urllib.parse import quote
    if schedule_mode != "simple":
        return frontend_redirect(f"/#/periodic/{report_type}/edit?error={quote('旧定时方式已停用，请重新设置生成时间')}")
    times = [
        {"ampm": ampm[i] if i < len(ampm) else "am", "hour": value,
         "minute": minute[i] if i < len(minute) else 0}
        for i, value in enumerate(hour)
    ]
    try:
        api_update_periodic_report(report_type, {
            "name": name, "enabled": bool(enabled), "schedule_mode": "calendar",
            "day_of_week": day_of_week or "1", "times": times,
            "include_docx": bool(include_docx),
            "include_screenshots": bool(include_screenshots),
            "send_empty_report": bool(send_empty_report),
            "wait_for_daily_jobs": bool(wait_for_daily_jobs),
            "retry_until_time": retry_until_time,
            "retry_interval_minutes": retry_interval_minutes,
            "send_on_timeout": bool(send_on_timeout),
            "dingtalk_enabled": bool(dingtalk_enabled),
        })
    except HTTPException as exc:
        return frontend_redirect(f"/#/periodic/{report_type}/edit?error={quote(str(exc.detail))}")
    return frontend_redirect("/#/periodic")

@frontend_router.post("/periodic-reports/{report_type}/run", dependencies=[Depends(require_login)])
def run_periodic_report_now(report_type: str, background_tasks: BackgroundTasks) -> RedirectResponse:
    from app.periodic_reporter import trigger_periodic_report
    from urllib.parse import quote
    background_tasks.add_task(trigger_periodic_report, report_type, True)
    return frontend_redirect(f"/periodic-reports?success={quote('周期汇总报告生成任务已在后台启动，请稍候刷新页面查看。')}")


@frontend_router.get("/periodic-reports/download/{run_id}", dependencies=[Depends(require_login)])
def download_periodic_report_zip(run_id: int) -> FileResponse:
    from app.repository import get_periodic_report_run
    from app.storage_paths import is_safe_path, resolve_artifact_path
    run = get_periodic_report_run(run_id)
    if not run or not run.get("zip_path"):
        raise HTTPException(status_code=404, detail="周期报告包不存在")
    path = resolve_artifact_path(run["zip_path"]).resolve()
    if not is_safe_path(path):
        raise HTTPException(status_code=403, detail="非法路径，拒绝访问")
    if not path.exists():
        raise HTTPException(status_code=400, detail="周期报告附件已按数据保留策略被自动清理")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@frontend_router.post("/periodic-reports/delete/{run_id}", dependencies=[Depends(require_login)])
def delete_periodic_report_run_route(run_id: int) -> RedirectResponse:
    from urllib.parse import quote
    try:
        _remove_periodic_report_run(run_id)
    except HTTPException as exc:
        return frontend_redirect(f"/periodic-reports?error={quote(str(exc.detail))}")
    return frontend_redirect("/periodic-reports")


# ==============================================================================
# 存储管理与数据保留策略 API 路由
# ==============================================================================

@api_router.get("/storage", dependencies=[Depends(require_api_login)])
def api_get_storage() -> dict[str, Any]:
    from app.cleanup import get_storage_usage
    from app.storage_alert import alert_settings, disk_status
    from app.db import connect
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    try:
        usage = get_storage_usage()
    except Exception as e:
        app_logger.error(f"获取存储占用统计失败: {e}", exc_info=True)
        usage = {
            "total_bytes": 0, "screenshots_bytes": 0, "reports_bytes": 0,
            "logs_bytes": 0, "browser_state_bytes": 0, "periodic_reports_bytes": 0,
            "sqlite_db_bytes": 0, "estimated_cleanup_bytes": None, "estimated_cleanup_files": None
        }

    with connect() as conn:
        row = conn.execute("SELECT * FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
        config = dict(row) if row else {}
    for key in ("enabled", "allow_manual_cleanup", "browser_state_cleanup_enabled"):
        if key in config:
            config[key] = bool_value(config[key])

    with connect() as conn:
        row_run = conn.execute("SELECT * FROM storage_cleanup_runs ORDER BY id DESC LIMIT 1").fetchone()
        latest_run = dict(row_run) if row_run else None

    current_month_start = datetime.now(ZoneInfo(settings.default_timezone)).replace(day=1)
    return {
        "usage": usage,
        "disk": disk_status(),
        "disk_alert": alert_settings(),
        "config": config,
        "latest_run": latest_run,
        "latest_completed_month": (current_month_start - timedelta(days=1)).strftime("%Y-%m"),
    }


@api_router.put("/storage/disk-alert", dependencies=[Depends(require_api_login)])
def api_update_disk_alert(payload: dict[str, Any]) -> dict[str, Any]:
    from app.storage_alert import ENABLED_KEY, PROFILE_KEY, SENT_KEY, THRESHOLD_KEY, alert_settings

    enabled = bool_value(payload.get("enabled"), False)
    try:
        threshold = int(payload.get("threshold", 80))
        profile_id = int(payload.get("profile_id") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="告警阈值或钉钉机器人无效") from exc
    if not 1 <= threshold <= 99:
        raise HTTPException(status_code=400, detail="告警阈值需为 1–99%")
    if enabled and (not profile_id or not get_dingtalk_profile(profile_id)):
        raise HTTPException(status_code=400, detail="请先选择已配置的钉钉机器人")
    previous = alert_settings()
    set_system_setting(ENABLED_KEY, "1" if enabled else "0")
    set_system_setting(THRESHOLD_KEY, str(threshold))
    set_system_setting(PROFILE_KEY, str(profile_id))
    if previous != {"enabled": enabled, "threshold": threshold, "profile_id": profile_id}:
        set_system_setting(SENT_KEY, "0")
    return {"ok": True, "disk_alert": alert_settings()}


@api_router.put("/storage/settings", dependencies=[Depends(require_api_login)])
def api_update_storage_settings(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db import connect
    from app.scheduler import reload_jobs
    import json

    with connect() as conn:
        existing_row = conn.execute("SELECT * FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
        existing = dict(existing_row) if existing_row else {}

    enabled = bool_value(payload.get("enabled"), bool(existing.get("enabled", True)))
    # 手动清理由管理员确认操作，不再由独立开关控制；保留旧列兼容现有数据库。
    allow_manual_cleanup = True
    cleanup_schedule_mode = text_value(payload, "cleanup_schedule_mode", existing.get("cleanup_schedule_mode") or "daily")
    if cleanup_schedule_mode not in {option["value"] for option in CLEANUP_SCHEDULE_MODES}:
        raise HTTPException(status_code=400, detail="清理频率无效")

    try:
        cfg = json.loads(text_value(payload, "cleanup_schedule_config", existing.get("cleanup_schedule_config") or "{}") or "{}")
        if not isinstance(cfg, dict):
            raise ValueError("清理计划必须是对象")
        parts = str(cfg.get("time") or "02:30").split(":")
        if len(parts) != 2:
            raise ValueError("清理时间格式无效")
        hour, minute = (int(part) for part in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("清理时间超出范围")
        time_str = f"{hour:02d}:{minute:02d}"
        normalized_cfg: dict[str, Any] = {"time": time_str}
        if cleanup_schedule_mode == "weekly":
            weekday = int(cfg.get("day_of_week") or 1)
            if weekday == 0:
                weekday = 7
            if not 1 <= weekday <= 7:
                raise ValueError("每周执行日期无效")
            normalized_cfg["day_of_week"] = str(weekday)
            cleanup_schedule_label = f"每周 {['周一', '周二', '周三', '周四', '周五', '周六', '周日'][weekday - 1]} {time_str}"
        elif cleanup_schedule_mode == "monthly":
            day = int(cfg.get("day_of_month") or 1)
            if not 1 <= day <= 31:
                raise ValueError("每月执行日期无效")
            normalized_cfg["day_of_month"] = str(day)
            cleanup_schedule_label = f"每月 {day}号 {time_str}"
        else:
            cleanup_schedule_label = f"每天 {time_str}"
        cleanup_schedule_config = json.dumps(normalized_cfg, ensure_ascii=False)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"自动清理计划无效：{exc}") from exc

    periodic_sent_retention_days = int_value(payload, "periodic_sent_retention_days", int(existing.get("periodic_sent_retention_days") or 3))
    periodic_failed_retention_days = int_value(payload, "periodic_failed_retention_days", int(existing.get("periodic_failed_retention_days") or 30))
    screenshot_retention_days = int_value(payload, "screenshot_retention_days", int(existing.get("screenshot_retention_days") or 7))
    report_retention_days = int_value(payload, "report_retention_days", int(existing.get("report_retention_days") or 30))
    run_record_retention_days = int_value(payload, "run_record_retention_days", int(existing.get("run_record_retention_days") or 90))
    log_retention_days = int_value(payload, "log_retention_days", int(existing.get("log_retention_days") or 14))
    browser_state_retention_days = int_value(payload, "browser_state_retention_days", int(existing.get("browser_state_retention_days") or 30))
    browser_state_cleanup_enabled = bool_value(payload.get("browser_state_cleanup_enabled"), bool(existing.get("browser_state_cleanup_enabled", False)))
    protect_recent_days = int_value(payload, "protect_recent_days", int(existing.get("protect_recent_days") or 3))
    retention_values = (
        periodic_sent_retention_days, periodic_failed_retention_days,
        screenshot_retention_days, report_retention_days, run_record_retention_days,
        log_retention_days, browser_state_retention_days, protect_recent_days,
    )
    if any(value < 1 for value in retention_values):
        raise HTTPException(status_code=400, detail="保留天数必须大于 0")

    with connect() as conn:
        row = conn.execute("SELECT id FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            setting_id = row[0]
            conn.execute(
                """
                UPDATE storage_cleanup_settings SET
                    enabled = ?, allow_manual_cleanup = ?, cleanup_schedule_mode = ?,
                    cleanup_schedule_label = ?, cleanup_schedule_config = ?,
                    periodic_sent_retention_days = ?, periodic_failed_retention_days = ?,
                    screenshot_retention_days = ?, report_retention_days = ?,
                    run_record_retention_days = ?, log_retention_days = ?,
                    browser_state_retention_days = ?, browser_state_cleanup_enabled = ?,
                    protect_recent_days = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    enabled, allow_manual_cleanup, cleanup_schedule_mode,
                    cleanup_schedule_label, cleanup_schedule_config,
                    periodic_sent_retention_days, periodic_failed_retention_days,
                    screenshot_retention_days, report_retention_days,
                    run_record_retention_days, log_retention_days,
                    browser_state_retention_days, browser_state_cleanup_enabled,
                    protect_recent_days, setting_id
                )
            )
        else:
            conn.execute(
                """
                INSERT INTO storage_cleanup_settings (
                    enabled, allow_manual_cleanup, cleanup_schedule_mode,
                    cleanup_schedule_label, cleanup_schedule_config,
                    periodic_sent_retention_days, periodic_failed_retention_days,
                    screenshot_retention_days, report_retention_days,
                    run_record_retention_days, log_retention_days,
                    browser_state_retention_days, browser_state_cleanup_enabled,
                    protect_recent_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    enabled, allow_manual_cleanup, cleanup_schedule_mode,
                    cleanup_schedule_label, cleanup_schedule_config,
                    periodic_sent_retention_days, periodic_failed_retention_days,
                    screenshot_retention_days, report_retention_days,
                    run_record_retention_days, log_retention_days,
                    browser_state_retention_days, browser_state_cleanup_enabled,
                    protect_recent_days
                )
            )

    reload_jobs()
    return {"ok": True}


@api_router.post("/storage/estimate", dependencies=[Depends(require_api_login)])
def api_estimate_storage(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.cleanup import estimate_cleanup
    try:
        res = estimate_cleanup(target_month=payload.get("month") if payload else None)
        return {"ok": True, "result": res}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        app_logger.error(f"执行预估清理失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"预估清理失败: {e}")


@api_router.post("/storage/cleanup", dependencies=[Depends(require_api_login)])
def api_run_storage_cleanup(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.cleanup import confirmed_cleanup, preview_cleanup, run_cleanup
    payload = payload or {}
    target_month = payload.get("month")
    try:
        fingerprint = payload.get("preview_fingerprint")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise HTTPException(status_code=400, detail="请先查看待清理数据")
        current = preview_cleanup(
            scopes=payload.get("scopes"), older_than_days=payload.get("older_than_days"),
            target_month=target_month, page=1, page_size=1, include_all_items=True,
        )
        if current.get("status") != "success" or current.get("fingerprint") != fingerprint or not current.get("total_items"):
            raise HTTPException(status_code=409, detail="待清理数据已变化，请重新查看")
        with confirmed_cleanup(current["_all_items"]):
            res = run_cleanup(
                mode="manual", dry_run=False, target_month=target_month,
                scopes=payload.get("scopes"), older_than_days=payload.get("older_than_days"),
            )
        if res.get("status") == "failed":
            detail = res.get("error_summary") or "清理失败"
            status_code = 400 if "系统策略限制" in detail else 500
            raise HTTPException(status_code=status_code, detail=detail)
        return {"ok": True, "result": res}
    except HTTPException as he:
        raise he
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        app_logger.error(f"执行物理清理失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"执行清理失败: {e}")


@api_router.post("/storage/preview", dependencies=[Depends(require_api_login)])
def api_preview_storage_cleanup(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.cleanup import preview_cleanup

    payload = payload or {}
    try:
        result = preview_cleanup(
            scopes=payload.get("scopes"), older_than_days=payload.get("older_than_days"),
            target_month=payload.get("month"), page=payload.get("page", 1),
            page_size=payload.get("page_size", 20),
        )
        if result.get("status") != "success":
            raise HTTPException(status_code=500, detail=result.get("error_summary") or "读取待清理数据失败")
        return {"ok": True, "result": result}
    except (HTTPException, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
    except Exception as exc:
        app_logger.error("读取待清理数据失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="读取待清理数据失败") from exc


@api_router.get("/storage/cleanup-runs", dependencies=[Depends(require_api_login)])
def api_get_cleanup_runs() -> dict[str, Any]:
    from app.db import connect
    with connect() as conn:
        rows = conn.execute("SELECT * FROM storage_cleanup_runs ORDER BY id DESC LIMIT 50").fetchall()
        runs = [dict(row) for row in rows]
    return {"runs": runs}


app.include_router(frontend_router)
app.include_router(api_router)
