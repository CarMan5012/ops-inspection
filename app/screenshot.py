from __future__ import annotations

import os
import re
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.repository import get_auth_profile
from app.settings import settings
from app.utils import safe_name, timestamp_text, resolve_auth_credential

logger = logging.getLogger("app.screenshot")


@dataclass
class CaptureResult:
    status: str
    file_path: str = ""
    error_message: str = ""


def resolve_env_placeholders(text: str) -> str:
    if not text:
        return text
    def replace_match(match):
        var_name = match.group(1)
        val = os.getenv(var_name)
        if val is not None:
            return val
        return match.group(0)
    return re.sub(r"\{([A-Za-z0-9_]+)\}", replace_match, text)



def capture_item(
    item: dict[str, Any],
    job: dict[str, Any],
    run_id: int,
    suffix: str = "",
) -> CaptureResult:
    last_error = ""
    retry_count = int(item.get("retry_count") or 0)
    logger.info(f"====> 开始截图项: '{item.get('name')}' (ID: {item.get('id')}, 运行 ID: {run_id})")
    for attempt in range(retry_count + 1):
        try:
            if attempt > 0:
                logger.info(f"第 {attempt} 次重试...")
            return _capture_once(item, job, run_id, suffix=suffix)
        except Exception as exc:  # noqa: BLE001 - the error is saved into run history.
            last_error = f"{type(exc).__name__}: {exc}"
            logger.error(f"截图尝试失败 (当前第 {attempt} 次/最多 {retry_count} 次重试): {last_error}", exc_info=True)
            if attempt < retry_count:
                time.sleep(min(2 + attempt, 5))
    logger.error(f"====> 截图项 '{item.get('name')}' 最终失败. 错误: {last_error}")
    return CaptureResult(status="failed", error_message=last_error)


DEFAULT_USERNAME_SELECTOR = (
    'input[name="user"], input[name="username"], input[type="email"], '
    'input[placeholder*="username"], input[placeholder*="Username"], '
    'input[placeholder*="email"], input[placeholder*="Email"], '
    'input[placeholder*="user"], input[placeholder*="User"], '
    'input[aria-label*="username" i], input[aria-label*="email" i], '
    'input[id*="user" i], input[id*="login" i]'
)
DEFAULT_PASSWORD_SELECTOR = (
    'input[name="password"], input[type="password"], '
    'input[placeholder*="password"], input[placeholder*="Password"], '
    'input[aria-label*="password" i], input[id*="password" i]'
)
DEFAULT_SUBMIT_SELECTOR = (
    'button[type="submit"], button:has-text("Log in"), button:has-text("Login"), '
    'button:has-text("登录"), input[type="submit"]'
)


def _selector_with_fallback(configured_selector: Any, fallback_selector: str) -> str:
    configured = str(configured_selector or "").strip()
    if not configured:
        return fallback_selector
    return f"{configured}, {fallback_selector}"


def _page_has_grafana_asset_error(page: Page) -> bool:
    try:
        page.get_by_text("If you're seeing this Grafana has failed to load its application files").wait_for(
            state="visible",
            timeout=500,
        )
        return True
    except PlaywrightTimeoutError:
        return False
    except Exception:
        return False


def _assert_login_page_ready(page: Page, login_url: str) -> None:
    if _page_has_grafana_asset_error(page):
        raise RuntimeError(
            "Grafana 登录页加载失败：前端资源没有正常加载。"
            "请检查 Grafana 地址是否能从巡检服务访问，以及 Grafana 的 root_url 是否与当前访问地址匹配。"
        )
    title = ""
    try:
        title = page.title()
    except Exception:
        pass
    if "grafana has failed to load" in title.lower():
        raise RuntimeError(f"Grafana 登录页加载失败，请检查 Grafana root_url 配置。当前登录地址：{login_url}")


def _is_login_failure_visible(page: Page) -> bool:
    failure_patterns = [
        "Login failed",
        "Invalid username or password",
        "Invalid username",
        "invalid username",
        "incorrect",
        "Unauthorized",
        "用户名或密码",
        "登录失败",
        "认证失败",
    ]
    for pattern in failure_patterns:
        try:
            page.get_by_text(pattern, exact=False).wait_for(state="visible", timeout=500)
            return True
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return False


def _raise_if_login_failed(page: Page) -> None:
    if _is_login_failure_visible(page):
        raise RuntimeError("登录验证失败：用户名或密码错误，请检查页面中填写的账号和密码。")


def _wait_for_login_success(page: Page, success_selector: str, timeout_ms: int) -> None:
    deadline = time.time() + min(timeout_ms, 30_000) / 1000
    last_error = ""
    success_selector = success_selector.strip()

    while time.time() < deadline:
        _raise_if_login_failed(page)

        if success_selector and success_selector != "body":
            try:
                page.locator(success_selector).first.wait_for(state="visible", timeout=800)
                return
            except Exception as exc:
                last_error = str(exc)

        if "/login" not in page.url:
            return

        try:
            password_count = page.locator(DEFAULT_PASSWORD_SELECTOR).count()
            if password_count == 0:
                return
        except Exception as exc:
            last_error = str(exc)

        try:
            page.wait_for_timeout(300)
        except Exception:
            break

    _raise_if_login_failed(page)
    detail = f" 当前 URL: {page.url}"
    if last_error:
        detail += f"；最后一次检测信息: {last_error[:160]}"
    raise RuntimeError(f"登录后没有检测到成功状态。请检查账号密码、成功选择器或 Grafana 登录状态。{detail}")


