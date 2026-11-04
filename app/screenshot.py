from __future__ import annotations

import os
import re
import time
import random
import logging
import tempfile
import math
from io import BytesIO
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.repository import get_auth_profile
from app.settings import settings
from app.utils import safe_name, now_local, resolve_auth_credential
from app.storage_paths import get_screenshot_dir, unique_artifact_path
from app.watermark import DEFAULT_TIME_FORMAT, WATERMARK_DEFAULTS, format_watermark_time, validate_watermark_alignment

logger = logging.getLogger("app.screenshot")

DEFAULT_WATERMARK_TEMPLATE = "王\n{time}"
DEFAULT_WATERMARK_FONT_SIZE = WATERMARK_DEFAULTS["watermark_font_size"]
DEFAULT_WATERMARK_TEXT_SPACING = 10
DEFAULT_WATERMARK_TILE_PADDING = 45
DEFAULT_WATERMARK_TILE_GAP_X = WATERMARK_DEFAULTS["watermark_gap_x"]
DEFAULT_WATERMARK_TILE_GAP_Y = WATERMARK_DEFAULTS["watermark_gap_y"]
DEFAULT_WATERMARK_OPACITY = WATERMARK_DEFAULTS["watermark_opacity"]
DEFAULT_WATERMARK_ANGLE = WATERMARK_DEFAULTS["watermark_angle"]
TASKBAR_TEMPLATE_DIR = Path(__file__).resolve().parent / "static" / "images"
TASKBAR_TEMPLATE_NAMES = ("windows-taskbar-template.png", "1.png", "2.png", "3.png")
DEFAULT_TASKBAR_TEMPLATE_PATH = TASKBAR_TEMPLATE_DIR / "windows-taskbar-template.png"


@dataclass
class CaptureResult:
    status: str
    file_path: str = ""
    error_message: str = ""
    duration_seconds: float = 0.0
    monitor_data: dict[str, Any] | None = None
    content_status: str = "not_applicable"
    content_summary: str = ""


@dataclass
class BrowserSession:
    browser: Any
    launch: Callable[[], Any]

    @classmethod
    def open(cls, launch: Callable[[], Any]) -> BrowserSession:
        return cls(browser=launch(), launch=launch)

    def restart(self) -> None:
        try:
            self.browser.close()
        except Exception:
            logger.debug("关闭故障浏览器失败", exc_info=True)
        self.browser = self.launch()

    def close(self) -> None:
        try:
            self.browser.close()
        except Exception:
            logger.debug("关闭浏览器失败", exc_info=True)


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
    browser_session: BrowserSession | None = None,
    item_index: int | None = None,
    total_items: int | None = None,
) -> CaptureResult:
    start_time = time.monotonic()
    last_error = ""
    retry_count = max(0, int(item.get("retry_count") or 0))
    total_seconds = max(1, int(item.get("total_timeout_seconds") or 240))
    total_deadline = start_time + total_seconds
    tag = f"[{item_index}/{total_items}] " if (item_index is not None and total_items is not None) else ""
    logger.info(f"截图开始 {tag}{item.get('name')}（记录 #{run_id}，最多尝试 {retry_count + 1} 次，总时限 {total_seconds} 秒）")
    for attempt in range(retry_count + 1):
        if time.monotonic() >= total_deadline:
            last_error = f"单项总耗时已达 {total_seconds} 秒，已停止重试"
            break
        try:
            if attempt > 0:
                logger.info(f"截图重试 {tag}{item.get('name')}：第 {attempt + 1}/{retry_count + 1} 次")
            browser = browser_session.browser if browser_session is not None else None
            res = _capture_once(item, job, run_id, suffix=suffix, browser=browser, total_deadline=total_deadline)
            duration = round(time.monotonic() - start_time, 2)
            res.duration_seconds = duration
            file_name = Path(res.file_path).name if res.file_path else ""
            logger.info(f"截图成功 {tag}{item.get('name')}：{file_name}，{res.content_summary or '页面已截取'}，耗时 {duration} 秒")
            return res
        except Exception as exc:  # noqa: BLE001 - the error is saved into run history.
            expired = time.monotonic() >= total_deadline
            failure_stage = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            last_error = (
                f"单项总耗时已达 {total_seconds} 秒，已停止重试；最后阶段：{failure_stage}"
                if expired else f"{type(exc).__name__}: {exc}"
            )
            reason = last_error if expired else failure_stage
            logger.warning(f"截图失败 {tag}{item.get('name')}（第 {attempt + 1}/{retry_count + 1} 次）：{reason}")
            logger.debug("截图失败的详细信息", exc_info=True)
            if expired:
                break
            if attempt < retry_count:
                if browser_session is not None and _browser_needs_restart(exc, browser_session.browser):
                    logger.warning("  浏览器失联或卡住，正在重启后重试")
                    try:
                        _remaining_ms(total_deadline)
                        browser_session.restart()
                        _remaining_ms(total_deadline)
                    except Exception as restart_exc:
                        last_error = (
                            f"单项总耗时已达 {total_seconds} 秒，已停止重试"
                            if time.monotonic() >= total_deadline
                            else f"浏览器重启失败：{restart_exc}"
                        )
                        break
                time.sleep(min(2 + attempt, 5, max(0, total_deadline - time.monotonic())))
    duration = round(time.monotonic() - start_time, 2)
    logger.error(f"截图失败 {tag}{item.get('name')}：{last_error.splitlines()[0] if last_error else '未执行'}，耗时 {duration} 秒")
    return CaptureResult(status="failed", error_message=last_error, duration_seconds=duration)


def _browser_needs_restart(exc: Exception, browser: Any) -> bool:
    try:
        if not browser.is_connected():
            return True
    except Exception:
        return True
    causes = []
    current: BaseException | None = exc
    while current is not None and len(causes) < 8:
        causes.append(current)
        current = current.__cause__
    detail = " ".join(str(cause).lower() for cause in causes)
    if any(signal in detail for signal in (
        "browser has been closed", "browser closed", "browser disconnected",
        "target page, context or browser has been closed", "browser process exited",
    )):
        return True
    timed_out = any(isinstance(cause, PlaywrightTimeoutError) for cause in causes)
    if not timed_out:
        return False
    if str(exc).startswith(("准备浏览器失败：", "登录失败：")):
        return True
    return str(exc).startswith("截图失败：") and not any(
        marker in detail for marker in ("grafana 面板仍为空白", "grafana 至少一个面板仍为空白")
    )


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


def _logical_window_position(scale_factor: float) -> tuple[int, int]:
    return round(20 / scale_factor), round(50 / scale_factor)


def chrome_launch_args(scale_factor: float, real_window: bool, width: int = 0, height: int = 0) -> list[str]:
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--lang=zh-CN",
        "--disable-translate",
        "--disable-features=Translate,TranslateUI",
        "--disable-save-password-bubble",
        "--enable-features=SidePanel,SharingHub,DesktopSharingHub",
    ]
    if real_window:
        logical_width, logical_height = _css_viewport_size(width, height, scale_factor)
        left, top = _logical_window_position(scale_factor)
        args.extend([
            f"--force-device-scale-factor={scale_factor}",
            "--high-dpi-support=1",
            "--use-gl=angle",
            "--use-angle=swiftshader",
            f"--window-position={left},{top}",
            f"--window-size={logical_width},{logical_height}",
        ])
    else:
        args.extend([f"--force-device-scale-factor={scale_factor}", "--high-dpi-support=1"])
    return args


def _remaining_ms(deadline: float) -> int:
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise TimeoutError("截图超时：已超过本项设置的加载超时时间")
    return remaining


def _bounded_timeout_ms(timeout_ms: int, total_deadline: float | None) -> int:
    return min(timeout_ms, _remaining_ms(total_deadline)) if total_deadline is not None else timeout_ms

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
            if page.get_by_text(pattern, exact=False).first.is_visible():
                return True
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


def _fill_login_form(
    page: Page,
    auth_profile: dict[str, Any],
    username: str,
    password: str,
    deadline: float | None = None,
    action_timeout_ms: int = 30_000,
) -> None:
    username_selector = _selector_with_fallback(auth_profile.get("username_selector"), DEFAULT_USERNAME_SELECTOR)
    password_selector = _selector_with_fallback(auth_profile.get("password_selector"), DEFAULT_PASSWORD_SELECTOR)
    submit_selector = _selector_with_fallback(auth_profile.get("submit_selector"), DEFAULT_SUBMIT_SELECTOR)

    user_input = page.locator(username_selector).first
    password_input = page.locator(password_selector).first
    user_input.wait_for(state="visible", timeout=_bounded_timeout_ms(15_000, deadline))
    password_input.wait_for(state="visible", timeout=_bounded_timeout_ms(15_000, deadline))
    user_input.fill(username, timeout=_bounded_timeout_ms(action_timeout_ms, deadline))
    password_input.fill(password, timeout=_bounded_timeout_ms(action_timeout_ms, deadline))
    page.locator(submit_selector).first.click(timeout=_bounded_timeout_ms(action_timeout_ms, deadline))


