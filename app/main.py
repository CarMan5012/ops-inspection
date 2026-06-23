from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import COOKIE_NAME, check_password, create_token, require_login
from app.db import audit, init_db
from app.repository import (
    create_run,
    delete_auth_profile,
    delete_job,
    delete_mail_profile,
    delete_screenshot_item,
    get_auth_profile,
    get_job,
    get_mail_profile,
    get_run,
    get_screenshot_item,
    list_auth_profiles,
    list_jobs,
    list_mail_profiles,
    list_runs,
    list_screenshot_items,
    list_screenshot_results,
    save_auth_profile,
    save_job,
    save_mail_profile,
    save_screenshot_item,
)
from app.runner import run_job, test_capture_item
from app.scheduler import reload_jobs, shutdown_scheduler, start_scheduler
from app.settings import settings
from app.report import list_template_sections
import os
import logging
import sys


app_logger = logging.getLogger("app")
app_logger.setLevel(logging.INFO)
if not app_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    app_logger.addHandler(handler)
    app_logger.propagate = False

app = FastAPI(title=settings.app_name)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def status_label(value: Any) -> str:
    labels = {
        "running": "运行中",
        "success": "成功",
        "partial_success": "部分成功",
        "failed": "失败",
    }
    return labels.get(str(value or ""), str(value or "-"))


def mail_status_label(value: Any) -> str:
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


templates.env.filters["status_label"] = status_label
templates.env.filters["mail_status_label"] = mail_status_label

from app.utils import env_status, mask_value, resolve_auth_credential, resolve_mail_credential, decrypt_secret

templates.env.globals["env_status"] = env_status
templates.env.globals["mask_value"] = mask_value
templates.env.globals["resolve_auth_credential"] = resolve_auth_credential
templates.env.globals["resolve_mail_credential"] = resolve_mail_credential
templates.env.globals["decrypt_secret"] = decrypt_secret



def job_form(
    name: str = Form(...),
    environment: str = Form("生产环境"),
    enabled: str | None = Form(None),
    cron_expression: str = Form("0 9 * * *"),
    time_range_label: str = Form("最近24小时"),
    report_title: str = Form("自动化巡检报告"),
    mail_profile_id: str | None = Form(None),
    send_mail: str | None = Form(None),
    browser_width: int = Form(1920),
    browser_height: int = Form(1080),
    headless: str | None = Form(None),
    schedule_mode: str = Form("cron"),
    frequency: str = Form("daily"),
    day_of_week: str | None = Form(None),
    day_of_month: str | None = Form(None),
    ampm: list[str] = Form([]),
    hour: list[int] = Form([]),
    minute: list[int] = Form([]),
) -> dict[str, Any]:
    import json
    from app.utils import times_to_cron

    times_list = []
    for i in range(len(hour)):
        times_list.append({
            "ampm": ampm[i] if i < len(ampm) else "am",
            "hour": int(hour[i]),
            "minute": int(minute[i]) if i < len(minute) else 0
        })

    if schedule_mode == "simple":
        cron_expr, label_expr = times_to_cron(frequency, day_of_week, day_of_month, times_list)
        schedule_config = json.dumps({
            "frequency": frequency,
            "day_of_week": day_of_week,
            "day_of_month": day_of_month,
            "times": times_list
        })
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
        "mail_profile_id": parse_optional_int(mail_profile_id),
        "send_mail": 1 if send_mail else 0,
        "browser_width": browser_width,
        "browser_height": browser_height,
        "headless": 1 if headless else 0,
        "schedule_mode": schedule_mode,
        "schedule_label": label_expr,
        "schedule_config": schedule_config,
    }


