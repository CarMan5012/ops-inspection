from __future__ import annotations

import os
import re
import time
import logging
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.repository import get_auth_profile
from app.settings import settings
from app.utils import safe_name, timestamp_text, resolve_auth_credential
from app.storage_paths import get_screenshot_dir

logger = logging.getLogger("app.screenshot")

DEFAULT_WATERMARK_TEMPLATE = "{time}\n省客户服务中心\ndwangchengyi7(王诚毅)"
DEFAULT_WATERMARK_FONT_SIZE = 24
DEFAULT_WATERMARK_TEXT_SPACING = 10
DEFAULT_WATERMARK_TILE_PADDING = 45
DEFAULT_WATERMARK_TILE_GAP_X = 140
DEFAULT_WATERMARK_TILE_GAP_Y = 140
DEFAULT_WATERMARK_OPACITY = 65
DEFAULT_WATERMARK_ANGLE = -45
DEFAULT_TASKBAR_TEMPLATE_PATH = Path(__file__).resolve().parent / "static" / "images" / "windows-taskbar-template.png"


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
    browser: Any = None,
    contexts: dict[Any, Any] = None,
) -> CaptureResult:
    last_error = ""
    retry_count = int(item.get("retry_count") or 0)
    logger.info(f"====> 开始截图项: '{item.get('name')}' (ID: {item.get('id')}, 运行 ID: {run_id})")
    for attempt in range(retry_count + 1):
        try:
            if attempt > 0:
                logger.info(f"第 {attempt} 次重试...")
            return _capture_once(item, job, run_id, suffix=suffix, browser=browser, contexts=contexts)
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



def _browser_scale_factor(job: dict[str, Any]) -> float:
    try:
        raw_scale = job.get("browser_scale_factor") if job.get("browser_scale_factor") is not None else 1.5
        scale_factor = float(raw_scale)
    except (TypeError, ValueError):
        scale_factor = 1.5
    return max(scale_factor, 0.1)


def _css_viewport_size(physical_width: int, physical_height: int, scale_factor: float) -> tuple[int, int]:
    return max(1, round(physical_width / scale_factor)), max(1, round(physical_height / scale_factor))

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
    browser: Any = None,
    contexts: dict[Any, Any] = None,
) -> CaptureResult:
    auth_profile = get_auth_profile(item.get("auth_profile_id"))
    width = int(item.get("browser_width") or job.get("browser_width") or 3840)
    height = int(item.get("browser_height") or job.get("browser_height") or 2160)
    scale_factor = _browser_scale_factor(job)
    timeout_ms = int(item.get("timeout_seconds") or 60) * 1000
    screenshot_dir = get_screenshot_dir(run_id)
    file_name = f"{safe_name(item.get('name', 'screenshot'))}{suffix}_{timestamp_text()}.png"
    output_path = screenshot_dir / file_name

    raw_url = str(item["url"])
    resolved_url = resolve_env_placeholders(raw_url)
    logger.info(f"原始 URL: {raw_url} | 解析后 URL: {resolved_url}")

    is_real_capture = bool(item.get("real_browser_capture") if "real_browser_capture" in item else 1)
    capture_mode = str(item.get("capture_mode") or "viewport")
    if is_real_capture and capture_mode == "full_page":
        logger.warning("启用真实浏览器截图时不支持整页长截图 (full_page)，已自动降级为视口截图 (viewport) 模式。")

    if browser is None:
        with sync_playwright() as p:
            headless_val = False if is_real_capture else True
            launch_kwargs = {
                "headless": headless_val,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    f"--force-device-scale-factor={scale_factor}",
                    "--high-dpi-support=1",
                    "--lang=zh-CN"
                ]
            }
            if is_real_capture:
                launch_kwargs["args"].extend([
                    "--window-position=0,0",
                    f"--window-size={width},{height}"
                ])
            logger.info(f"启动单次使用 chromium 浏览器 (headless={headless_val})...")
            tmp_browser = p.chromium.launch(**launch_kwargs)
            try:
                local_contexts = {}
                res = _capture_with_browser(
                    item, job, run_id, suffix, tmp_browser, width, height, timeout_ms,
                    screenshot_dir, file_name, output_path, resolved_url, is_real_capture,
                    auth_profile, local_contexts
                )
                return res
            finally:
                for ctx in list(local_contexts.values()):
                    try:
                        ctx.close()
                    except Exception:
                        pass
                tmp_browser.close()
    else:
        return _capture_with_browser(
            item, job, run_id, suffix, browser, width, height, timeout_ms,
            screenshot_dir, file_name, output_path, resolved_url, is_real_capture,
            auth_profile, contexts
        )