def _capture_once(
    item: dict[str, Any],
    job: dict[str, Any],
    run_id: int,
    suffix: str = "",
    browser: Any = None,
    total_deadline: float | None = None,
) -> CaptureResult:
    if total_deadline is not None:
        _remaining_ms(total_deadline)
    auth_profile = get_auth_profile(item.get("auth_profile_id"))
    target_width = int(item.get("browser_width") or job.get("browser_width") or 3840)
    target_height = int(item.get("browser_height") or job.get("browser_height") or 2160)
    is_real_capture = bool(item.get("real_browser_capture") if "real_browser_capture" in item else 0)
    width, height = browser_window_size(item, job, run_id) if is_real_capture else (target_width, target_height)
    scale_factor = _browser_scale_factor(job)
    timeout_ms = int(item.get("timeout_seconds") or 60) * 1000
    screenshot_dir = get_screenshot_dir(run_id)
    time_label = now_local().strftime("%H-%M-%S")
    stem = f"{time_label}_{safe_name(str(item.get('name') or 'screenshot'))}{suffix}"
    output_path = unique_artifact_path(screenshot_dir, stem, ".png")

    raw_url = str(item["url"])
    resolved_url = resolve_env_placeholders(raw_url)

    capture_mode = str(item.get("capture_mode") or "viewport")
    if is_real_capture and capture_mode == "full_page":
        logger.warning("  ├─ 视口配置: 启用真实浏览器截图时不支持 full_page，已自动降级为视口模式 (viewport)")

    if browser is None:
        with sync_playwright() as p:
            headless_val = False if is_real_capture else True
            launch_kwargs = {
                "headless": headless_val,
                "channel": "chrome",
                "ignore_default_args": ["--enable-automation", "--disable-sync"],
                "args": chrome_launch_args(scale_factor, is_real_capture, width, height),
            }
            tmp_browser = p.chromium.launch(**launch_kwargs)
            try:
                if total_deadline is not None:
                    _remaining_ms(total_deadline)
                return _capture_with_browser(
                    item, job, run_id, tmp_browser, width, height, timeout_ms,
                    screenshot_dir, output_path, resolved_url, is_real_capture, auth_profile,
                    total_deadline=total_deadline,
                    final_size=(target_width, target_height) if is_real_capture else None,
                )
            finally:
                tmp_browser.close()
    else:
        return _capture_with_browser(
            item, job, run_id, browser, width, height, timeout_ms,
            screenshot_dir, output_path, resolved_url, is_real_capture, auth_profile,
            total_deadline=total_deadline,
            final_size=(target_width, target_height) if is_real_capture else None,
        )


def _capture_with_browser(
    item: dict[str, Any],
    job: dict[str, Any],
    run_id: int,
    browser: Any,
    width: int,
    height: int,
    timeout_ms: int,
    screenshot_dir: Path,
    output_path: Path,
    resolved_url: str,
    is_real_capture: bool,
    auth_profile: dict[str, Any] | None,
    deadline: float | None = None,
    total_deadline: float | None = None,
    final_size: tuple[int, int] | None = None,
) -> CaptureResult:
    scale_factor = _browser_scale_factor(job)
    viewport_width, viewport_height = _css_viewport_size(width, height, scale_factor)
    logger.debug(f"目标地址：{resolved_url}")
    logger.debug(f"截图尺寸：{width}x{height}，网页视口：{viewport_width}x{viewport_height}，缩放：{scale_factor}")
    if total_deadline is not None:
        _remaining_ms(total_deadline)
    context = _new_context(browser, auth_profile, viewport_width, viewport_height, scale_factor)
    page = None
    temp_path = None
    monitor_data: dict[str, Any] | None = None
    content: dict[str, Any] = {"status": "not_applicable", "summary": ""}
    query_tracker: _GrafanaQueryTracker | None = None
    stage = "准备浏览器"
    try:
        page = context.new_page()
        page.set_default_timeout(_bounded_timeout_ms(timeout_ms, total_deadline))
        if is_real_capture:
            _browser_window_rect(page, viewport_width, viewport_height, scale_factor=scale_factor)
        stage = "登录"
        _ensure_logged_in(
            context, page, auth_profile,
            _bounded_timeout_ms(timeout_ms, total_deadline),
            deadline=total_deadline,
        )
        deadline = deadline if deadline is not None else time.monotonic() + timeout_ms / 1000
        if total_deadline is not None:
            deadline = min(deadline, total_deadline)
        page.set_default_timeout(_remaining_ms(deadline))
        if _is_grafana_dashboard(resolved_url):
            query_tracker = _GrafanaQueryTracker(page)

        stage = "打开页面"
        logger.info("  正在打开页面…")
        response = page.goto(resolved_url, wait_until="domcontentloaded", timeout=_remaining_ms(deadline))
        if response is not None and response.status >= 400:
            raise RuntimeError(f"页面返回 HTTP {response.status}")

        state_path = _storage_state_path(auth_profile)
        if state_path and state_path.exists() and _is_on_login_page(page, auth_profile):
            logger.warning("  登录状态已失效，正在重新登录")
            safe_delete_auth_state(auth_profile)
            context.clear_cookies()
            stage = "重新登录"
            _ensure_logged_in(
                context, page, auth_profile,
                _bounded_timeout_ms(timeout_ms, total_deadline),
                deadline=total_deadline,
            )
            deadline = time.monotonic() + timeout_ms / 1000
            if total_deadline is not None:
                deadline = min(deadline, total_deadline)
            page.set_default_timeout(_remaining_ms(deadline))
            stage = "重新打开页面"
            logger.info("  重新打开页面…")
            response = page.goto(resolved_url, wait_until="domcontentloaded", timeout=_remaining_ms(deadline))
            if response is not None and response.status >= 400:
                raise RuntimeError(f"页面返回 HTTP {response.status}")

        stage = "等待页面"
        _disable_grafana_auto_refresh(page, deadline)
        _assert_not_login_page_before_screenshot(page, auth_profile)
        waited_content = _wait_for_page(page, item, _remaining_ms(deadline), query_tracker)
        if isinstance(waited_content, dict):
            content = waited_content
        _assert_not_login_page_before_screenshot(page, auth_profile)
        _remaining_ms(deadline)
        if _is_grafana_dashboard(str(page.url)):
            content = _wait_for_grafana_panels(page, min(5_000, _remaining_ms(deadline)), query_tracker, settle_ms=0)
        elif str(item.get("item_type") or "").lower() == "kibana":
            content = _wait_for_kibana_panels(page, min(5_000, _remaining_ms(deadline)), settle_ms=0)
        if is_real_capture:
            _assert_real_window_viewport(page)
        fd, temp_name = tempfile.mkstemp(prefix=".capture-", suffix=".png", dir=screenshot_dir)
        os.close(fd)
        temp_path = Path(temp_name)
        stage = "截图"
        logger.info("  正在截图…")
        final_content = _take_screenshot(
            page, item, temp_path, is_real_capture, width, height, _remaining_ms(deadline),
            deadline=deadline, window_scale_factor=scale_factor if is_real_capture else 1.0,
            query_tracker=query_tracker,
        )
        if isinstance(final_content, dict):
            content = final_content
        _remaining_ms(deadline)
        stage = "处理图片"
        if is_real_capture:
            _normalize_window_image(temp_path, width, height)
        _apply_configured_watermark(temp_path, item)
        _apply_simulated_taskbar(temp_path, item, run_id)
        if final_size is not None:
            _assert_image_size(temp_path, final_size)
        _remaining_ms(deadline)
        _persist_storage_state(context, auth_profile)
        _remaining_ms(deadline)
        stage = "保存图片"
        os.replace(temp_path, output_path)
        temp_path = None
        logger.info(f"  截图已保存：{output_path.name}")
        if re.search(r"/d(?:-solo)?/", str(page.url)):
            from app.monitoring import collect_dashboard_alerts

            monitor_data = collect_dashboard_alerts(
                context, str(page.url), total_deadline,
                runtime_datasource_keys=query_tracker.datasource_keys if query_tracker else None,
            )
            logger.info("  数据源告警：%s（%s 条）", monitor_data["status"], len(monitor_data["alerts"]))
    except Exception as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"{stage}失败：{exc}") from exc
    finally:
        try:
            if page is not None:
                page.close()
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            logger.debug("关闭截图页面环境失败", exc_info=True)

    return CaptureResult(
        status="success", file_path=str(output_path), monitor_data=monitor_data,
        content_status=content["status"], content_summary=content["summary"],
    )