def _fill_login_form(page: Page, auth_profile: dict[str, Any], username: str, password: str) -> None:
    username_selector = _selector_with_fallback(auth_profile.get("username_selector"), DEFAULT_USERNAME_SELECTOR)
    password_selector = _selector_with_fallback(auth_profile.get("password_selector"), DEFAULT_PASSWORD_SELECTOR)
    submit_selector = _selector_with_fallback(auth_profile.get("submit_selector"), DEFAULT_SUBMIT_SELECTOR)

    user_input = page.locator(username_selector).first
    password_input = page.locator(password_selector).first
    user_input.wait_for(state="visible", timeout=15_000)
    password_input.wait_for(state="visible", timeout=15_000)
    user_input.fill(username)
    password_input.fill(password)
    page.locator(submit_selector).first.click()


def _capture_once(
    item: dict[str, Any],
    job: dict[str, Any],
    run_id: int,
    suffix: str = "",
) -> CaptureResult:
    auth_profile = get_auth_profile(item.get("auth_profile_id"))
    width = int(item.get("browser_width") or job.get("browser_width") or 1920)
    height = int(item.get("browser_height") or job.get("browser_height") or 1080)
    timeout_ms = int(item.get("timeout_seconds") or 60) * 1000
    screenshot_dir = settings.screenshot_dir / str(run_id)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{safe_name(item.get('name', 'screenshot'))}{suffix}_{timestamp_text()}.png"
    output_path = screenshot_dir / file_name

    raw_url = str(item["url"])
    resolved_url = resolve_env_placeholders(raw_url)
    logger.info(f"原始 URL: {raw_url} | 解析后 URL: {resolved_url}")

    with sync_playwright() as p:
        logger.info(f"启动 chromium 浏览器 (headless={bool(job.get('headless', 1))})...")
        browser = p.chromium.launch(headless=bool(job.get("headless", 1)))
        context = _new_context(browser, auth_profile, width, height)
        try:
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            _ensure_logged_in(context, page, auth_profile, timeout_ms)
            
            logger.info(f"正在加载截图 URL: {resolved_url}")
            page.goto(resolved_url, wait_until="domcontentloaded", timeout=timeout_ms)
            _wait_for_page(page, item, timeout_ms)
            _take_screenshot(page, item, output_path)
            _persist_storage_state(context, auth_profile)
        finally:
            context.close()
            browser.close()

    logger.info(f"截图成功并保存至: {output_path}")
    return CaptureResult(status="success", file_path=str(output_path))


def _new_context(browser: Any, auth_profile: dict[str, Any] | None, width: int, height: int) -> BrowserContext:
    state_path = _storage_state_path(auth_profile)
    kwargs: dict[str, Any] = {
        "viewport": {"width": width, "height": height},
        "ignore_https_errors": True,
        "locale": "zh-CN",
        "timezone_id": settings.default_timezone,
    }
    if state_path and state_path.exists():
        logger.info(f"载入缓存的浏览器状态文件: {state_path}")
        kwargs["storage_state"] = str(state_path)
    return browser.new_context(**kwargs)


def _storage_state_path(auth_profile: dict[str, Any] | None) -> Path | None:
    if not auth_profile:
        return None
    raw_path = str(auth_profile.get("storage_state_path") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    if auth_profile.get("auth_type") in {"form", "state"}:
        name = safe_name(str(auth_profile.get("name") or f"auth_{auth_profile['id']}"))
        raw_login_url = str(auth_profile.get("login_url") or "").strip()
        login_url = resolve_env_placeholders(raw_login_url)
        if login_url:
            from urllib.parse import urlparse
            parsed = urlparse(login_url)
            host_str = safe_name(parsed.netloc or "default")
            return settings.browser_state_dir / f"{name}_{host_str}.json"
        return settings.browser_state_dir / f"{name}.json"
    return None



def _ensure_logged_in(
    context: BrowserContext,
    page: Page,
    auth_profile: dict[str, Any] | None,
    timeout_ms: int,
) -> None:
    if not auth_profile or auth_profile.get("auth_type") in {"", "none"}:
        return
    if auth_profile.get("auth_type") == "state":
        logger.info("认证类型为 state，信任本地存储状态。")
        return
    if auth_profile.get("auth_type") != "form":
        return
    state_path = _storage_state_path(auth_profile)
    if state_path and state_path.exists():
        logger.info(f"缓存状态文件已存在，跳过登录表单: {state_path}")
        return

    raw_login_url = str(auth_profile.get("login_url") or "").strip()
    login_url = resolve_env_placeholders(raw_login_url)
    
    creds = resolve_auth_credential(auth_profile)
    username = creds["username"]
    password = creds["password"]
        
    if not login_url:
        logger.info("登录配置没有 login_url，跳过表单登录。")
        return
    if not username or not password:
        raise RuntimeError(f"登录配置 {auth_profile['name']} 缺少用户名或密码配置，请在网页端核对配置")

    logger.info(f"开始执行表单登录. 登录页面: {login_url}")
    page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)
    _assert_login_page_ready(page, login_url)
    _fill_login_form(page, auth_profile, username, password)
    logger.info("已点击提交按钮，等待登录成功...")
    
    success_selector = str(auth_profile.get("success_selector") or "").strip()
    if success_selector and success_selector != "body":
        logger.info(f"等待登录成功指示选择器: '{success_selector}'")
    else:
        logger.info("未配置成功选择器，自动检测 URL、登录表单和失败提示。")
    _wait_for_login_success(page, success_selector, timeout_ms)
    logger.info(f"登录成功，当前 URL: {page.url}")
            
    logger.info("登录完成，正在将浏览器状态(Cookies等)保存至缓存文件...")
    _persist_storage_state(context, auth_profile)
    logger.info("浏览器登录状态保存成功！")