def item_form(
    auth_profile_id: str | None = Form(None),
    name: str = Form(...),
    item_type: str = Form("web"),
    url: str = Form(...),
    section: str = Form("巡检截图"),
    capture_mode: str = Form("full_page"),
    css_selector: str = Form(""),
    wait_selector: str = Form(""),
    wait_seconds: float = Form(3),
    timeout_seconds: int = Form(60),
    retry_count: int = Form(2),
    browser_width: str | None = Form(None),
    browser_height: str | None = Form(None),
    sort_order: int = Form(100),
    enabled: str | None = Form(None),
) -> dict[str, Any]:
    return {
        "job_id": None,
        "auth_profile_id": parse_optional_int(auth_profile_id),
        "name": name,
        "item_type": item_type,
        "url": url,
        "section": section,
        "capture_mode": capture_mode,
        "css_selector": css_selector,
        "wait_selector": wait_selector,
        "wait_seconds": wait_seconds,
        "timeout_seconds": timeout_seconds,
        "retry_count": retry_count,
        "browser_width": parse_optional_int(browser_width),
        "browser_height": parse_optional_int(browser_height),
        "sort_order": sort_order,
        "enabled": 1 if enabled else 0,
    }


def auth_form(
    name: str = Form(...),
    auth_type: str = Form("none"),
    login_url: str = Form(""),
    username_value: str = Form(""),
    password: str = Form(""),
    username_selector: str = Form('input[name="user"], input[name="username"], input[type="email"], input[placeholder*="username"], input[placeholder*="email"], input[placeholder*="user"]'),
    password_selector: str = Form('input[name="password"], input[type="password"], input[placeholder*="password"]'),
    submit_selector: str = Form('button[type="submit"]'),
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


def mail_form(
    name: str = Form(...),
    smtp_host: str = Form(""),
    smtp_port: int = Form(465),
    use_ssl: str | None = Form(None),
    use_starttls: str | None = Form(None),
    username: str = Form(""),
    password: str = Form(""),
    sender: str = Form(""),
    recipients: str = Form(""),
    cc: str = Form(""),
    subject_template: str = Form("自动化巡检报告 - {date}"),
    body_template: str = Form("巡检报告已生成，请查看附件。"),
) -> dict[str, Any]:
    return {
        "name": name,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "use_ssl": 1 if use_ssl else 0,
        "use_starttls": 1 if use_starttls else 0,
        "username": username,
        "password": password,
        "password_source": "plain",
        "password_env": "",
        "sender": sender,
        "recipients": recipients,
        "cc": cc,
        "subject_template": subject_template,
        "body_template": body_template,
    }


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    shutdown_scheduler()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": request.query_params.get("error", "")},
    )


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    if not check_password(username, password):
        return RedirectResponse("/login?error=1", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE_NAME, create_token(username), httponly=True, samesite="lax")
    return response


@app.get("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": list_jobs(),
            "runs": list_runs(8),
            "title": settings.app_name,
        },
    )