def _new_context(browser: Any, auth_profile: dict[str, Any] | None, width: int, height: int, scale_factor: float = 1.5) -> BrowserContext:
    kwargs: dict[str, Any] = {
        "viewport": {"width": width, "height": height},
        "ignore_https_errors": True,
        "locale": "zh-CN",
        "timezone_id": settings.default_timezone,
        "device_scale_factor": scale_factor,
    }
    state_path = _storage_state_path(auth_profile)
    if state_path and state_path.exists():
        logger.info(f"  ├─ 会话认证: 载入已缓存的登录状态 ({state_path.name})")
        try:
            import json
            encrypted_bytes = state_path.read_bytes()
            decrypted_str = _decrypt_state_data(encrypted_bytes)
            kwargs["storage_state"] = json.loads(decrypted_str)
            if encrypted_bytes.startswith(b"v1:"):
                try:
                    temporary_path = None
                    with tempfile.NamedTemporaryFile(dir=state_path.parent, prefix=state_path.name + ".",
                                                     suffix=".tmp", delete=False) as temporary_file:
                        temporary_path = Path(temporary_file.name)
                        temporary_file.write(_encrypt_state_data(decrypted_str))
                    os.replace(temporary_path, state_path)
                except Exception as exc:
                    logger.warning(f"  ├─ 会话认证: 旧状态缓存转换失败，将在下次读取时重试: {exc}")
                finally:
                    if temporary_path is not None:
                        try:
                            temporary_path.unlink(missing_ok=True)
                        except OSError:
                            pass
        except Exception as e:
            logger.error(f"  ├─ 会话认证: 解密或加载状态缓存失败，将重新登录: {e}")
    return browser.new_context(**kwargs)


def _storage_state_path(auth_profile: dict[str, Any] | None) -> Path | None:
    if not auth_profile:
        return None
    profile_id = auth_profile.get("id")
    if profile_id:
        raw_path = str(auth_profile.get("storage_state_path") or "").strip()
        suffix = safe_name(Path(raw_path).stem) if raw_path else "session"
        return settings.browser_state_dir / f"auth_{int(profile_id)}_{suffix}.json"
    return _legacy_storage_state_path(auth_profile)


def _legacy_storage_state_path(auth_profile: dict[str, Any] | None) -> Path | None:
    if not auth_profile:
        return None
    raw_path = str(auth_profile.get("storage_state_path") or "").strip()
    if raw_path:
        safe_filename = Path(raw_path).name
        if safe_filename:
            return (settings.browser_state_dir / safe_filename).resolve()
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

    # Match a login endpoint, not dashboard slugs such as /d/uid/login-overview.
    from urllib.parse import urlsplit
    path_segments = urlsplit(current_url).path.strip("/").split("/")
    if any(segment in {"login", "signin", "login.html", "login.htm", "login.php", "login.jsp"}
           for segment in path_segments):
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
    try:
        state_paths = {_storage_state_path(auth_profile), _legacy_storage_state_path(auth_profile)}
        for state_path in state_paths:
            if state_path is not None:
                _delete_one_auth_state(state_path)
    except Exception as exc:
        logger.error(f"清理浏览器状态缓存文件失败: {exc}", exc_info=True)


def _delete_one_auth_state(state_path: Path) -> None:
    from app.cleanup import is_safe_path
    try:
        if not state_path.exists():
            return
            
        if not state_path.is_file():
            logger.warning(f"安全拦截：浏览器状态缓存对象不是文件，跳过删除: {state_path}")
            return
            
        try:
            resolved_state = state_path.resolve()
            resolved_state_dir = settings.browser_state_dir.resolve()
            in_browser_state_dir = resolved_state_dir in resolved_state.parents or resolved_state == resolved_state_dir
        except Exception:
            in_browser_state_dir = False
            
        in_data_dir = is_safe_path(state_path)
        
        if in_browser_state_dir or in_data_dir:
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
    deadline: float | None = None,
) -> None:
    if not auth_profile or auth_profile.get("auth_type") in {"", "none"}:
        return
    if auth_profile.get("auth_type") == "state":
        logger.info("  ├─ 会话认证: 认证类型为 state，信任本地持久化状态")
        return
    if auth_profile.get("auth_type") != "form":
        return
    state_path = _storage_state_path(auth_profile)
    if state_path and state_path.exists():
        logger.info(f"  ├─ 会话认证: 检测到本地会话凭证，已复用无需重新登录")
        return

    raw_login_url = str(auth_profile.get("login_url") or "").strip()
    login_url = resolve_env_placeholders(raw_login_url)
    
    creds = resolve_auth_credential(auth_profile)
    username = creds["username"]
    password = creds["password"]
        
    if not login_url:
        logger.info("  ├─ 会话认证: 缺少 login_url，跳过表单登录")
        return
    if not username or not password:
        raise RuntimeError(f"登录配置 {auth_profile['name']} 缺少用户名或密码配置，请在网页端核对配置")

    logger.info(f"  ├─ 会话认证: 正在执行表单登录 (地址: {login_url}, 用户: {username})")
    page.goto(login_url, wait_until="domcontentloaded", timeout=_bounded_timeout_ms(timeout_ms, deadline))
    _assert_login_page_ready(page, login_url)
    _fill_login_form(page, auth_profile, username, password, deadline=deadline, action_timeout_ms=timeout_ms)
    
    success_selector = str(auth_profile.get("success_selector") or "").strip()
    _wait_for_login_success(page, success_selector, _bounded_timeout_ms(timeout_ms, deadline))
    if deadline is not None:
        _remaining_ms(deadline)
    _persist_storage_state(context, auth_profile)
    logger.info("  ├─ 会话认证: ✔ 登录成功，最新会话已持久化加密保存")


def _persist_storage_state(context: BrowserContext, auth_profile: dict[str, Any] | None) -> None:
    state_path = _storage_state_path(auth_profile)
    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import json
        state_dict = context.storage_state()
        state_str = json.dumps(state_dict)
        encrypted_bytes = _encrypt_state_data(state_str)
        state_path.write_bytes(encrypted_bytes)
    except Exception as exc:
        logger.error(f"加密保存浏览器状态缓存失败: {exc}", exc_info=True)