def _persist_storage_state(context: BrowserContext, auth_profile: dict[str, Any] | None) -> None:
    state_path = _storage_state_path(auth_profile)
    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_path))


def _wait_for_page(page: Page, item: dict[str, Any], timeout_ms: int) -> None:
    wait_selector = str(item.get("wait_selector") or "").strip()
    if wait_selector:
        logger.info(f"等待页面指定元素加载可见: {wait_selector}")
        page.wait_for_selector(wait_selector, state="visible", timeout=timeout_ms)
    try:
        logger.info("等待网络活动空闲 (networkidle)...")
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 30_000))
    except PlaywrightTimeoutError:
        logger.warning("等待 networkidle 超时，将强行继续后续步骤")
    wait_seconds = float(item.get("wait_seconds") or 0)
    if wait_seconds > 0:
        logger.info(f"额外等待页面静止渲染: {wait_seconds} 秒")
        page.wait_for_timeout(int(wait_seconds * 1000))


def _take_screenshot(page: Page, item: dict[str, Any], output_path: Path) -> None:
    capture_mode = str(item.get("capture_mode") or "full_page")
    selector = str(item.get("css_selector") or "").strip()
    logger.info(f"开始截图操作. 模式: {capture_mode}, 局部选择器: '{selector}'")
    if capture_mode == "selector" and selector:
        locator = page.locator(selector).first
        locator.wait_for(state="visible")
        locator.screenshot(path=str(output_path))
        return

    page.screenshot(path=str(output_path), full_page=(capture_mode == "full_page"))


def test_auth_profile_login(auth_profile_id: int) -> dict[str, Any]:
    """
    无头模式下通过 Playwright 模拟真实表单登录过程，以测试账号凭证与选择器的有效性
    """
    auth_profile = get_auth_profile(auth_profile_id)
    if not auth_profile:
        return {"success": False, "message": "认证配置不存在"}
        
    if auth_profile.get("auth_type") in {"", "none"}:
        return {"success": True, "message": "无需登录类型，验证成功"}
        
    creds = resolve_auth_credential(auth_profile)
    username = creds["username"]
    password = creds["password"]
    login_url = resolve_env_placeholders(str(auth_profile.get("login_url") or "").strip())
    
    if not login_url:
        return {"success": False, "message": "登录 URL 不能为空"}
    if not username or not password:
        return {"success": False, "message": "用户名或密码为空，请核对凭据配置"}
        
    timeout_ms = 30000
    try:
        with sync_playwright() as p:
            logger.info(f"启动 chromium 测试浏览器登录: {login_url}")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                locale="zh-CN",
                timezone_id=settings.default_timezone,
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            
            page.on("console", lambda msg: logger.warning(f"测试登录浏览器 Console: [{msg.type}] {msg.text}"))
            page.on("requestfailed", lambda req: logger.warning(f"测试登录浏览器 Request 失败: {req.url} - ({req.failure.error_text if req.failure else 'unknown'})"))
            
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)
                _assert_login_page_ready(page, login_url)
                _fill_login_form(page, auth_profile, username, password)
                logger.info("已点击提交，等待页面状态响应...")
                
                success_selector = str(auth_profile.get("success_selector") or "").strip()
                _wait_for_login_success(page, success_selector, timeout_ms)
            except Exception as inner_exc:
                try:
                    settings.log_dir.mkdir(parents=True, exist_ok=True)
                    error_path = settings.log_dir / "test_login_error.png"
                    page.screenshot(path=str(error_path))
                    logger.info(f"验证登录凭证异常，已保存错误截图至: {error_path}")
                except Exception as screenshot_exc:
                    logger.error(f"保存测试登录错误截图失败: {screenshot_exc}", exc_info=True)
                raise inner_exc
                
            context.close()
            browser.close()
        return {"success": True, "message": "测试登录成功！凭据配置及网页选择器匹配正常！"}
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.error(f"验证登录凭证异常: {err_msg}", exc_info=True)
        return {"success": False, "message": f"测试登录失败: {err_msg}"}