def _capture_with_browser(
    item: dict[str, Any],
    job: dict[str, Any],
    run_id: int,
    suffix: str,
    browser: Any,
    width: int,
    height: int,
    timeout_ms: int,
    screenshot_dir: Path,
    file_name: str,
    output_path: Path,
    resolved_url: str,
    is_real_capture: bool,
    auth_profile: dict[str, Any] | None,
    contexts: dict[Any, Any] | None,
) -> CaptureResult:
    auth_id = auth_profile["id"] if auth_profile else None
    scale_factor = _browser_scale_factor(job)
    viewport_width, viewport_height = _css_viewport_size(width, height, scale_factor)
    context_key = (auth_id, viewport_width, viewport_height, round(scale_factor, 3))
    logger.info(
        f"截图尺寸: physical={width}x{height}, css_viewport={viewport_width}x{viewport_height}, dpr={scale_factor}"
    )

    if contexts is not None and context_key in contexts:
        context = contexts[context_key]
    else:
        context = _new_context(browser, auth_profile, viewport_width, viewport_height, scale_factor)
        if contexts is not None:
            contexts[context_key] = context
            
    page = context.new_page()
    try:
        page.set_default_timeout(timeout_ms)
        page.set_viewport_size({"width": viewport_width, "height": viewport_height})
        
        _ensure_logged_in(context, page, auth_profile, timeout_ms)
        
        logger.info(f"正在加载截图 URL: {resolved_url}")
        page.goto(resolved_url, wait_until="domcontentloaded", timeout=timeout_ms)
        
        state_path = _storage_state_path(auth_profile)
        if state_path and state_path.exists() and _is_on_login_page(page, auth_profile):
            logger.warning("载入缓存状态文件后访问目标页面，却被重定向回了登录页。将清空失效的缓存文件并尝试重新登录自愈！")
            safe_delete_auth_state(auth_profile)
            
            page.close()
            if contexts is not None and context_key in contexts:
                try:
                    contexts[context_key].close()
                except Exception:
                    pass
                del contexts[context_key]
                
            context = _new_context(browser, auth_profile, viewport_width, viewport_height, scale_factor)
            if contexts is not None:
                contexts[context_key] = context
                
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.set_viewport_size({"width": viewport_width, "height": viewport_height})
            
            _ensure_logged_in(context, page, auth_profile, timeout_ms)
            logger.info(f"登录自愈重试完毕，再次载入截图页面 URL: {resolved_url}")
            page.goto(resolved_url, wait_until="domcontentloaded", timeout=timeout_ms)

        _assert_not_login_page_before_screenshot(page, auth_profile)
        _wait_for_page(page, item, timeout_ms)
        _assert_not_login_page_before_screenshot(page, auth_profile)
        _take_screenshot(page, item, output_path, is_real_capture, width, height)
        _apply_configured_watermark(output_path, item)
        _apply_simulated_taskbar(output_path, item)
        _persist_storage_state(context, auth_profile)
    finally:
        try:
            page.close()
        except Exception:
            pass
            
    logger.info(f"截图成功并保存至: {output_path}")
    return CaptureResult(status="success", file_path=str(output_path))