def _disable_grafana_auto_refresh(page: Page, deadline: float) -> None:
    if not re.search(r"/d(?:-solo)?/", str(page.url)):
        return
    # Grafana versions and languages expose different refresh controls. Pausing it
    # is only a best-effort improvement, never a prerequisite for a screenshot.
    pause_deadline = min(deadline, time.monotonic() + 2)

    def short_timeout() -> int:
        remaining = int((pause_deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise TimeoutError("暂停自动刷新的尝试已超时")
        return min(750, remaining)

    button = page.locator('button[aria-label*="Choose refresh time interval"]').first
    menu_opened = False
    try:
        button.wait_for(state="visible", timeout=short_timeout())
        label = button.get_attribute("aria-label", timeout=short_timeout()) or ""
        if "Auto refresh turned off" in label:
            return
        button.evaluate("element => element.click()", timeout=short_timeout())
        menu_opened = True
        off = page.get_by_role("menu").get_by_text("Off", exact=True).first
        off.wait_for(state="visible", timeout=short_timeout())
        off.evaluate("element => element.click()", timeout=short_timeout())
        page.wait_for_function(
            "() => !!document.querySelector('button[aria-label*=\"Auto refresh turned off\"]')",
            timeout=short_timeout(),
        )
    except Exception as exc:
        if menu_opened:
            try:
                page.keyboard.press("Escape", timeout=500)
            except Exception:
                logger.debug("收起 Grafana 刷新菜单失败", exc_info=True)
        logger.warning("  Grafana 自动刷新未能暂停，继续等待面板并截图：%s", str(exc).splitlines()[0])
        return
    logger.info("  Grafana 自动刷新已暂停，正在等待面板内容")


def _is_grafana_dashboard(url: str) -> bool:
    return bool(re.search(r"/d(?:-solo)?/", urlsplit(url).path))


class _GrafanaQueryTracker:
    """Observe this page's Grafana queries without waiting for unrelated live traffic."""

    def __init__(self, page: Page) -> None:
        self.pending: set[int] = set()
        self.responses_by_request: dict[int, Any] = {}
        self.responses: list[Any] = []
        self.errors: list[str] = []
        self.datasource_keys: set[str] = set()
        self.seen = 0
        self.completed_cycle = False
        page.on("request", self._started)
        page.on("response", self._responded)
        page.on("requestfinished", self._finished)
        page.on("requestfailed", self._failed)

    @staticmethod
    def _is_query(url: str) -> bool:
        path = urlsplit(url).path.lower()
        return "/api/ds/query" in path or (
            "/api/datasources/proxy/" in path and
            any(part in path for part in ("/api/v1/query", "/_search", "/_msearch"))
        )

    def _started(self, request: Any) -> None:
        if self._is_query(request.url):
            self.seen += 1
            self.pending.add(id(request))
            proxy = re.search(r"/api/datasources/proxy/(?:uid/)?([^/]+)", urlsplit(request.url).path, re.IGNORECASE)
            if proxy:
                self.datasource_keys.add(unquote(proxy.group(1)))
            if "/api/ds/query" in urlsplit(request.url).path.lower():
                try:
                    payload = request.post_data_json
                    if isinstance(payload, dict):
                        for query in payload.get("queries", []):
                            if not isinstance(query, dict):
                                continue
                            source = query.get("datasource")
                            key = (source.get("uid") or source.get("id")) if isinstance(source, dict) else source
                            key = key or query.get("datasourceUid") or query.get("datasourceId")
                            if isinstance(key, (str, int)) and str(key):
                                self.datasource_keys.add(str(key))
                except Exception:
                    logger.debug("无法解析 Grafana 查询使用的数据源", exc_info=True)

    def _responded(self, response: Any) -> None:
        if self._is_query(response.url):
            if response.status >= 400:
                self.errors.append(f"Grafana 数据查询返回 HTTP {response.status}")
            else:
                self.responses_by_request[id(response.request)] = response

    def _finished(self, request: Any) -> None:
        self.pending.discard(id(request))
        response = self.responses_by_request.pop(id(request), None)
        if response is not None:
            self.responses.append(response)

    def _failed(self, request: Any) -> None:
        if id(request) in self.pending:
            self.pending.discard(id(request))
            self.responses_by_request.pop(id(request), None)
            reason = str(request.failure or "请求失败")
            if "aborted" not in reason.lower():
                self.errors.append(f"Grafana 数据查询失败：{reason[:120]}")

    def check(self) -> None:
        if self.errors:
            raise RuntimeError(self.errors[0])
        while self.responses:
            response = self.responses.pop(0)
            try:
                payload = response.json()
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("error"):
                raise RuntimeError(f"Grafana 数据查询失败：{str(payload['error'])[:160]}")
            if payload.get("status") == "error":
                raise RuntimeError("Grafana 数据查询返回错误状态")
            results = payload.get("results")
            if isinstance(results, dict):
                for result in results.values():
                    if not isinstance(result, dict):
                        continue
                    status = result.get("status")
                    is_http_error = (isinstance(status, int) and status >= 400) or (
                        isinstance(status, str) and status.lower() in {"error", "failed"}
                    )
                    if result.get("error") or is_http_error:
                        detail = result.get("error") or f"HTTP {status}"
                        raise RuntimeError(f"Grafana 数据查询失败：{str(detail)[:160]}")
        if self.seen and not self.pending:
            self.completed_cycle = True


_GRAFANA_PANEL_SNAPSHOT = r"""() => {
  const isRowItem = (item) => {
    return item.matches('.dashboard-row, [class*="dashboard-row"]') ||
      !!item.querySelector('.dashboard-row, [class*="dashboard-row"], [data-testid*="dashboard-row"], [aria-label*="Toggle row"]');
  };
  const gridItems = [...document.querySelectorAll('.react-grid-item')];
  const candidates = gridItems.length
    ? gridItems.filter(item => {
        if (isRowItem(item)) return false;
        return item.matches('.panel-container, [class*="panel-container"], [data-testid="panel-container"]') ||
          !!item.querySelector('.panel-container, [class*="panel-container"], [data-testid="panel-container"]');
      })
    : [...document.querySelectorAll('.panel-container, [class*="panel-container"], [data-testid="panel-container"]')];
  const visible = candidates.filter(panel => {
    const rect = panel.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 &&
      rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
  });
  const panels = visible.map((panel, index) => {
    const container = panel.matches('.panel-container, [class*="panel-container"], [data-testid="panel-container"]')
      ? panel : panel.querySelector('.panel-container, [class*="panel-container"], [data-testid="panel-container"]');
    const title = (container?.querySelector('.panel-title, [class*="panel-title"], .panel-header, [class*="panel-header"], [class*="panelHeader"]')?.innerText ||
      container?.getAttribute('data-testid')?.replace(/^data-testid Panel header\s*/, '') ||
      `面板 ${index + 1}`).trim().slice(0, 80);
    if (!container) return {title, state: 'loading'};
    const content = container.querySelector(
      '.panel-content, [class*="panel-content"], [data-testid="panel-content"], [data-testid="data-testid panel content"], [data-testid="panel-body"], [class*="panelContent"]'
    );
    const nodes = content ? [content] : [...container.children].filter(node =>
      !node.matches('.panel-header, .panel-info-corner, [class*="panelHeader"], [class*="panel-header"], [class*="panel-loading"], [class*="resize-handle"]')
    );
    const bodyText = nodes.map(node => node.innerText || '').join(' ').trim();
    const loading = /Loading plugin panel|Loading panel|Loading data|加载中/i.test(bodyText) ||
      nodes.some(node => !!node.querySelector('[role="progressbar"], [aria-busy="true"], .panel-loading'));
    if (loading || !nodes.length) return {title, state: 'loading'};
    const errorNode = nodes.some(node => !!node.querySelector(
      '.panel-error, [data-testid="panel-error"], .query-error, [class*="panelError"]'
    ));
    const errorText = /(?:^|\n)\s*(?:Query error|Data source error|Datasource error|Failed to fetch|Internal Server Error|Bad Gateway|查询失败|数据源错误|加载失败)(?:\b|\s|:|：|$)/i.test(bodyText);
    if (errorNode || errorText) return {title, state: 'error', detail: bodyText.slice(0, 160)};
    const noData = /^(?:No data|暂无数据|无数据)(?:\s|$)/i.test(bodyText);
    if (noData) return {title, state: 'no_data'};
    const visual = 'canvas, svg, img, video, iframe, table, [role="table"], [role="img"], [role="grid"]';
    const hasVisual = nodes.some(node => node.matches(visual) || !!node.querySelector(visual));
    return {title, state: hasVisual || !!bodyText ? 'data' : 'loading'};
  });
  return {panels};
}"""


def _grafana_snapshot(page: Page) -> dict[str, Any]:
    return page.evaluate(_GRAFANA_PANEL_SNAPSHOT)


_KIBANA_PANEL_SNAPSHOT = r"""() => {
  const visible = node => {
    const r = node.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 &&
      r.top < innerHeight && r.left < innerWidth;
  };
  const selector = '[data-test-subj="dashboardPanel"], [data-test-subj="embeddablePanel"], [data-test-subj="embeddablePanelWrapper"]';
  const panels = [...document.querySelectorAll(selector)].filter(node =>
    visible(node) && !node.parentElement?.closest(selector));
  return {panels: panels.map((panel, index) => {
    const title = (panel.querySelector('[data-test-subj="embeddablePanelHeading"], [data-test-subj="dashboardPanelTitle"], .embPanel__title')?.textContent ||
      `面板 ${index + 1}`).trim().slice(0, 80);
    const body = panel.querySelector('[data-test-subj="embeddablePanelContent"], [data-test-subj="dashboardPanelContent"], .embPanel__content');
    if (!body) return {title, state: 'loading'};
    const text = (body.innerText || '').trim();
    if (body.querySelector('[data-test-subj="embeddablePanelError"], [data-test-subj="dashboardPanelError"], .euiCallOut--danger') ||
        /(?:^|\n)\s*(?:Error loading|Unable to load|Query error|查询失败|加载失败)(?:\b|\s|:|：|$)/i.test(text))
      return {title, state: 'error', detail: text.slice(0, 160)};
    if (body.querySelector('[data-test-subj="loadingIndicator"], [data-test-subj="embeddablePanelLoading"], [role="progressbar"], [aria-busy="true"], .euiLoadingSpinner'))
      return {title, state: 'loading'};
    if (/^(?:No results found|No data|暂无数据|无数据)(?:\s|$)/i.test(text))
      return {title, state: 'no_data'};
    const visual = 'canvas, svg, img, table, [role="table"], [role="img"], [data-test-subj="visualizationLoader"]';
    return {title, state: body.querySelector(visual) || text ? 'data' : 'loading'};
  })};
}"""


def _wait_for_kibana_panels(page: Page, timeout_ms: int, settle_ms: int = 1000) -> dict[str, Any]:
    logger.info("  等待 Kibana 图表完成加载…")
    deadline = time.monotonic() + timeout_ms / 1000
    ready_since: float | None = None
    last_panels: list[dict[str, str]] = []
    while True:
        snapshot = page.evaluate(_KIBANA_PANEL_SNAPSHOT)
        panels = snapshot.get("panels") or []
        last_panels = panels
        errors = [panel for panel in panels if panel.get("state") == "error"]
        if errors:
            panel = errors[0]
            raise RuntimeError(f"Kibana 图表“{panel['title']}”查询错误：{panel.get('detail') or '请检查数据源'}")
        now = time.monotonic()
        if panels and all(panel.get("state") in {"data", "no_data"} for panel in panels):
            ready_since = ready_since or now
            if (now - ready_since) * 1000 >= settle_ms:
                no_data = [panel["title"] for panel in panels if panel["state"] == "no_data"]
                status = "no_data" if len(no_data) == len(panels) else "partial_no_data" if no_data else "data"
                summary = (f"全部 {len(panels)} 个图表无数据，需确认" if status == "no_data" else
                           f"{len(no_data)} 个图表无数据：{'、'.join(no_data[:5])}" if no_data else
                           f"{len(panels)} 个图表已显示")
                logger.info("  Kibana 图表已加载：%s", summary)
                return {"status": status, "summary": summary}
        else:
            ready_since = None
        remaining = deadline - now
        if remaining <= 0:
            unfinished = [panel["title"] for panel in last_panels if panel.get("state") not in {"data", "no_data"}]
            raise TimeoutError(f"Kibana 图表未完成加载：{'、'.join(unfinished[:5]) or '未找到可见图表'}")
        page.wait_for_timeout(min(300, max(1, int(remaining * 1000))))


def _wait_for_grafana_panels(
    page: Page, timeout_ms: int, tracker: _GrafanaQueryTracker | None = None,
    settle_ms: int = 2000,
) -> dict[str, Any]:
    if not _is_grafana_dashboard(str(page.url)):
        return {"status": "not_applicable", "summary": ""}
    logger.info("  等待 Grafana 图表完成加载…")
    deadline = time.monotonic() + timeout_ms / 1000
    ready_since: float | None = None
    last_panels: list[dict[str, str]] = []
    while True:
        if tracker is not None:
            tracker.check()
        snapshot = _grafana_snapshot(page)
        panels = snapshot.get("panels") or []
        last_panels = panels
        errors = [p for p in panels if p.get("state") == "error"]
        if errors:
            panel = errors[0]
            raise RuntimeError(f"Grafana 图表“{panel['title']}”查询错误：{panel.get('detail') or '请检查数据源'}")
        pending_first_cycle = tracker is not None and bool(tracker.pending) and not tracker.completed_cycle
        ready = bool(panels) and not pending_first_cycle and all(p.get("state") in {"data", "no_data"} for p in panels)
        now = time.monotonic()
        if ready:
            ready_since = ready_since or now
            if (now - ready_since) * 1000 >= settle_ms:
                no_data = [p["title"] for p in panels if p["state"] == "no_data"]
                status = "no_data" if len(no_data) == len(panels) else "partial_no_data" if no_data else "data"
                summary = (
                    f"全部 {len(panels)} 个图表无数据，需确认" if status == "no_data" else
                    f"{len(no_data)} 个图表无数据：{'、'.join(no_data[:5])}" if no_data else
                    f"{len(panels)} 个图表已显示"
                )
                logger.info("  Grafana 图表已加载：%s", summary)
                return {"status": status, "summary": summary}
        else:
            ready_since = None
        remaining = deadline - now
        if remaining <= 0:
            unfinished = [p["title"] for p in last_panels if p.get("state") not in {"data", "no_data"}]
            detail = "、".join(unfinished[:5]) or ("数据查询仍在进行" if pending_first_cycle else "未找到可见图表")
            raise TimeoutError(f"Grafana 图表未完成加载：{detail}")
        page.wait_for_timeout(min(300, max(1, int(remaining * 1000))))


def _wait_for_page(
    page: Page, item: dict[str, Any], timeout_ms: int,
    tracker: _GrafanaQueryTracker | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000
    wait_selector = str(item.get("wait_selector") or "").strip()
    if wait_selector:
        logger.info(f"  等待元素：{wait_selector}")
        page.wait_for_selector(wait_selector, state="visible", timeout=_remaining_ms(deadline))
    content = (_wait_for_kibana_panels(page, _remaining_ms(deadline))
               if str(item.get("item_type") or "").lower() == "kibana"
               else _wait_for_grafana_panels(page, _remaining_ms(deadline), tracker))
    wait_seconds = float(item.get("wait_seconds") or 0)
    if wait_seconds > 0:
        if wait_seconds * 1000 > _remaining_ms(deadline):
            raise TimeoutError("截图超时：手动等待时间超过本项剩余超时时间")
        logger.info(f"  等待 {wait_seconds:g} 秒后截图")
        page.wait_for_timeout(int(wait_seconds * 1000))
    return content


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
    options = _configured_watermark_options(item)
    if options:
        _apply_watermark(image_path=image_path, **options)


def _configured_watermark_options(item: dict[str, Any]) -> dict[str, Any] | None:
    if not _watermark_bool(item.get("watermark_enabled"), False):
        return None

    custom_text = str(item.get("watermark_text") or "").strip()
    parts = []
    time_line_index = None
    if custom_text:
        parts.append(custom_text)
    if _watermark_bool(item.get("watermark_time_enabled"), False):
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(settings.default_timezone))
        except Exception:
            now = datetime.now()
        time_line_index = len("\n".join(parts).splitlines()) if parts else 0
        parts.append(format_watermark_time(now, str(item.get("watermark_time_format") or DEFAULT_TIME_FORMAT)))
    if not parts:
        return None

    return {
        "text": "\n".join(parts),
        "opacity": _watermark_int(item.get("watermark_opacity"), DEFAULT_WATERMARK_OPACITY, 0, 255),
        "font_size": _watermark_int(item.get("watermark_font_size"), DEFAULT_WATERMARK_FONT_SIZE, 10, 72),
        "gap_x": _watermark_int(item.get("watermark_gap_x"), DEFAULT_WATERMARK_TILE_GAP_X, 20, 800),
        "gap_y": _watermark_int(item.get("watermark_gap_y"), DEFAULT_WATERMARK_TILE_GAP_Y, 20, 800),
        "angle": _watermark_int(item.get("watermark_angle"), DEFAULT_WATERMARK_ANGLE, -90, 90),
        "alignment": validate_watermark_alignment(str(item.get("watermark_alignment") or WATERMARK_DEFAULTS["watermark_alignment"])),
        "time_line_index": time_line_index,
    }


def _taskbar_template_paths() -> list[Path]:
    configured = os.getenv("TASKBAR_TEMPLATE_PATH", "").strip()
    if configured:
        return [Path(configured).resolve()]
    return [(TASKBAR_TEMPLATE_DIR / name).resolve() for name in TASKBAR_TEMPLATE_NAMES if (TASKBAR_TEMPLATE_DIR / name).exists()]


def _taskbar_template_path(run_id: int | str | None = None, key: str = "") -> Path:
    if key:
        from app.taskbar_templates import selected_path
        return selected_path(key)
    paths = _taskbar_template_paths()
    if not paths:
        return DEFAULT_TASKBAR_TEMPLATE_PATH.resolve()
    if run_id is None:
        return random.choice(paths)
    return random.Random(str(run_id)).choice(paths)


def browser_window_size(item: dict[str, Any], job: dict[str, Any], run_id: int | str) -> tuple[int, int]:
    """Reserve space for the taskbar within the requested final screenshot size."""
    from PIL import Image

    width = int(item.get("browser_width") or job.get("browser_width") or 3840)
    height = int(item.get("browser_height") or job.get("browser_height") or 2160)
    if not _watermark_bool(item.get("taskbar_enabled"), True):
        return width, height
    template_path = _taskbar_template_path(run_id, str(item.get("taskbar_template_key") or ""))
    if not template_path.exists():
        raise FileNotFoundError(f"Windows 任务栏模板不存在：{template_path}")
    with Image.open(template_path) as template:
        taskbar_height = max(1, round(template.height * width / template.width))
    if taskbar_height >= height:
        raise ValueError(f"截图高度 {height}px 不足以容纳 {taskbar_height}px 任务栏")
    return width, height - taskbar_height


def _normalize_window_image(image_path: Path, width: int, height: int) -> None:
    """Correct the one-pixel X11 rounding difference without scaling the browser UI."""
    from PIL import Image

    with Image.open(image_path) as source:
        if source.size == (width, height):
            return
        if abs(source.width - width) > 8 or abs(source.height - height) > 8:
            raise RuntimeError(
                f"浏览器窗口图片尺寸 {source.width}x{source.height} 与预期 {width}x{height} 不一致"
            )
        original = source.convert("RGB")
        original.load()
    background = original.getpixel((original.width - 1, original.height - 1))
    normalized = Image.new("RGB", (width, height), background)
    normalized.paste(original.crop((0, 0, min(width, original.width), min(height, original.height))))
    normalized.save(image_path)


def _assert_image_size(image_path: Path, expected: tuple[int, int]) -> None:
    from PIL import Image

    with Image.open(image_path) as image:
        if image.size != expected:
            raise RuntimeError(
                f"最终截图尺寸 {image.width}x{image.height} 与配置 {expected[0]}x{expected[1]} 不一致"
            )


def _format_taskbar_clock(now: datetime) -> tuple[str, str]:
    return now.strftime("%H:%M"), now.strftime("%Y/%m/%d")


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
    right = width - max(20, round(height * 0.28))
    gap = max(3, round(height * 0.11))
    time_bbox = draw.textbbox((0, 0), time_str, font=font)
    date_bbox = draw.textbbox((0, 0), date_str, font=font)
    time_h = time_bbox[3] - time_bbox[1]
    date_h = date_bbox[3] - date_bbox[1]
    top = max(0, round((height - time_h - gap - date_h) / 2) + max(2, round(height * 0.08)))

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

    logger.info("  ├─ 视觉增强: ✔ 已合成真实的 Windows 任务栏底栏")


def _apply_simulated_taskbar(image_path: Path, item: dict[str, Any], run_id: int | str | None = None) -> None:
    if not _watermark_bool(item.get("taskbar_enabled"), True):
        return

    try:
        from PIL import Image  # noqa: F401
    except ImportError as err:
        raise RuntimeError("Taskbar template composition failed: Pillow is not installed") from err

    if not image_path.exists():
        raise FileNotFoundError(f"Taskbar template composition failed: screenshot file not found {image_path}")

    template_path = _taskbar_template_path(run_id, str(item.get("taskbar_template_key") or ""))
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
        alignment=WATERMARK_DEFAULTS["watermark_alignment"],
        time_line_index=1,
    )


def render_watermark_preview_png(item: dict[str, Any], image_width: int, image_height: int) -> bytes:
    """Render a transparent preview with the screenshot watermark renderer."""
    from PIL import Image

    preview_scale = min(1.0, 1200 / image_width, 1600 / image_height)
    preview_width = max(1, round(image_width * preview_scale))
    preview_height = max(1, round(image_height * preview_scale))
    render_native = image_width * image_height <= 10_000_000
    render_size = (image_width, image_height) if render_native else (preview_width, preview_height)
    preview = Image.new("RGBA", render_size, (0, 0, 0, 0))
    options = _configured_watermark_options(item)
    if options:
        preview = _render_watermark(
            preview, **options,
            geometry_size=None if render_native else (image_width, image_height),
        )
    if render_native and preview_scale < 1:
        preview = preview.resize((preview_width, preview_height), Image.Resampling.LANCZOS)
    output = BytesIO()
    preview.save(output, format="PNG")
    return output.getvalue()


def _apply_watermark(
    image_path: Path,
    text: str,
    opacity: int,
    font_size: int,
    gap_x: int,
    gap_y: int,
    angle: int,
    alignment: str = "center",
    time_line_index: int | None = None,
) -> None:
    try:
        from PIL import Image
    except ImportError as err:
        raise RuntimeError("截图水印处理失败：未安装 Pillow，请先安装 requirements.txt 中的 Pillow 依赖") from err

    if not image_path.exists():
        raise FileNotFoundError(f"截图水印处理失败：图片文件不存在 {image_path}")

    with Image.open(image_path) as orig_img:
        img_rgba = orig_img.convert("RGBA")
        img_rgba.load()
    combined = _render_watermark(img_rgba, text, opacity, font_size, gap_x, gap_y, angle, alignment, time_line_index=time_line_index)
    if image_path.suffix.lower() in {".jpg", ".jpeg"}:
        combined.convert("RGB").save(image_path)
    else:
        combined.save(image_path)

    logger.info("  ├─ 视觉增强: ✔ 已为截图添加配置的水印")


# Measured from the four lossless reference captures. The interpolation keeps
# width and height independent: two captures have the same width but different
# heights, while another pair has almost the same height but different widths.
_REFERENCE_GEOMETRY = {
    (3840, 2088): {"angle": 36.19, "name_height": 60, "name_width": 69, "date_width": 652,
                   "date_dx": 22.5, "date_dy": 34.5, "name_dx": -38.5, "name_dy": -75.5},
    (1898, 2058): {"angle": 42.27, "name_height": 48, "name_width": 55, "date_width": 474,
                   "date_dx": 18.25, "date_dy": 24, "name_dx": -36, "name_dy": -55},
    (2489, 1470): {"angle": 37.20, "name_height": 39, "name_width": 45, "date_width": 429,
                   "date_dx": 13.6, "date_dy": 22, "name_dx": -25, "name_dy": -49},
    (2489, 2043): {"angle": 41.21, "name_height": 50, "name_width": 57, "date_width": 540,
                   "date_dx": 19.6, "date_dy": 25.75, "name_dx": -36.5, "name_dy": -59},
}


def _reference_metric(width: int, height: int, key: str) -> float:
    """Interpolate measured watermark geometry without treating a screenshot as a fixed bitmap."""
    if not (1898 <= width <= 3840 and 1470 <= height <= 2088):
        anchor_width = max(1898, min(width, 3840))
        anchor_height = max(1470, min(height, 2088))
        anchor_value = _reference_metric(anchor_width, anchor_height, key)
        if key == "angle":
            return anchor_value
        return anchor_value * min(width / anchor_width, height / anchor_height)

    short = _REFERENCE_GEOMETRY[(2489, 1470)][key]
    tall = _REFERENCE_GEOMETRY[(2489, 2043)][key]
    height_slope = (tall - short) / (2043 - 1470)
    value = short + (height - 1470) * height_slope
    if width < 2489:
        left = _REFERENCE_GEOMETRY[(1898, 2058)][key]
        value += (width - 2489) * (left - short - (2058 - 1470) * height_slope) / (1898 - 2489)
    elif width > 2489:
        right = _REFERENCE_GEOMETRY[(3840, 2088)][key]
        value += (width - 2489) * (right - short - (2088 - 1470) * height_slope) / (3840 - 2489)
    return value


def _render_reference_watermark(
    img_rgba: Any, name: str, date_text: str, opacity: int, font_size: int,
    gap_x: int, gap_y: int, angle: int, name_font: Any, date_font: Any,
    geometry_size: tuple[int, int] | None = None,
) -> Any:
    from PIL import Image, ImageDraw, ImageFont

    width, height = img_rgba.size
    geometry_width, geometry_height = geometry_size or (width, height)
    geometry_scale = min(width / geometry_width, height / geometry_height)
    size_scale = font_size / WATERMARK_DEFAULTS["watermark_font_size"]
    measured_angle = _reference_metric(geometry_width, geometry_height, "angle") + angle - WATERMARK_DEFAULTS["watermark_angle"]
    measured_angle = max(-90.0, min(90.0, measured_angle))
    radians = math.radians(measured_angle)
    along_x, along_y = math.cos(radians), -math.sin(radians)

    measure = ImageDraw.Draw(Image.new("L", (1, 1)))

    def fitted_font(base_font: Any, sample: str, target: float, axis: str) -> Any:
        if not hasattr(base_font, "font_variant"):
            return base_font
        initial = measure.textbbox((0, 0), sample, font=base_font)
        current = (initial[2] - initial[0]) if axis == "width" else (initial[3] - initial[1])
        guess = max(1, round(base_font.size * target / max(1, current)))
        best = base_font
        best_error = float("inf")
        for size in range(max(1, guess - 4), guess + 5):
            candidate = base_font.font_variant(size=size)
            box = measure.textbbox((0, 0), sample, font=candidate)
            value = (box[2] - box[0]) if axis == "width" else (box[3] - box[1])
            error = abs(value - target)
            if error < best_error:
                best, best_error = candidate, error
        return best

    if any(ord(character) > 127 for character in date_text):
        for path_str in (
            r"C:\Windows\Fonts\msyhbd.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        ):
            if Path(path_str).exists():
                date_font = ImageFont.truetype(path_str, date_font.size)
                break

    name_font = fitted_font(name_font, name, max(1, _reference_metric(geometry_width, geometry_height, "name_height") * size_scale), "height")
    date_font = fitted_font(date_font, "2026-10-26 08:48:13", max(1, _reference_metric(geometry_width, geometry_height, "date_width") * size_scale), "width")

    def glyph(value: str, selected_font: Any) -> Any:
        box = measure.textbbox((0, 0), value, font=selected_font)
        padding = 5
        image = Image.new("RGBA", (box[2] - box[0] + padding * 2, box[3] - box[1] + padding * 2))
        draw = ImageDraw.Draw(image)
        draw.text((padding - box[0], padding - box[1]), value, font=selected_font,
                  fill=(155, 155, 155, max(0, min(255, opacity))))
        rotated = image.rotate(measured_angle, expand=True, resample=Image.Resampling.BILINEAR)
        return rotated.crop(rotated.getchannel("A").getbbox())

    name_image = glyph(name, name_font)
    date_image = glyph(date_text, date_font)
    layer = Image.new("RGBA", (width, height))

    def place(image: Any, center_x: float, center_y: float) -> None:
        x = round(center_x - image.width / 2)
        y = round(center_y - image.height / 2)
        left, top = max(0, x), max(0, y)
        right, bottom = min(width, x + image.width), min(height, y + image.height)
        if right > left and bottom > top:
            part = image.crop((left - x, top - y, right - x, bottom - y))
            layer.alpha_composite(part, (left, top))

    pitch = geometry_width / 4 * geometry_scale + gap_x - WATERMARK_DEFAULTS["watermark_gap_x"] * geometry_scale
    row_distance = geometry_height / 2 * geometry_scale + gap_y - WATERMARK_DEFAULTS["watermark_gap_y"] * geometry_scale
    name_width = max(1, _reference_metric(geometry_width, geometry_height, "name_width") * size_scale)
    date_width = max(1, _reference_metric(geometry_width, geometry_height, "date_width") * size_scale)
    name_shift = max(0, width / 8 - 0.7 * name_width)
    date_shift = name_shift - (date_width - name_width) / 2
    date_dx = _reference_metric(geometry_width, geometry_height, "date_dx") * size_scale
    date_dy = _reference_metric(geometry_width, geometry_height, "date_dy") * size_scale
    name_dx = _reference_metric(geometry_width, geometry_height, "name_dx") * size_scale
    name_dy = _reference_metric(geometry_width, geometry_height, "name_dy") * size_scale

    for column, direction in enumerate((-1, 0, 1, 1)):
        date_x = geometry_width / 8 * geometry_scale + column * pitch + date_dx
        date_y = geometry_height * 0.75 * geometry_scale + date_dy
        name_x = date_x + name_dx
        name_y = date_y + name_dy
        place(date_image, date_x, date_y)
        place(name_image, name_x, name_y)
        place(date_image, date_x + direction * date_shift * along_x,
              date_y - row_distance + direction * date_shift * along_y)
        place(name_image, name_x + direction * name_shift * along_x,
              name_y - row_distance + direction * name_shift * along_y)

    return Image.alpha_composite(img_rgba, layer)


def _render_watermark(
    img_rgba: Any,
    text: str,
    opacity: int,
    font_size: int,
    gap_x: int,
    gap_y: int,
    angle: int,
    alignment: str,
    scale: float = 1.0,
    time_line_index: int | None = None,
    geometry_size: tuple[int, int] | None = None,
) -> Any:
    from PIL import Image, ImageDraw, ImageFont

    width, height = img_rgba.size
    if geometry_size is not None:
        scale *= min(width / geometry_size[0], height / geometry_size[1])

    # Screenshot settings are physical pixels. The preview is rendered at the
    # capture size first and only the completed layer is scaled for display.
    font_size = max(1, round(font_size * scale))
    gap_x = round(gap_x * scale)
    gap_y = round(gap_y * scale)
    padding = round(DEFAULT_WATERMARK_TILE_PADDING * scale)
    line_spacing = round(DEFAULT_WATERMARK_TEXT_SPACING * scale)
    alignment = validate_watermark_alignment(alignment)

    font_paths = [
        r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\dengl.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\deng.ttf",
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

    time_font = None
    for path_str in (
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ):
        font_path = Path(path_str)
        if font_path.exists():
            try:
                time_font = ImageFont.truetype(str(font_path), max(1, round(font_size * 1.2)))
                break
            except Exception:
                continue
    time_font_needs_stroke = time_font is None
    if time_font is None:
        time_font = font

    if alignment == "reference" and time_line_index == 1 and len(text.splitlines()) == 2:
        name, date_text = text.splitlines()
        return _render_reference_watermark(
            img_rgba, name, date_text, opacity, font_size, gap_x, gap_y, angle, font, time_font,
            geometry_size=geometry_size,
        )
    if alignment == "reference":
        alignment = "center"

    temp_img = Image.new("RGBA", (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    lines = text.splitlines()
    fonts = [time_font if index == time_line_index else font for index in range(len(lines))]
    boxes = [temp_draw.textbbox((0, 0), line, font=line_font) for line, line_font in zip(lines, fonts)]
    line_widths = [box[2] - box[0] for box in boxes]
    txt_w = max(line_widths)
    line_advance = font_size + line_spacing
    txt_h = (len(lines) - 1) * line_advance + max(box[3] - box[1] for box in boxes)
    txt_img_w = txt_w + padding * 2 + 1
    txt_img_h = txt_h + padding * 2 + 1
    txt_img = Image.new("RGBA", (txt_img_w, txt_img_h), (0, 0, 0, 0))
    draw_txt = ImageDraw.Draw(txt_img)
    for index, (line, box, line_width, line_font) in enumerate(zip(lines, boxes, line_widths, fonts)):
        offset = {"left": 0, "center": (txt_w - line_width) / 2, "right": txt_w - line_width}[alignment]
        stroke_width = 1 if index == time_line_index and time_font_needs_stroke else 0
        draw_txt.text(
            (round(padding + offset - box[0]), round(padding + index * line_advance - box[1])),
            line, font=line_font, fill=(160, 160, 160, opacity),
            stroke_width=stroke_width, stroke_fill=(160, 160, 160, opacity),
        )

    resample_bilinear = getattr(getattr(Image, "Resampling", Image), "BILINEAR", Image.BILINEAR)
    rotated_txt = txt_img.rotate(angle, expand=True, resample=resample_bilinear)
    rot_w, rot_h = rotated_txt.size

    watermark_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    # Keep the reference layout at four columns and roughly two rows as the
    # capture size changes. The gap controls adjust that pitch in pixels.
    step_x = max(1, round(width / 4 + gap_x - round(WATERMARK_DEFAULTS["watermark_gap_x"] * scale)))
    step_y = max(1, round(height * 5 / 9 + gap_y - round(WATERMARK_DEFAULTS["watermark_gap_y"] * scale)))
    start_x = round(width * 0.032)
    start_y = round(height * 0.032)

    for y in range(start_y, height + rot_h, step_y):
        for x in range(start_x, width + rot_w, step_x):
            right, bottom = min(width, x + rot_w), min(height, y + rot_h)
            if right > x and bottom > y:
                watermark_layer.alpha_composite(rotated_txt.crop((0, 0, right - x, bottom - y)), (x, y))

    combined = Image.alpha_composite(img_rgba, watermark_layer)
    return combined


def _browser_window_rect(page: Page, width: int, height: int, *, position: bool = True, scale_factor: float = 1.0) -> dict[str, int]:
    """Ask Chromium for this page's actual window instead of guessing a screen area."""
    if width <= 0 or height <= 0:
        raise ValueError("未配置有效的浏览器窗口尺寸")
    session = page.context.new_cdp_session(page)
    try:
        window_id = session.send("Browser.getWindowForTarget")["windowId"]
        if position:
            left, top = _logical_window_position(scale_factor)
            session.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {"windowState": "normal", "left": left, "top": top, "width": width, "height": height},
            })
        page.bring_to_front()
        page.wait_for_timeout(300)
        bounds = session.send("Browser.getWindowBounds", {"windowId": window_id})["bounds"]
        rect = {key: int(bounds[key]) for key in ("left", "top", "width", "height")}
        if rect["width"] <= 0 or rect["height"] <= 0:
            raise RuntimeError("目标浏览器窗口没有有效尺寸")
        if abs(rect["width"] - width) > 8 or abs(rect["height"] - height) > 8:
            raise RuntimeError(
                f"浏览器窗口实际尺寸 {rect['width']}x{rect['height']} 与配置 {width}x{height} 不一致"
            )
        if page.evaluate("document.visibilityState") != "visible":
            raise RuntimeError("目标浏览器窗口不可见")
        return rect
    except Exception as exc:
        raise RuntimeError(f"无法定位目标浏览器窗口：{exc}") from exc
    finally:
        try:
            session.detach()
        except Exception:
            logger.debug("关闭窗口定位会话失败", exc_info=True)


def _rect_inside_screen(rect: dict[str, int], screen: dict[str, int]) -> bool:
    return (
        rect["left"] >= screen["left"]
        and rect["top"] >= screen["top"]
        and rect["left"] + rect["width"] <= screen["left"] + screen["width"]
        and rect["top"] + rect["height"] <= screen["top"] + screen["height"]
    )


def _assert_real_window_viewport(page: Page) -> None:
    sizes = page.evaluate("() => ({outer: window.outerWidth, inner: window.innerWidth})")
    outer = int(sizes["outer"])
    inner = int(sizes["inner"])
    if outer <= 0 or inner <= 0 or abs(outer - inner) > max(32, round(outer * 0.03)):
        raise RuntimeError(
            f"网页视口宽度 {inner}px 与浏览器窗口宽度 {outer}px 不匹配，"
            "页面可能只显示在窗口的一部分"
        )


def _active_browser_frame_rect() -> dict[str, int]:
    """Locate the decorated X11 frame of the foreground Chrome window."""
    import subprocess

    env = {**os.environ, "LC_ALL": "C"}
    active_info = subprocess.check_output(
        ["xprop", "-root", "_NET_ACTIVE_WINDOW"], text=True, timeout=5, env=env,
    )
    active_match = re.search(r"window id #\s*(0x[0-9a-fA-F]+)", active_info)
    if active_match is None or int(active_match.group(1), 16) == 0:
        raise RuntimeError("无法确定前台浏览器窗口")
    window_id = active_match.group(1)
    window_class = subprocess.check_output(
        ["xprop", "-id", window_id, "WM_CLASS"], text=True, timeout=5, env=env,
    )
    if not re.search(r"chrome|chromium", window_class, re.IGNORECASE):
        raise RuntimeError(f"前台窗口不是 Chrome：{window_class.strip()}")
    info = subprocess.check_output(
        ["xwininfo", "-frame", "-id", window_id], text=True, timeout=5, env=env,
    )
    fields = {
        "left": "Absolute upper-left X",
        "top": "Absolute upper-left Y",
        "width": "Width",
        "height": "Height",
    }
    rect = {}
    for key, label in fields.items():
        match = re.search(rf"^\s*{re.escape(label)}:\s*(-?\d+)\s*$", info, re.MULTILINE)
        if match is None:
            raise RuntimeError(f"无法读取浏览器窗口外框的 {label}")
        rect[key] = int(match.group(1))
    if rect["width"] <= 0 or rect["height"] <= 0:
        raise RuntimeError("浏览器窗口外框没有有效尺寸")
    return rect


def _take_screenshot(
    page: Page,
    item: dict[str, Any],
    output_path: Path,
    is_real_capture: bool,
    expected_width: int | None = None,
    expected_height: int | None = None,
    timeout_ms: int = 60_000,
    *,
    deadline: float | None = None,
    window_scale_factor: float = 1.0,
    query_tracker: _GrafanaQueryTracker | None = None,
) -> dict[str, Any] | None:
    deadline = deadline if deadline is not None else time.monotonic() + timeout_ms / 1000
    if is_real_capture:
        logger.info("  正在截取浏览器窗口…")
        
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
            
        # 3. CDP 定位浏览器，X11 获取包含标题栏和窗口按钮的完整外框。
        try:
            logical_width, logical_height = _css_viewport_size(
                int(expected_width or 0), int(expected_height or 0), window_scale_factor,
            )
            _browser_window_rect(page, logical_width, logical_height, position=False)
            rect = _active_browser_frame_rect()
            if abs(rect["width"] - int(expected_width or 0)) > 8 or abs(rect["height"] - int(expected_height or 0)) > 8:
                raise RuntimeError(
                    f"浏览器实际截图尺寸 {rect['width']}x{rect['height']} 与配置 "
                    f"{expected_width}x{expected_height} 不一致"
                )
            content = (_wait_for_grafana_panels(
                page, min(5_000, _remaining_ms(deadline)), query_tracker, settle_ms=0,
            ) if _is_grafana_dashboard(str(page.url)) else None)
            _remaining_ms(deadline)
            with mss.mss() as sct:
                if not sct.monitors:
                    raise RuntimeError("没有可用的虚拟显示器")
                screens = sct.monitors[1:] or sct.monitors
                if not any(_rect_inside_screen(rect, screen) for screen in screens):
                    screen = sct.monitors[0]
                    raise RuntimeError(
                        f"浏览器窗口 {rect['width']}x{rect['height']} 位于 ({rect['left']},{rect['top']})，"
                        f"超出虚拟屏幕 {screen['width']}x{screen['height']}；请调整 XVFB_WIDTH/XVFB_HEIGHT。"
                    )
                sct_img = sct.grab(rect)
                mss.tools.to_png(sct_img.rgb, sct_img.size, output=str(output_path))
                logger.info(f"  浏览器窗口已截取：{rect['width']}x{rect['height']}")
            return content
        except Exception as exc:
            raise RuntimeError(
                f"真实浏览器窗口截图捕获失败：{exc}。\n"
                "请确保 Xvfb 服务正常运行在后台，且没有其他窗口遮挡。"
            ) from exc
    else:
        # 普通 Playwright 截图
        capture_mode = str(item.get("capture_mode") or "viewport")
        selector = str(item.get("css_selector") or "").strip()
        logger.info(f"  ├─ 画面捕获: 使用 Playwright 截图 (模式: {capture_mode})")
        if capture_mode == "selector" and selector:
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=_remaining_ms(deadline))
            locator.screenshot(path=str(output_path), timeout=_remaining_ms(deadline))
        else:
            page.screenshot(path=str(output_path), full_page=(capture_mode == "full_page"), timeout=_remaining_ms(deadline))
        return None


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
        
    logger.info("-" * 68)
    logger.info(f"🔑 [认证配置测试登录] 配置: '{auth_profile.get('name')}' (ID: {auth_profile_id})")
    logger.info(f"├─ 登录地址: {login_url}")
    logger.info(f"├─ 用户名: {username}")

    timeout_ms = 30000
    try:
        with sync_playwright() as p:
            launch_kwargs = {
                "headless": True,
                "channel": "chrome",
                "ignore_default_args": ["--enable-automation", "--disable-sync"],
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--force-device-scale-factor=1",
                    "--high-dpi-support=1",
                    "--lang=zh-CN",
                    "--disable-translate",
                    "--disable-features=Translate,TranslateUI",
                    "--disable-save-password-bubble",
                ]
            }
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception:
                launch_kwargs.pop("channel", None)
                browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                ignore_https_errors=True,
                locale="zh-CN",
                timezone_id=settings.default_timezone,
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1.0,
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            
            page.on("console", lambda msg: logger.warning(f"  ├─ 浏览器 Console: [{msg.type}] {msg.text}"))
            page.on("requestfailed", lambda req: logger.warning(f"  ├─ 网络 Request 失败: {req.url} - ({req.failure.error_text if req.failure else 'unknown'})"))
            
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)
                _assert_login_page_ready(page, login_url)
                _fill_login_form(page, auth_profile, username, password)
                logger.info("├─ 表单提交成功，等待授权成功标识...")
                
                success_selector = str(auth_profile.get("success_selector") or "").strip()
                _wait_for_login_success(page, success_selector, timeout_ms)
            except Exception as inner_exc:
                try:
                    settings.log_dir.mkdir(parents=True, exist_ok=True)
                    error_path = settings.log_dir / "test_login_error.png"
                    page.screenshot(path=str(error_path))
                    logger.info(f"├─ 认证异常，已保存排查截图至: {error_path.name}")
                except Exception as screenshot_exc:
                    logger.error(f"├─ 保存错误排查截图失败: {screenshot_exc}")
                raise inner_exc
                
            context.close()
            browser.close()
        logger.info("└─ ✔ 认证登录测试成功！页面选择器匹配正常")
        logger.info("-" * 68)
        return {"success": True, "message": "测试登录成功！凭据配置及网页选择器匹配正常！"}
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.error(f"└─ ✖ 认证登录测试失败: {err_msg}")
        logger.info("-" * 68)
        return {"success": False, "message": f"测试登录失败: {err_msg}"}


def _encrypt_state_data(data_str: str) -> bytes:
    from app.utils import encrypt_secret
    return encrypt_secret(data_str).encode("utf-8")


def _decrypt_state_data(data_bytes: bytes) -> str:
    from app.utils import decrypt_secret
    return decrypt_secret(data_bytes.decode("utf-8"))