@app.get("/jobs/new", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def new_job(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "job_form.html",
        {
            "request": request,
            "job": {},
            "mail_profiles": list_mail_profiles(),
            "action": "/jobs/new",
            "error": request.query_params.get("error", "")
        },
    )


@app.post("/jobs/new", dependencies=[Depends(require_login)])
def create_job(form: dict[str, Any] = Depends(job_form)) -> RedirectResponse:
    from app.utils import validate_cron_expression
    from urllib.parse import quote
    try:
        validate_cron_expression(form["cron_expression"])
    except ValueError as e:
        return RedirectResponse(f"/jobs/new?error={quote(str(e))}", status_code=303)

    job_id = save_job(form)
    audit("create_job", str(job_id))
    reload_jobs()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def job_detail(request: Request, job_id: int) -> HTMLResponse:
    from app.scheduler import scheduler
    db_job = get_job(job_id)
    job = dict(db_job) if db_job else None
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
        
    next_runs = []
    if int(job.get("enabled") or 0):
        from app.utils import get_next_run_times
        next_runs = get_next_run_times(job.get("cron_expression") or "", limit=3)
    
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


@app.get("/jobs/{job_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_job(request: Request, job_id: int) -> HTMLResponse:
    job = require_value(get_job(job_id), "任务不存在")
    return templates.TemplateResponse(
        "job_form.html",
        {
            "request": request,
            "job": job,
            "mail_profiles": list_mail_profiles(),
            "action": f"/jobs/{job_id}/edit",
            "error": request.query_params.get("error", "")
        },
    )


@app.post("/jobs/{job_id}/edit", dependencies=[Depends(require_login)])
def update_job(job_id: int, form: dict[str, Any] = Depends(job_form)) -> RedirectResponse:
    from app.utils import validate_cron_expression
    from urllib.parse import quote
    try:
        validate_cron_expression(form["cron_expression"])
    except ValueError as e:
        return RedirectResponse(f"/jobs/{job_id}/edit?error={quote(str(e))}", status_code=303)

    save_job(form, job_id)
    audit("update_job", str(job_id))
    reload_jobs()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/delete", dependencies=[Depends(require_login)])
def remove_job(job_id: int) -> RedirectResponse:
    delete_job(job_id)
    audit("delete_job", str(job_id))
    reload_jobs()
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/run", dependencies=[Depends(require_login)])
def run_job_now(job_id: int, background_tasks: BackgroundTasks) -> RedirectResponse:
    job = require_value(get_job(job_id), "任务不存在")
    run_id = create_run(job_id, str(job["name"]))
    background_tasks.add_task(run_job, job_id, run_id)
    audit("run_job", str(job_id))
    return RedirectResponse(f"/runs/{run_id}?watch=1", status_code=303)


@app.get("/jobs/{job_id}/items/new", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def new_item(request: Request, job_id: int) -> HTMLResponse:
    require_value(get_job(job_id), "任务不存在")
    return templates.TemplateResponse(
        "item_form.html",
        {
            "request": request,
            "item": {"job_id": job_id, "enabled": 1, "capture_mode": "viewport", "item_type": "grafana"},
            "job_id": job_id,
            "auth_profiles": list_auth_profiles(),
            "template_sections": list_template_sections(),
            "action": f"/jobs/{job_id}/items/new",
        },
    )


@app.post("/jobs/{job_id}/items/new", dependencies=[Depends(require_login)])
def create_item(job_id: int, form: dict[str, Any] = Depends(item_form)) -> RedirectResponse:
    form["job_id"] = job_id
    item_id = save_screenshot_item(form)
    audit("create_item", str(item_id))
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/items/{item_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_item(request: Request, item_id: int) -> HTMLResponse:
    item = require_value(get_screenshot_item(item_id), "截图项不存在")
    return templates.TemplateResponse(
        "item_form.html",
        {
            "request": request,
            "item": item,
            "job_id": item["job_id"],
            "auth_profiles": list_auth_profiles(),
            "template_sections": list_template_sections(),
            "action": f"/items/{item_id}/edit",
        },
    )


@app.post("/items/{item_id}/edit", dependencies=[Depends(require_login)])
def update_item(item_id: int, form: dict[str, Any] = Depends(item_form)) -> RedirectResponse:
    current = require_value(get_screenshot_item(item_id), "截图项不存在")
    form["job_id"] = current["job_id"]
    save_screenshot_item(form, item_id)
    audit("update_item", str(item_id))
    return RedirectResponse(f"/jobs/{current['job_id']}", status_code=303)


@app.post("/items/{item_id}/delete", dependencies=[Depends(require_login)])
def remove_item(item_id: int) -> RedirectResponse:
    item = require_value(get_screenshot_item(item_id), "截图项不存在")
    delete_screenshot_item(item_id)
    audit("delete_item", str(item_id))
    return RedirectResponse(f"/jobs/{item['job_id']}", status_code=303)


@app.post("/items/{item_id}/test", dependencies=[Depends(require_login)])
def test_item(item_id: int, background_tasks: BackgroundTasks) -> RedirectResponse:
    require_value(get_screenshot_item(item_id), "截图项不存在")
    background_tasks.add_task(test_capture_item, item_id)
    audit("test_item", str(item_id))
    return RedirectResponse("/runs?watch=1", status_code=303)


@app.get("/auth-profiles", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def auth_profiles(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "auth_profiles.html",
        {
            "request": request, 
            "profiles": list_auth_profiles(),
            "success": request.query_params.get("success", ""),
            "error": request.query_params.get("error", "")
        },
    )


@app.get("/auth-profiles/new", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def new_auth_profile(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "auth_form.html",
        {"request": request, "profile": {}, "action": "/auth-profiles/new"},
    )


@app.post("/auth-profiles/new", dependencies=[Depends(require_login)])
def create_auth_profile(form: dict[str, Any] = Depends(auth_form)) -> RedirectResponse:
    from app.utils import encrypt_secret
    # 密码加密存储
    if form.get("password"):
        form["password_secret"] = encrypt_secret(form["password"])
    auth_id = save_auth_profile(form)
    audit("create_auth", str(auth_id))
    return RedirectResponse("/auth-profiles", status_code=303)


@app.get("/auth-profiles/{auth_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_auth_profile(request: Request, auth_id: int) -> HTMLResponse:
    profile = require_value(get_auth_profile(auth_id), "认证配置不存在")
    return templates.TemplateResponse(
        "auth_form.html",
        {"request": request, "profile": profile, "action": f"/auth-profiles/{auth_id}/edit"},
    )


@app.post("/auth-profiles/{auth_id}/edit", dependencies=[Depends(require_login)])
def update_auth_profile(auth_id: int, form: dict[str, Any] = Depends(auth_form)) -> RedirectResponse:
    from app.utils import encrypt_secret
    old = get_auth_profile(auth_id)
    if old:
        changed = False
        if form.get("password") and form.get("password") != old.get("password"):
            changed = True
        if form.get("username_value") and form.get("username_value") != old.get("username_value"):
            changed = True
        if form.get("login_url") and form.get("login_url") != old.get("login_url"):
            changed = True

        # 若留空代表不修改，沿用旧凭据
        if not form.get("password"):
            form["password_secret"] = old.get("password_secret")
            form["password"] = old.get("password")
        else:
            form["password_secret"] = encrypt_secret(form["password"])
            
        if not form.get("username_value"):
            form["username_value"] = old.get("username_value")
            form["username"] = old.get("username")
            
        # 沿用原有的选择器配置
        for field in ["username_selector", "password_selector", "submit_selector", "success_selector"]:
            form[field] = old.get(field) or form.get(field)

        if changed:
            from app.screenshot import _storage_state_path
            try:
                state_path = _storage_state_path(old)
                if state_path and state_path.exists():
                    os.remove(state_path)
                    app_logger.info(f"由于认证凭证发生变更，已物理删除旧的浏览器状态缓存: {state_path}")
            except Exception as e:
                app_logger.error(f"清理旧的浏览器登录状态文件失败: {e}", exc_info=True)
            
    save_auth_profile(form, auth_id)
    audit("update_auth", str(auth_id))
    return RedirectResponse("/auth-profiles", status_code=303)


@app.post("/auth-profiles/{auth_id}/delete", dependencies=[Depends(require_login)])
def remove_auth_profile(auth_id: int) -> RedirectResponse:
    delete_auth_profile(auth_id)
    audit("delete_auth", str(auth_id))
    return RedirectResponse("/auth-profiles", status_code=303)


@app.post("/auth-profiles/{auth_id}/test", dependencies=[Depends(require_login)])
def test_auth_profile_route(auth_id: int) -> RedirectResponse:
    from app.screenshot import test_auth_profile_login
    from urllib.parse import quote
    res = test_auth_profile_login(auth_id)
    if res["success"]:
        return RedirectResponse(f"/auth-profiles?success={quote(res['message'])}", status_code=303)
    return RedirectResponse(f"/auth-profiles?error={quote(res['message'])}", status_code=303)


@app.post("/auth-profiles/{auth_id}/test-async", dependencies=[Depends(require_login)])
def test_auth_profile_route_async(auth_id: int) -> dict[str, Any]:
    from app.screenshot import test_auth_profile_login
    return test_auth_profile_login(auth_id)


@app.post("/jobs/{job_id}/seed-local", dependencies=[Depends(require_login)])
def seed_local_test_item(job_id: int) -> RedirectResponse:
    require_value(get_job(job_id), "任务不存在")
    
    # 自动搜索本地 Grafana 配置关联
    grafana_auth = None
    grafana_base_url = "http://localhost:3000"
    for profile in list_auth_profiles():
        if "Grafana" in profile["name"]:
            grafana_auth = profile["id"]
            login_url = str(profile.get("login_url") or "").strip()
            if login_url:
                from urllib.parse import urlparse
                parsed = urlparse(login_url)
                if parsed.scheme and parsed.netloc:
                    grafana_base_url = f"{parsed.scheme}://{parsed.netloc}"
            break
            
    from app.repository import save_screenshot_item
    item_data = {
        "job_id": job_id,
        "auth_profile_id": grafana_auth,
        "name": "本地 Grafana 测试看板",
        "item_type": "grafana",
        "url": f"{grafana_base_url}/d/local-inspection-test/local-grafana-inspection-test?orgId=1&from=now-1h&to=now&kiosk",
        "section": "1服务器资源",
        "capture_mode": "viewport",
        "css_selector": "",
        "wait_selector": ".react-grid-layout",
        "wait_seconds": 5.0,
        "timeout_seconds": 90,
        "retry_count": 2,
        "browser_width": 1920,
        "browser_height": 1080,
        "sort_order": 10,
        "enabled": 1
    }
    save_screenshot_item(item_data)
    from urllib.parse import quote
    audit("seed_local_item", str(job_id))
    return RedirectResponse(f"/jobs/{job_id}?success={quote('🌱 成功自动填充本地测试截图项！')}", status_code=303)


@app.get("/mail-profiles", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def mail_profiles(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "mail_profiles.html",
        {"request": request, "profiles": list_mail_profiles()},
    )


@app.get("/mail-profiles/new", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def new_mail_profile(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "mail_form.html",
        {"request": request, "profile": {}, "action": "/mail-profiles/new"},
    )


@app.post("/mail-profiles/new", dependencies=[Depends(require_login)])
def create_mail_profile(form: dict[str, Any] = Depends(mail_form)) -> RedirectResponse:
    from app.utils import encrypt_secret
    if form.get("password"):
        form["password_secret"] = encrypt_secret(form["password"])
    mail_id = save_mail_profile(form)
    audit("create_mail", str(mail_id))
    return RedirectResponse("/mail-profiles", status_code=303)


@app.get("/mail-profiles/{mail_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_mail_profile(request: Request, mail_id: int) -> HTMLResponse:
    profile = require_value(get_mail_profile(mail_id), "邮件配置不存在")
    return templates.TemplateResponse(
        "mail_form.html",
        {"request": request, "profile": profile, "action": f"/mail-profiles/{mail_id}/edit"},
    )


@app.post("/mail-profiles/{mail_id}/edit", dependencies=[Depends(require_login)])
def update_mail_profile(mail_id: int, form: dict[str, Any] = Depends(mail_form)) -> RedirectResponse:
    from app.utils import encrypt_secret
    old = get_mail_profile(mail_id)
    if old:
        if not form.get("password"):
            form["password_secret"] = old.get("password_secret")
            form["password"] = old.get("password")
        else:
            form["password_secret"] = encrypt_secret(form["password"])
    save_mail_profile(form, mail_id)
    audit("update_mail", str(mail_id))
    return RedirectResponse("/mail-profiles", status_code=303)


@app.post("/mail-profiles/{mail_id}/delete", dependencies=[Depends(require_login)])
def remove_mail_profile(mail_id: int) -> RedirectResponse:
    delete_mail_profile(mail_id)
    audit("delete_mail", str(mail_id))
    return RedirectResponse("/mail-profiles", status_code=303)


@app.post("/mail-profiles/{mail_id}/test", dependencies=[Depends(require_login)])
def test_mail_profile(mail_id: int, test_recipient: str = Form("")) -> RedirectResponse:
    profile = require_value(get_mail_profile(mail_id), "邮件配置不存在")
    recipient = test_recipient.strip()
    if not recipient:
        recipient = str(profile.get("recipients") or "").strip()
    if not recipient:
        recipient = str(profile.get("sender") or profile.get("username") or "").strip()
    if not recipient:
        from urllib.parse import quote
        return RedirectResponse(f"/mail-profiles?error={quote('收件人为空且无法提取默认收件人，请指定测试收件邮箱')}", status_code=303)
        
    import tempfile
    from docx import Document
    from urllib.parse import quote
    from app.mailer import send_report_mail

    temp_dir = tempfile.gettempdir()
    test_doc_path = os.path.join(temp_dir, "test_report.docx")
    try:
        doc = Document()
        doc.add_heading("邮件发送配置测试报告", level=1)
        doc.add_paragraph("这是一封由系统自动生成的巡检报告测试附件。")
        doc.add_paragraph("如果您收到这封邮件，说明您的 SMTP 邮件服务配置成功，且能够成功传递带附件的报告。")
        doc.save(test_doc_path)
        
        test_profile = dict(profile)
        test_profile["recipients"] = recipient
        test_profile["cc"] = ""
        
        dummy_job = {
            "name": "邮件配置连接测试",
            "environment": "SRE测试环境",
            "time_range_label": "即时"
        }
        send_report_mail(test_profile, dummy_job, test_doc_path)
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        app_logger.error(f"测试发送邮件失败: {err_msg}", exc_info=True)
        return RedirectResponse(f"/mail-profiles?error={quote(err_msg)}", status_code=303)
    finally:
        if os.path.exists(test_doc_path):
            try:
                os.remove(test_doc_path)
            except Exception:
                pass
                
    from urllib.parse import quote
    return RedirectResponse(f"/mail-profiles?success={quote('🎉 测试邮件已成功发出！请检查您的邮箱收件箱。')}", status_code=303)


@app.post("/mail-profiles/{mail_id}/test-async", dependencies=[Depends(require_login)])
def test_mail_profile_async(mail_id: int, test_recipient: str = Form("")) -> dict[str, Any]:
    profile = get_mail_profile(mail_id)
    if not profile:
        return {"success": False, "message": "邮件配置不存在"}
        
    recipient = test_recipient.strip()
    if not recipient:
        recipient = str(profile.get("recipients") or "").strip()
    if not recipient:
        recipient = str(profile.get("sender") or profile.get("username") or "").strip()
    if not recipient:
        return {"success": False, "message": "收件人为空且无法提取默认收件人，请指定测试收件邮箱"}
        
    import tempfile
    from docx import Document
    from app.mailer import send_report_mail

    temp_dir = tempfile.gettempdir()
    test_doc_path = os.path.join(temp_dir, "test_report.docx")
    try:
        doc = Document()
        doc.add_heading("邮件发送配置测试报告", level=1)
        doc.add_paragraph("这是一封由系统自动生成的巡检报告测试附件。")
        doc.add_paragraph("如果您收到这封邮件，说明您的 SMTP 邮件服务配置成功，且能够成功传递带附件的报告。")
        doc.save(test_doc_path)
        
        test_profile = dict(profile)
        test_profile["recipients"] = recipient
        test_profile["cc"] = ""
        
        dummy_job = {
            "name": "邮件配置连接测试",
            "environment": "SRE测试环境",
            "time_range_label": "即时"
        }
        send_report_mail(test_profile, dummy_job, test_doc_path)
        return {"success": True, "message": "🎉 测试邮件已成功发出！请检查您的邮箱收件箱。"}
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        app_logger.error(f"测试发送邮件失败: {err_msg}", exc_info=True)
        return {"success": False, "message": f"测试邮件发送失败: {err_msg}"}
    finally:
        if os.path.exists(test_doc_path):
            try:
                os.remove(test_doc_path)
            except Exception:
                pass


@app.get("/runs", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def runs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "runs.html",
        {
            "request": request,
            "runs": list_runs(settings.max_recent_runs),
            "watch": request.query_params.get("watch", ""),
        },
    )


@app.get("/help", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def help_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "help.html",
        {"request": request, "title": "配置说明与指南"},
    )



@app.get("/runs/{run_id}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def run_detail(request: Request, run_id: int) -> HTMLResponse:
    run = require_value(get_run(run_id), "运行记录不存在")
    results = [with_file_url(item) for item in list_screenshot_results(run_id)]
    if run.get("report_path"):
        filename = Path(run["report_path"]).name
        local_path = settings.report_dir / str(run_id) / filename
        if local_path.exists():
            run["report_url"] = f"/artifact/reports/{run_id}/{filename}"
    return templates.TemplateResponse(
        "run_detail.html",
        {
            "request": request,
            "run": run,
            "results": results,
            "watch": request.query_params.get("watch", ""),
        },
    )


@app.get("/artifact/{kind}/{run_id}/{filename}", dependencies=[Depends(require_login)])
def artifact(kind: str, run_id: int, filename: str) -> FileResponse:
    base = {"screenshots": settings.screenshot_dir, "reports": settings.report_dir}.get(kind)
    if base is None:
        raise HTTPException(status_code=404, detail="未知文件类型")
    path = (base / str(run_id) / filename).resolve()
    if not is_under(path, base.resolve()) or not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


def require_value(value: Any, message: str) -> Any:
    if value is None:
        raise HTTPException(status_code=404, detail=message)
    return value


def with_file_url(item: dict[str, Any]) -> dict[str, Any]:
    file_path = item.get("file_path") or ""
    run_id = item.get("run_id")
    if file_path and run_id:
        filename = Path(file_path).name
        local_path = settings.screenshot_dir / str(run_id) / filename
        if local_path.exists():
            item["file_url"] = f"/artifact/screenshots/{run_id}/{filename}"
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
    return int(value)


# ==============================================================================
# 周期报告与数据库重置路由
# ==============================================================================

@app.post("/settings/reset", dependencies=[Depends(require_login)])
def reset_database() -> RedirectResponse:
    from app.db import reset_database_data
    from app.scheduler import reload_jobs
    
    app_logger.info("用户请求一键重置所有本地测试数据")
    try:
        reset_database_data()
        reload_jobs()
    except Exception as exc:
        app_logger.error(f"重置数据库失败: {exc}", exc_info=True)
        
    from urllib.parse import quote
    return RedirectResponse(f"/?success={quote('⚡ 系统所有配置及历史数据已成功清空并重置！')}", status_code=303)


@app.get("/periodic-reports", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def periodic_reports_list(request: Request) -> HTMLResponse:
    from app.repository import list_periodic_report_settings, list_periodic_report_runs
    settings_list = list_periodic_report_settings()
    runs_list = list_periodic_report_runs(50)
    
    formatted_runs = []
    for r in runs_list:
        run_dict = dict(r)
        z_path = r.get("zip_path")
        if z_path and os.path.exists(z_path):
            size_bytes = os.path.getsize(z_path)
            if size_bytes < 1024 * 1024:
                run_dict["size_str"] = f"{size_bytes / 1024:.2f} KB"
            else:
                run_dict["size_str"] = f"{size_bytes / (1024 * 1024):.2f} MB"
            run_dict["filename"] = Path(z_path).name
        else:
            run_dict["size_str"] = "-"
            run_dict["filename"] = ""
        formatted_runs.append(run_dict)

    return templates.TemplateResponse(
        "periodic_reports.html",
        {
            "request": request,
            "settings": settings_list,
            "runs": formatted_runs,
            "success": request.query_params.get("success", ""),
            "error": request.query_params.get("error", "")
        }
    )


@app.get("/periodic-reports/{report_type}/edit", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def edit_periodic_report(request: Request, report_type: str) -> HTMLResponse:
    from app.repository import get_periodic_report_setting, list_mail_profiles
    setting = get_periodic_report_setting(report_type)
    if not setting:
        raise HTTPException(status_code=404, detail="周期报告配置不存在")
        
    return templates.TemplateResponse(
        "periodic_form.html",
        {
            "request": request,
            "setting": setting,
            "mail_profiles": list_mail_profiles(),
            "action": f"/periodic-reports/{report_type}/edit"
        }
    )


@app.post("/periodic-reports/{report_type}/edit", dependencies=[Depends(require_login)])
def update_periodic_report(
    report_type: str,
    enabled: str | None = Form(None),
    name: str = Form(...),
    schedule_mode: str = Form("simple"),
    cron_expression: str = Form(""),
    mail_profile_id: str | None = Form(None),
    include_docx: str | None = Form(None),
    include_screenshots: str | None = Form(None),
    send_empty_report: str | None = Form(None),
    wait_for_daily_jobs: str | None = Form(None),
    retry_until_time: str = Form("23:50"),
    retry_interval_minutes: int = Form(10),
    recipients_override: str = Form(""),
    frequency: str = Form("weekly"),
    day_of_week: str | None = Form(None),
    day_of_month: str | None = Form(None),
    ampm: list[str] = Form([]),
    hour: list[int] = Form([]),
    minute: list[int] = Form([]),
    send_on_timeout: int = Form(1),
) -> RedirectResponse:
    import json
    from app.utils import times_to_cron, validate_cron_expression
    from app.repository import save_periodic_report_setting
    from app.scheduler import reload_jobs
    from urllib.parse import quote

    times_list = []
    for i in range(len(hour)):
        times_list.append({
            "ampm": ampm[i] if i < len(ampm) else "am",
            "hour": int(hour[i]),
            "minute": int(minute[i]) if i < len(minute) else 0
        })

    if schedule_mode == "simple":
        if frequency == "monthly_last":
            cron_list = []
            label_times = []
            for t in times_list:
                t_ampm = t.get("ampm", "am")
                t_hour = int(t.get("hour", 9))
                t_minute = int(t.get("minute", 0))
                
                if t_ampm == "pm" and t_hour < 12:
                    h_24 = t_hour + 12
                elif t_ampm == "am" and t_hour == 12:
                    h_24 = 0
                else:
                    h_24 = t_hour
                cron_list.append(f"0 {t_minute} {h_24} * * *")
                t_ampm_cn = "上午" if t_ampm == "am" else "下午"
                label_times.append(f"{t_ampm_cn}{t_hour}点{t_minute:02d}分" if t_minute else f"{t_ampm_cn}{t_hour}点")
            
            cron_expr = ";".join(cron_list)
            label_expr = f"每月最后一天 {', '.join(label_times)}"
            schedule_config = json.dumps({
                "frequency": "monthly_last",
                "times": times_list
            })
        else:
            cron_expr, label_expr = times_to_cron(frequency, day_of_week, day_of_month, times_list)
            schedule_config = json.dumps({
                "frequency": frequency,
                "day_of_week": day_of_week,
                "day_of_month": day_of_month,
                "times": times_list
            })
    else:
        cron_expr = cron_expression
        label_expr = "高级 Cron"
        schedule_config = ""

    try:
        validate_cron_expression(cron_expr)
    except ValueError as e:
        return RedirectResponse(f"/periodic-reports/{report_type}/edit?error={quote(str(e))}", status_code=303)

    update_data = {
        "enabled": 1 if enabled else 0,
        "name": name,
        "schedule_mode": schedule_mode,
        "schedule_label": label_expr,
        "cron_expression": cron_expr,
        "mail_profile_id": parse_optional_int(mail_profile_id),
        "include_screenshots": 1 if include_screenshots else 0,
        "include_docx": 1 if include_docx else 0,
        "send_empty_report": 1 if send_empty_report else 0,
        "wait_for_daily_jobs": 1 if wait_for_daily_jobs else 0,
        "retry_until_time": retry_until_time,
        "retry_interval_minutes": retry_interval_minutes,
        "recipients_override": recipients_override,
        "schedule_config": schedule_config,
        "send_on_timeout": send_on_timeout,
    }
    
    save_periodic_report_setting(report_type, update_data)
    reload_jobs()
    return RedirectResponse(f"/periodic-reports?success={quote('🎉 周期汇总配置保存成功！')}", status_code=303)


@app.post("/periodic-reports/{report_type}/run", dependencies=[Depends(require_login)])
def run_periodic_report_now(report_type: str, background_tasks: BackgroundTasks) -> RedirectResponse:
    from app.periodic_reporter import trigger_periodic_report
    from urllib.parse import quote
    background_tasks.add_task(trigger_periodic_report, report_type, True)
    return RedirectResponse(f"/periodic-reports?success={quote('📊 周期汇总报告生成任务已在后台启动，打包发信中，请稍候刷新页面查看。')}", status_code=303)


@app.get("/periodic-reports/download/{run_id}", dependencies=[Depends(require_login)])
def download_periodic_report_zip(run_id: int) -> FileResponse:
    from app.repository import get_periodic_report_run
    run = get_periodic_report_run(run_id)
    if not run or not run.get("zip_path"):
        raise HTTPException(status_code=404, detail="周期报告包不存在")
    path = Path(run["zip_path"]).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="周期报告物理文件不存在")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/periodic-reports/delete/{run_id}", dependencies=[Depends(require_login)])
def delete_periodic_report_run_route(run_id: int) -> RedirectResponse:
    from app.repository import get_periodic_report_run, delete_periodic_report_run
    run = get_periodic_report_run(run_id)
    if run:
        z_path = run.get("zip_path")
        if z_path and os.path.exists(z_path):
            try:
                os.remove(z_path)
            except Exception:
                pass
        delete_periodic_report_run(run_id)
    return RedirectResponse("/periodic-reports", status_code=303)