def _new_context(browser: Any, auth_profile: dict[str, Any] | None, width: int, height: int, scale_factor: float = 1.5) -> BrowserContext:
    state_path = _storage_state_path(auth_profile)
    kwargs: dict[str, Any] = {
        "viewport": {"width": width, "height": height},
        "ignore_https_errors": True,
        "locale": "zh-CN",
        "timezone_id": settings.default_timezone,
        "device_scale_factor": scale_factor,
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


def _is_on_login_page(page: Page, auth_profile: dict[str, Any] | None) -> bool:
    """判定当前页面是否已退回到或处于登录页表单状态"""
    current_url = str(page.url).lower()

    raw_login_url = str(auth_profile.get("login_url") or "").strip() if auth_profile else ""
    login_url = resolve_env_placeholders(raw_login_url).strip().lower()
    if login_url:
        from urllib.parse import urlsplit

        current_parts = urlsplit(current_url)
        login_parts = urlsplit(login_url)
        current_key = (current_parts.scheme, current_parts.netloc, current_parts.path.rstrip("/"))
        login_key = (login_parts.scheme, login_parts.netloc, login_parts.path.rstrip("/"))
        if current_key == login_key:
            return True

    # 1. URL 特征识别
    if "/login" in current_url or "/signin" in current_url:
        return True

    # 2. 密码框可见性识别
    try:
        password_count = page.locator(DEFAULT_PASSWORD_SELECTOR).count()
        if password_count > 0:
            if page.locator(DEFAULT_PASSWORD_SELECTOR).first.is_visible():
                return True
    except Exception:
        pass

    return False


def _assert_not_login_page_before_screenshot(page: Page, auth_profile: dict[str, Any] | None) -> None:
    if not _is_on_login_page(page, auth_profile):
        return
    if not auth_profile or auth_profile.get("auth_type") in {"", "none"}:
        raise RuntimeError("页面需要登录，请先为截图项关联登录认证配置，已停止截图以避免保存登录页。")
    profile_name = auth_profile.get("name") or auth_profile.get("id") or "当前认证配置"
    raise RuntimeError(f"登录配置 {profile_name} 未登录成功或登录态已失效，已停止截图以避免保存登录页。")


def safe_delete_auth_state(auth_profile: dict[str, Any]) -> None:
    """安全地物理删除该认证配置对应的缓存 JSON 状态文件"""
    from app.cleanup import is_safe_path
    
    try:
        state_path = _storage_state_path(auth_profile)
        if not state_path or not state_path.exists():
            return
            
        if not state_path.is_file():
            logger.warning(f"安全拦截：浏览器状态缓存对象不是文件，跳过删除: {state_path}")
            return
            
        try:
            resolved_state = state_path.resolve()
            resolved_state_dir = settings.browser_state_dir.resolve()
            in_browser_state_dir = resolved_state_dir in resolved_state.parents
        except Exception:
            in_browser_state_dir = False
            
        in_data_dir = is_safe_path(state_path)
        is_custom_path = bool(str(auth_profile.get("storage_state_path") or "").strip())
        
        if in_browser_state_dir or in_data_dir or is_custom_path:
            state_path.unlink(missing_ok=True)
            logger.info(f"成功清理浏览器 session 状态缓存文件: {state_path}")
        else:
            logger.warning(f"安全拦截：试图物理删除非安全路径下的状态缓存文件: {state_path}")
    except Exception as exc:
        logger.error(f"清理浏览器状态缓存文件失败: {exc}", exc_info=True)




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


def _current_watermark_time() -> str:
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(settings.default_timezone))
    except Exception:
        now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


def _watermark_text(template: str | None = None) -> str:
    raw_template = (template or DEFAULT_WATERMARK_TEMPLATE).strip() or DEFAULT_WATERMARK_TEMPLATE
    try:
        return raw_template.format(time=_current_watermark_time())
    except Exception:
        return raw_template.replace("{time}", _current_watermark_time())


def _watermark_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _watermark_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _apply_configured_watermark(image_path: Path, item: dict[str, Any]) -> None:
    if not _watermark_bool(item.get("watermark_enabled"), False):
        return

    _apply_watermark(
        image_path=image_path,
        text=_watermark_text(str(item.get("watermark_text") or "")),
        opacity=_watermark_int(item.get("watermark_opacity"), DEFAULT_WATERMARK_OPACITY, 0, 255),
        font_size=_watermark_int(item.get("watermark_font_size"), DEFAULT_WATERMARK_FONT_SIZE, 10, 72),
        gap_x=_watermark_int(item.get("watermark_gap_x"), DEFAULT_WATERMARK_TILE_GAP_X, 20, 800),
        gap_y=_watermark_int(item.get("watermark_gap_y"), DEFAULT_WATERMARK_TILE_GAP_Y, 20, 800),
        angle=_watermark_int(item.get("watermark_angle"), DEFAULT_WATERMARK_ANGLE, -90, 90),
    )


def _taskbar_template_path() -> Path:
    return Path(os.getenv("TASKBAR_TEMPLATE_PATH", str(DEFAULT_TASKBAR_TEMPLATE_PATH))).resolve()


def _format_taskbar_clock(now: datetime) -> tuple[str, str]:
    return now.strftime("%H:%M"), f"{now.year}/{now.month}/{now.day}"


def _fit_taskbar_template(template: Any, target_width: int) -> Any:
    if target_width <= 0:
        raise ValueError("target_width must be greater than 0")
    if template.width == target_width:
        return template.copy()

    resample = getattr(getattr(template, "Resampling", template), "LANCZOS", None)
    if resample is None:
        from PIL import Image

        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
    target_height = max(1, round(template.height * target_width / template.width))
    return template.resize((target_width, target_height), resample=resample)


def _taskbar_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(settings.default_timezone))
    except Exception:
        return datetime.now()


def _taskbar_font_paths() -> list[Path]:
    configured = os.getenv("TASKBAR_CLOCK_FONT_PATH", "").strip()
    paths = [
        settings.data_dir / "fonts" / "msyh.ttc",
        Path(__file__).resolve().parent / "static" / "fonts" / "msyh.ttc",
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    if configured:
        paths.insert(0, Path(configured))
    return paths


def _taskbar_font(size: int) -> Any:
    from PIL import ImageFont

    for font_path in _taskbar_font_paths():
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_taskbar_clock(taskbar: Any, now: datetime) -> None:
    from PIL import ImageDraw

    time_str, date_str = _format_taskbar_clock(now)
    width, height = taskbar.size
    sample = taskbar.getpixel((max(0, width - max(4, round(height * 0.35))), max(0, height // 8)))
    bg = sample[:3] if len(sample) == 3 else sample[:3]
    brightness = sum(bg[:3]) / 3
    text_color = (24, 28, 32, 255) if brightness > 150 else (245, 245, 245, 255)

    draw = ImageDraw.Draw(taskbar)
    font = _taskbar_font(max(12, round(height * 0.24)))
    right = width - max(30, round(height * 0.44))
    gap = max(3, round(height * 0.11))
    time_bbox = draw.textbbox((0, 0), time_str, font=font)
    date_bbox = draw.textbbox((0, 0), date_str, font=font)
    time_h = time_bbox[3] - time_bbox[1]
    date_h = date_bbox[3] - date_bbox[1]
    top = max(0, round((height - time_h - gap - date_h) / 2))

    rows = (
        (time_str, time_bbox, top - time_bbox[1]),
        (date_str, date_bbox, top + time_h + gap - date_bbox[1]),
    )
    for text, bbox, y in rows:
        draw.text((right - bbox[2], y), text, fill=text_color, font=font)


def _apply_taskbar_template(image_path: Path, template_path: Path) -> None:
    from PIL import Image

    with Image.open(image_path) as orig_img:
        width, height = orig_img.size
        img_rgba = orig_img.convert("RGBA")
        img_rgba.load()

    with Image.open(template_path) as template_img:
        taskbar = _fit_taskbar_template(template_img.convert("RGBA"), width)
        taskbar.load()

    _draw_taskbar_clock(taskbar, _taskbar_now())

    combined = Image.new("RGBA", (width, height + taskbar.height))
    combined.paste(img_rgba, (0, 0))
    combined.paste(taskbar, (0, height))

    if image_path.suffix.lower() in {".jpg", ".jpeg"}:
        combined.convert("RGB").save(image_path)
    else:
        combined.save(image_path)

    logger.info(f"Applied real Windows taskbar template: {template_path} -> {image_path}")


def _apply_simulated_taskbar(image_path: Path, item: dict[str, Any]) -> None:
    if not _watermark_bool(item.get("taskbar_enabled"), True):
        return

    try:
        from PIL import Image  # noqa: F401
    except ImportError as err:
        raise RuntimeError("Taskbar template composition failed: Pillow is not installed") from err

    if not image_path.exists():
        raise FileNotFoundError(f"Taskbar template composition failed: screenshot file not found {image_path}")

    template_path = _taskbar_template_path()
    if not template_path.exists():
        logger.warning(f"Windows 任务栏模板未找到，已跳过拼接底栏美化逻辑。模板路径: {template_path}")
        return

    try:
        _apply_taskbar_template(image_path, template_path)
    except Exception as exc:
        logger.error(f"拼接任务栏底栏失败: {exc}，已降级为不带底栏的原图。", exc_info=True)


def _apply_default_watermark(image_path: Path) -> None:
    _apply_watermark(
        image_path=image_path,
        text=_watermark_text(DEFAULT_WATERMARK_TEMPLATE),
        opacity=DEFAULT_WATERMARK_OPACITY,
        font_size=DEFAULT_WATERMARK_FONT_SIZE,
        gap_x=DEFAULT_WATERMARK_TILE_GAP_X,
        gap_y=DEFAULT_WATERMARK_TILE_GAP_Y,
        angle=DEFAULT_WATERMARK_ANGLE,
    )


def _apply_watermark(
    image_path: Path,
    text: str,
    opacity: int,
    font_size: int,
    gap_x: int,
    gap_y: int,
    angle: int,
) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as err:
        raise RuntimeError("截图水印处理失败：未安装 Pillow，请先安装 requirements.txt 中的 Pillow 依赖") from err

    if not image_path.exists():
        raise FileNotFoundError(f"截图水印处理失败：图片文件不存在 {image_path}")

    font_paths = [
        r"C:\Windows\Fonts\dengl.ttf",
        r"C:\Windows\Fonts\deng.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    font = None
    for path_str in font_paths:
        font_path = Path(path_str)
        if font_path.exists():
            try:
                font = ImageFont.truetype(str(font_path), font_size)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    with Image.open(image_path) as orig_img:
        img_rgba = orig_img.convert("RGBA")
        img_rgba.load()

    width, height = img_rgba.size

    temp_img = Image.new("RGBA", (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    try:
        bbox = temp_draw.multiline_textbbox(
            (0, 0), text, font=font, spacing=DEFAULT_WATERMARK_TEXT_SPACING, align="center"
        )
        offset_x = bbox[0]
        offset_y = bbox[1]
        txt_w = bbox[2] - bbox[0]
        txt_h = bbox[3] - bbox[1]
    except AttributeError:
        offset_x = 0
        offset_y = 0
        txt_w = max(len(line) for line in text.split("\n")) * font_size
        txt_h = len(text.split("\n")) * (font_size + DEFAULT_WATERMARK_TEXT_SPACING)

    txt_img_w = int(txt_w + 1) + DEFAULT_WATERMARK_TILE_PADDING * 2
    txt_img_h = int(txt_h + 1) + DEFAULT_WATERMARK_TILE_PADDING * 2
    txt_img = Image.new("RGBA", (txt_img_w, txt_img_h), (0, 0, 0, 0))
    draw_txt = ImageDraw.Draw(txt_img)
    draw_txt.multiline_text(
        (int(DEFAULT_WATERMARK_TILE_PADDING - offset_x), int(DEFAULT_WATERMARK_TILE_PADDING - offset_y)),
        text,
        font=font,
        fill=(0, 0, 0, 0),
        stroke_width=1,
        stroke_fill=(160, 160, 160, opacity),
        spacing=DEFAULT_WATERMARK_TEXT_SPACING,
        align="center",
    )

    resample_bilinear = getattr(getattr(Image, "Resampling", Image), "BILINEAR", Image.BILINEAR)
    rotated_txt = txt_img.rotate(angle, expand=True, resample=resample_bilinear)
    rot_w, rot_h = rotated_txt.size

    watermark_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    step_x = rot_w + gap_x
    step_y = rot_h + gap_y
    start_x = round(width * 0.04)
    start_y = round(height * 0.04)

    for y in range(start_y, height + rot_h, step_y):
        for x in range(start_x, width + rot_w, step_x):
            watermark_layer.paste(rotated_txt, (x, y), rotated_txt)

    combined = Image.alpha_composite(img_rgba, watermark_layer)
    if image_path.suffix.lower() in {".jpg", ".jpeg"}:
        combined.convert("RGB").save(image_path)
    else:
        combined.save(image_path)

    logger.info(f"已为截图添加水印: {image_path}")


def _trim_mss_desktop_margin(image_path: Path, max_trim: int = 40) -> None:
    from PIL import Image

    cropped_img = None
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size

        def is_black_column(x: int) -> bool:
            black = sum(1 for y in range(height) if max(rgb.getpixel((x, y))) <= 20)
            return black / height >= 0.98

        def is_black_row(y: int) -> bool:
            black = sum(1 for x in range(width) if max(rgb.getpixel((x, y))) <= 20)
            return black / width >= 0.98

        left = 0
        while left < min(max_trim, width - 1) and is_black_column(left):
            left += 1

        top = 0
        while top < min(max_trim, height - 1) and is_black_row(top):
            top += 1

        if left or top:
            cropped_img = img.crop((left, top, width, height))
            cropped_img.load()

    if cropped_img is not None:
        cropped_img.save(image_path)
        logger.info(f"Trimmed mss desktop margin: left={left}, top={top}, file={image_path}")


def _take_screenshot(
    page: Page,
    item: dict[str, Any],
    output_path: Path,
    is_real_capture: bool,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> None:
    if is_real_capture:
        logger.info("真实浏览器窗口截图模式已启用，正在使用 mss 进行系统级屏幕截图...")
        
        # 1. 验证 DISPLAY 环境变量
        import os
        display = os.getenv("DISPLAY")
        if not display:
            raise RuntimeError("真实浏览器窗口截图需要启用 Xvfb 或虚拟桌面环境")
            
        # 2. 验证 mss 库是否安装
        try:
            import mss
            import mss.tools
        except ImportError as err:
            raise RuntimeError(
                "真实浏览器窗口截图失败：未检测到 Python 系统截图依赖库 mss。\n"
                "请确保 requirements.txt 中包含了 mss，并在容器内执行了安装。"
            ) from err
            
        # 3. 执行系统级截屏
        try:
            # 真实地址栏必须来自 Chromium 窗口本身。为确保渲染就绪并置顶，略微等待 500ms
            page.wait_for_timeout(500)
            with mss.mss() as sct:
                if not sct.monitors or len(sct.monitors) < 2:
                    raise RuntimeError("系统截图失败：无法获取任何有效的虚拟显示器设备（sct.monitors 为空或不足）")
                # monitors[0] 是所有监视器的合集，monitors[1] 是第一个显示屏
                monitor = sct.monitors[1]
                monitor_width = int(monitor["width"])
                monitor_height = int(monitor["height"])
                target_width = int(expected_width or monitor_width)
                target_height = int(expected_height or monitor_height)
                if monitor_width < target_width or monitor_height < target_height:
                    raise RuntimeError(
                        f"虚拟屏幕尺寸 {monitor_width}x{monitor_height} 小于截图配置 {target_width}x{target_height}。"
                        "请设置 XVFB_WIDTH/XVFB_HEIGHT 与截图分辨率一致，并重建或重启容器。"
                    )
                capture_area = dict(monitor)
                capture_area["width"] = target_width
                capture_area["height"] = target_height
                sct_img = sct.grab(capture_area)
                mss.tools.to_png(sct_img.rgb, sct_img.size, output=str(output_path))
                _trim_mss_desktop_margin(output_path)
                logger.info(f"系统级真实窗口截图获取成功并保存至: {output_path}")
        except Exception as exc:
            raise RuntimeError(
                f"真实浏览器窗口截图捕获失败：{exc}。\n"
                "请确保 Xvfb 服务正常运行在后台，且没有其他窗口遮挡。"
            ) from exc
    else:
        # 普通 Playwright 截图
        capture_mode = str(item.get("capture_mode") or "viewport")
        selector = str(item.get("css_selector") or "").strip()
        logger.info(f"开始普通截图操作. 模式: {capture_mode}, 局部选择器: '{selector}'")
        if capture_mode == "selector" and selector:
            locator = page.locator(selector).first
            locator.wait_for(state="visible")
            locator.screenshot(path=str(output_path))
        else:
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
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--force-device-scale-factor=1",
                    "--high-dpi-support=1"
                ]
            )
            context = browser.new_context(
                ignore_https_errors=True,
                locale="zh-CN",
                timezone_id=settings.default_timezone,
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1.0,
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
