import sqlite3
import sys
import types

import pytest

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import screenshot


def test_grafana_sidebar_uses_close_menu_button():
    clicked = []

    class Button:
        first = None

        def __init__(self):
            self.first = self

        def is_visible(self):
            return True

        def click(self, **kwargs):
            clicked.append(kwargs)

    class Page:
        def get_by_role(self, role, *, name):
            assert role == "button"
            assert name.search("关闭菜单")
            return Button()

    screenshot._collapse_grafana_sidebar(Page(), screenshot.time.monotonic() + 5)
    assert len(clicked) == 1
    assert 0 < clicked[0]["timeout"] <= 2_000


def test_dashboard_slug_containing_login_is_not_a_login_page():
    class PageStub:
        url = "https://grafana.example/d/abc/login-overview"

        def locator(self, _selector):
            return types.SimpleNamespace(count=lambda: 0)

    page = PageStub()
    profile = {"login_url": "https://grafana.example/login"}
    assert not screenshot._is_on_login_page(page, profile)
    page.url = "https://grafana.example/login?redirect=/d/abc/login-overview"
    assert screenshot._is_on_login_page(page, profile)
    page.url = "https://grafana.example/auth/login/callback"
    assert screenshot._is_on_login_page(page, profile)


def test_login_failure_scan_does_not_wait_for_absent_messages():
    class TextStub:
        first = None

        def __init__(self):
            self.first = self

        def is_visible(self):
            return False

        def wait_for(self, **_kwargs):
            raise AssertionError("Missing error text must not consume the login timeout")

    page = types.SimpleNamespace(get_by_text=lambda *_args, **_kwargs: TextStub())
    assert not screenshot._is_login_failure_visible(page)


def test_grafana_refresh_is_disabled_in_capture_page():
    class LocatorStub:
        def __init__(self, page, kind):
            self.page = page
            self.kind = kind
            self.first = self

        def wait_for(self, *, state, timeout):
            assert state == "visible" and timeout > 0

        def get_attribute(self, name, *, timeout):
            assert name == "aria-label"
            assert 0 < timeout <= 750
            return "Choose refresh time interval with current interval 10 seconds selected"

        def evaluate(self, expression, *, timeout):
            assert expression == "element => element.click()"
            assert 0 < timeout <= 750
            self.page.clicked.append(self.kind)

        def get_by_text(self, text, *, exact):
            assert text == "Off" and exact
            return LocatorStub(self.page, "Off")

    class PageStub:
        url = "http://grafana.test/d/board/inspection?refresh=10s"

        def __init__(self):
            self.clicked = []

        def locator(self, _selector):
            return LocatorStub(self, "refresh")

        def get_by_role(self, role):
            assert role == "menu"
            return LocatorStub(self, "menu")

        def wait_for_function(self, expression, *, timeout):
            assert "Auto refresh turned off" in expression and 0 < timeout <= 750

    page = PageStub()
    screenshot._disable_grafana_auto_refresh(page, screenshot.time.monotonic() + 5)
    assert page.clicked == ["refresh", "Off"]


def test_grafana_refresh_control_missing_does_not_fail_capture():
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    class LocatorStub:
        first = None

        def __init__(self):
            self.first = self

        def wait_for(self, *, state, timeout):
            assert state == "visible" and 0 < timeout <= 750
            raise PlaywrightTimeoutError("刷新按钮不存在")

    class PageStub:
        url = "http://grafana.test/d/board/inspection?refresh=10s"

        def locator(self, _selector):
            return LocatorStub()

    screenshot._disable_grafana_auto_refresh(PageStub(), screenshot.time.monotonic() + 240)


def test_grafana_refresh_menu_missing_does_not_fail_capture():
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    class LocatorStub:
        first = None

        def __init__(self, kind):
            self.first = self
            self.kind = kind

        def wait_for(self, *, state, timeout):
            assert state == "visible" and 0 < timeout <= 750
            if self.kind == "Off":
                raise PlaywrightTimeoutError("Off 选项不存在")

        def get_attribute(self, _name, *, timeout):
            assert 0 < timeout <= 750
            return "Choose refresh time interval"

        def evaluate(self, _expression, *, timeout):
            assert 0 < timeout <= 750

        def get_by_text(self, _text, *, exact):
            return LocatorStub("Off")

    class PageStub:
        url = "http://grafana.test/d/board/inspection?refresh=10s"

        class KeyboardStub:
            def __init__(self):
                self.pressed = []

            def press(self, key, *, timeout):
                assert timeout == 500
                self.pressed.append(key)

        def __init__(self):
            self.keyboard = self.KeyboardStub()

        def locator(self, _selector):
            return LocatorStub("refresh")

        def get_by_role(self, _role):
            return LocatorStub("menu")

    page = PageStub()
    screenshot._disable_grafana_auto_refresh(page, screenshot.time.monotonic() + 240)
    assert page.keyboard.pressed == ["Escape"]


def test_screenshot_wait_uses_only_configured_waits():
    class PageStub:
        url = "http://example.test/"

        def __init__(self):
            self.calls = []

        def wait_for_selector(self, selector, state, timeout):
            self.calls.append(("selector", selector, state, timeout))

        def wait_for_timeout(self, milliseconds):
            self.calls.append(("timeout", milliseconds))

    page = PageStub()
    screenshot._wait_for_page(page, {"wait_selector": "#chart", "wait_seconds": 1.5}, 8000)
    assert page.calls[0][:3] == ("selector", "#chart", "visible")
    assert 0 < page.calls[0][3] <= 8000
    assert page.calls[1] == ("timeout", 1500)


def test_kibana_waits_for_visible_panels_and_records_no_data():
    class PageStub:
        url = "http://kibana.test/app/dashboards#/view/abc"

        def __init__(self):
            self.calls = 0

        def evaluate(self, expression):
            assert 'dashboardPanel' in expression
            self.calls += 1
            state = "loading" if self.calls == 1 else "no_data"
            return {"panels": [{"title": "请求量", "state": state}]}

        def wait_for_timeout(self, _milliseconds):
            pass

    page = PageStub()
    result = screenshot._wait_for_kibana_panels(page, 1000, settle_ms=0)
    assert result == {"status": "no_data", "summary": "全部 1 个图表无数据，需确认"}
    assert page.calls == 2


def test_kibana_page_wait_uses_panel_check(monkeypatch):
    class PageStub:
        url = "http://kibana.test/app/dashboards#/view/abc"

    expected = {"status": "data", "summary": "1 个图表已显示"}
    monkeypatch.setattr(screenshot, "_wait_for_kibana_panels", lambda *_args: expected)
    assert screenshot._wait_for_page(PageStub(), {"item_type": "kibana"}, 1000) == expected


def test_kibana_query_error_names_panel():
    class PageStub:
        def evaluate(self, _expression):
            return {"panels": [{"title": "请求量", "state": "error", "detail": "查询失败"}]}

    with pytest.raises(RuntimeError, match="Kibana 图表“请求量”查询错误：查询失败"):
        screenshot._wait_for_kibana_panels(PageStub(), 1000)


def test_kibana_wait_timeout_names_loading_panel():
    class PageStub:
        def evaluate(self, _expression):
            return {"panels": [{"title": "请求量", "state": "loading"}]}

        def wait_for_timeout(self, _milliseconds):
            pass

    with pytest.raises(TimeoutError, match="Kibana 图表未完成加载：请求量"):
        screenshot._wait_for_kibana_panels(PageStub(), 1)


def test_grafana_waits_for_panel_loading_to_finish():
    class PageStub:
        url = "http://grafana.test/d/board/inspection"

        def __init__(self):
            self.calls = 0
            self.waited = []

        def wait_for_timeout(self, milliseconds):
            self.waited.append(milliseconds)

        def evaluate(self, expression):
            self.calls += 1
            return {"panels": [{"title": "CPU", "state": "loading" if self.calls == 1 else "data"}]}

    page = PageStub()
    result = screenshot._wait_for_page(page, {"wait_seconds": 0.1}, 3000)
    assert result["status"] == "data"
    assert page.calls >= 2
    assert page.waited[-1] == 100


def test_grafana_no_data_is_success_but_recorded():
    class PageStub:
        url = "http://grafana.test/d/board/inspection"

        def evaluate(self, _expression):
            return {"panels": [
                {"title": "CPU", "state": "data"},
                {"title": "服务检查", "state": "no_data"},
            ]}

        def wait_for_timeout(self, _milliseconds):
            pass

    result = screenshot._wait_for_grafana_panels(PageStub(), 1000, settle_ms=0)
    assert result == {"status": "partial_no_data", "summary": "1 个图表无数据：服务检查"}


def test_grafana_waits_for_later_query_cycle():
    class PageStub:
        url = "http://grafana.test/d/board/inspection"

        def evaluate(self, _expression):
            return {"panels": [{"title": "CPU", "state": "data"}]}

        def wait_for_timeout(self, milliseconds):
            screenshot.time.sleep(milliseconds / 1000)

    class TrackerStub:
        pending = {1}
        completed_cycle = True

        def check(self):
            pass

    try:
        screenshot._wait_for_grafana_panels(PageStub(), 30, TrackerStub(), settle_ms=0)
    except TimeoutError as exc:
        assert "数据查询仍在进行" in str(exc)
    else:
        raise AssertionError("后续查询未完成时不能截图")


def test_grafana_all_no_data_needs_review():
    class PageStub:
        url = "http://grafana.test/d/board/inspection"

        def evaluate(self, _expression):
            return {"panels": [{"title": "服务检查", "state": "no_data"}]}

        def wait_for_timeout(self, _milliseconds):
            pass

    result = screenshot._wait_for_grafana_panels(PageStub(), 1000, settle_ms=0)
    assert result["status"] == "no_data"
    assert "需确认" in result["summary"]


def test_grafana_panel_error_fails_immediately():
    class PageStub:
        url = "http://grafana.test/d/board/inspection"

        def evaluate(self, _expression):
            return {"panels": [{"title": "CPU", "state": "error", "detail": "Query error"}]}

    try:
        screenshot._wait_for_grafana_panels(PageStub(), 3000)
    except RuntimeError as exc:
        assert "CPU" in str(exc)
        assert "Query error" in str(exc)
    else:
        raise AssertionError("查询错误不能保存为成功截图")


def test_grafana_query_http_error_is_not_no_data():
    class PageStub:
        url = "http://grafana.test/d/board/inspection"

        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

    class ResponseStub:
        url = "http://grafana.test/api/ds/query"
        status = 502

    page = PageStub()
    tracker = screenshot._GrafanaQueryTracker(page)
    page.handlers["response"](ResponseStub())
    try:
        tracker.check()
    except RuntimeError as exc:
        assert "HTTP 502" in str(exc)
    else:
        raise AssertionError("查询接口失败不能当作正常无数据")


def test_grafana_query_completes_before_no_data_capture():
    from playwright.sync_api import sync_playwright

    html = """
    <div class="react-grid-item"><section class="panel-container">
      <div class="panel-header">服务检查</div>
      <div class="panel-content">Loading data</div>
    </section></div>
    <script>
      fetch('/api/ds/query', {method: 'POST', body: '{}'}).then(response => response.json()).then(() => {
        document.querySelector('.panel-content').textContent = 'No data';
      });
    </script>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.route("https://grafana.example/api/ds/query", lambda route: route.fulfill(
            body='{"results":{"A":{"frames":[]}}}', content_type="application/json",
        ))
        page.route("https://grafana.example/d/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        tracker = screenshot._GrafanaQueryTracker(page)
        page.goto("https://grafana.example/d/test/dashboard")
        result = screenshot._wait_for_grafana_panels(page, 3000, tracker, settle_ms=0)
        assert tracker.seen == 1
        assert result["status"] == "no_data"
        browser.close()


def test_grafana_query_json_error_blocks_capture():
    from playwright.sync_api import sync_playwright

    html = """
    <div class="react-grid-item"><section class="panel-container">
      <div class="panel-header">服务检查</div><div class="panel-content">No data</div>
    </section></div>
    <script>fetch('/api/ds/query', {method: 'POST', body: '{}'});</script>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.route("https://grafana.example/api/ds/query", lambda route: route.fulfill(
            body='{"results":{"A":{"error":"数据源不可用","status":500}}}',
            content_type="application/json",
        ))
        page.route("https://grafana.example/d/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        tracker = screenshot._GrafanaQueryTracker(page)
        page.goto("https://grafana.example/d/test/dashboard")
        try:
            screenshot._wait_for_grafana_panels(page, 3000, tracker)
        except RuntimeError as exc:
            assert "数据源不可用" in str(exc)
        else:
            raise AssertionError("HTTP 200 中的 Grafana 查询错误不能当作正常无数据")
        browser.close()


def test_grafana_row_heading_does_not_block_rendered_panel():
    from playwright.sync_api import sync_playwright

    html = """
    <div class="react-grid-item"><div class="dashboard-row">核心概览</div></div>
    <div class="react-grid-item"><section class="panel-container">
      <div class="panel-header">CPU 使用率</div>
      <div class="panel-content"><canvas width="200" height="100"></canvas></div>
    </section></div>
    <div class="react-grid-item"><div class="panel-container">
      <div class="panel-header">自定义 SVG 图表</div>
      <div class="panel-content"><svg width="200" height="100"><circle cx="30" cy="30" r="20"/></svg></div>
    </div></div>
    <div class="react-grid-item"><section class="panel-container">
      <div class="panel-header">服务数据</div>
      <div class="panel-content">No data</div>
    </section></div>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.route("https://grafana.example/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://grafana.example/d/test/dashboard")
        page.evaluate("document.querySelector('canvas').getContext('2d').fillRect(10, 50, 100, 2)")
        screenshot._wait_for_grafana_panels(page, 3000)
        browser.close()


def test_grafana_11_generated_panel_classes_are_recognized():
    from playwright.sync_api import sync_playwright

    html = """
    <div class="react-grid-item"><div class="css-1f8sh1y"><section
      class="css-18yh62t-panel-container" data-testid="data-testid Panel header CPU / Memory Trend">
      <div class="css-ol7v04-panel-loading-bar-container"></div>
      <div class="css-19gv2o1-panel-header" data-testid="header-container">
        <div class="css-biljvk-panel-title">CPU / Memory Trend</div>
      </div>
      <div class="css-kuoxoh-panel-content" data-testid="data-testid panel content">
        <canvas width="200" height="100"></canvas><div>A-series 25%</div>
      </div>
    </section></div><div class="scene-resize-handle"><svg></svg></div></div>
    <div class="react-grid-item"><section class="css-18yh62t-panel-container"
      data-testid="data-testid Panel header Service Check Table">
      <div class="css-19gv2o1-panel-header"><div class="css-biljvk-panel-title">Service Check Table</div></div>
      <div class="css-kuoxoh-panel-content" data-testid="data-testid panel content">No data</div>
    </section></div>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.route("https://grafana.example/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://grafana.example/d/test/dashboard")
        page.evaluate("document.querySelector('canvas').getContext('2d').fillRect(10, 50, 100, 2)")
        result = screenshot._wait_for_grafana_panels(page, 3000, settle_ms=0)
        assert result["status"] == "partial_no_data"
        assert "Service Check Table" in result["summary"]
        browser.close()


def test_grafana_canvas_must_paint_before_capture():
    from playwright.sync_api import sync_playwright

    html = '<div class="react-grid-item"><section class="panel-container"><div class="panel-header">CPU</div><div class="panel-content"><canvas width="200" height="100"></canvas><div>CPU 25%</div></div></section></div>'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.route("https://grafana.example/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://grafana.example/d/test/dashboard")
        assert screenshot._grafana_snapshot(page)["panels"][0]["state"] == "loading"
        page.evaluate("document.querySelector('canvas').getContext('2d').fillRect(0, 50, 200, 2)")
        assert screenshot._grafana_snapshot(page)["panels"][0]["state"] == "data"
        browser.close()


def test_grafana_panel_header_icon_does_not_count_as_rendered_content():
    from playwright.sync_api import sync_playwright

    html = """
    <div class="react-grid-item"><section class="panel-container">
      <div class="panel-header">CPU 使用率 <svg width="16" height="16"></svg></div>
      <div class="panel-content"></div>
    </section></div>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.route("https://grafana.example/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://grafana.example/d/test/dashboard")
        try:
            screenshot._wait_for_grafana_panels(page, 700)
        except TimeoutError as exc:
            assert "图表未完成加载" in str(exc)
        else:
            raise AssertionError("只有面板标题和图标时，不应判定渲染完成")
        page.evaluate("document.querySelector('.panel-content').innerHTML = '<svg width=\"200\" height=\"100\"></svg>'")
        screenshot._wait_for_grafana_panels(page, 3000)
        browser.close()


def test_window_rect_must_fit_inside_screen():
    screen = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    assert screenshot._rect_inside_screen({"left": 100, "top": 50, "width": 800, "height": 600}, screen)
    assert not screenshot._rect_inside_screen({"left": 1800, "top": 50, "width": 800, "height": 600}, screen)


def test_real_window_scale_keeps_physical_window_at_requested_size():
    real_args = screenshot.chrome_launch_args(1.5, True, 3840, 2160)
    assert "--window-size=2560,1440" in real_args
    assert "--window-position=13,33" in real_args
    assert "--force-device-scale-factor=1.5" in real_args
    assert "--use-gl=angle" in real_args
    assert "--use-angle=swiftshader" in real_args
    headless_args = screenshot.chrome_launch_args(1.5, False)
    assert "--force-device-scale-factor=1.5" in headless_args
    assert "--use-angle=swiftshader" not in headless_args


def test_full_desktop_image_keeps_requested_pixel_size(tmp_path):
    from PIL import Image

    job = {"browser_width": 1200, "browser_height": 800}
    item = {"taskbar_enabled": 1}
    window_width, window_height = screenshot.browser_window_size(item, job, 147)
    assert window_width == 1200
    assert window_height < 800

    image_path = tmp_path / "desktop.png"
    Image.new("RGB", (window_width + 1, window_height + 1), "red").save(image_path)
    screenshot._normalize_window_image(image_path, window_width, window_height)
    screenshot._apply_taskbar_template(image_path, screenshot._taskbar_template_path(147))
    screenshot._assert_image_size(image_path, (1200, 800))
    with Image.open(image_path) as image:
        assert image.getpixel((100, 100)) == (255, 0, 0, 255)

    assert screenshot.browser_window_size({"taskbar_enabled": 0}, job, 147) == (1200, 800)


def test_real_window_rejects_partial_page_viewport():
    class PageStub:
        def __init__(self, outer, inner):
            self.outer = outer
            self.inner = inner

        def evaluate(self, _script):
            return {"outer": self.outer, "inner": self.inner}

    screenshot._assert_real_window_viewport(PageStub(2560, 2560))
    screenshot._assert_real_window_viewport(PageStub(2560, 2552))
    try:
        screenshot._assert_real_window_viewport(PageStub(3840, 2560))
    except RuntimeError as exc:
        assert "只显示在窗口的一部分" in str(exc)
    else:
        raise AssertionError("右侧留白的窗口应被拒绝")


def test_active_browser_frame_includes_window_decoration(monkeypatch):
    import subprocess

    def check_output(args, **_kwargs):
        if args[:3] == ["xprop", "-root", "_NET_ACTIVE_WINDOW"]:
            return "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3039\n"
        if args[:3] == ["xprop", "-id", "0x3039"]:
            return 'WM_CLASS(STRING) = "google-chrome", "Google-chrome"\n'
        if args[:3] == ["xwininfo", "-frame", "-id"]:
            return (
                "  Absolute upper-left X:  18\n"
                "  Absolute upper-left Y:  20\n"
                "  Width:  804\n"
                "  Height:  632\n"
            )
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "check_output", check_output)
    assert screenshot._active_browser_frame_rect() == {
        "left": 18, "top": 20, "width": 804, "height": 632,
    }


def test_browser_window_rect_uses_target_window_bounds():
    class SessionStub:
        def __init__(self):
            self.calls = []
            self.detached = False

        def send(self, method, params=None):
            self.calls.append((method, params))
            if method == "Browser.getWindowForTarget":
                return {"windowId": 7}
            if method == "Browser.getWindowBounds":
                return {"bounds": {"left": 12, "top": 24, "width": 800, "height": 600}}
            return {}

        def detach(self):
            self.detached = True

    class PageStub:
        def __init__(self):
            self.session = SessionStub()
            self.context = self
            self.front = False

        def new_cdp_session(self, _page):
            return self.session

        def bring_to_front(self):
            self.front = True

        def wait_for_timeout(self, _milliseconds):
            pass

        def evaluate(self, _expression):
            return "visible"

    page = PageStub()
    assert screenshot._browser_window_rect(page, 800, 600) == {"left": 12, "top": 24, "width": 800, "height": 600}
    assert page.front and page.session.detached
    assert page.session.calls[1] == (
        "Browser.setWindowBounds",
        {"windowId": 7, "bounds": {"windowState": "normal", "left": 20, "top": 50, "width": 800, "height": 600}},
    )


def test_each_capture_uses_new_context_and_publishes_only_completed_file(tmp_path, monkeypatch):
    contexts = []
    navigation_timeouts = []

    class PageStub:
        url = "http://example.test/"

        def set_default_timeout(self, _timeout):
            pass

        def goto(self, _url, **kwargs):
            navigation_timeouts.append(kwargs["timeout"])

        def close(self):
            pass

    class ContextStub:
        def __init__(self):
            self.closed = False

        def new_page(self):
            return PageStub()

        def close(self):
            self.closed = True

    def new_context(*_args):
        context = ContextStub()
        contexts.append(context)
        return context

    monkeypatch.setattr(screenshot, "_new_context", new_context)
    monkeypatch.setattr(screenshot, "_storage_state_path", lambda _auth: None)
    monkeypatch.setattr(screenshot, "_ensure_logged_in", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(screenshot, "_assert_not_login_page_before_screenshot", lambda *_args: None)
    monkeypatch.setattr(screenshot, "_wait_for_page", lambda *_args: None)
    monkeypatch.setattr(screenshot, "_take_screenshot", lambda _page, _item, path, *_args, **_kwargs: path.write_bytes(b"png"))
    monkeypatch.setattr(screenshot, "_apply_configured_watermark", lambda *_args: None)
    monkeypatch.setattr(screenshot, "_apply_simulated_taskbar", lambda *_args: None)
    monkeypatch.setattr(screenshot, "_persist_storage_state", lambda *_args: None)

    item = {"url": "http://example.test/"}
    job = {"browser_scale_factor": 1.0}
    for index in range(2):
        output = tmp_path / f"capture-{index}.png"
        result = screenshot._capture_with_browser(
            item, job, 1, object(), 800, 600, 5000, tmp_path, output,
            item["url"], False, None,
        )
        assert result.file_path == str(output)
        assert output.read_bytes() == b"png"
    assert len(contexts) == 2
    assert all(context.closed for context in contexts)

    budgeted_output = tmp_path / "budgeted.png"
    screenshot._capture_with_browser(
        item, job, 1, object(), 800, 600, 5000, tmp_path, budgeted_output,
        item["url"], False, None,
        total_deadline=screenshot.time.monotonic() + 2,
    )
    assert budgeted_output.exists()
    assert 0 < navigation_timeouts[-1] <= 2000

    def fail_processing(*_args):
        raise RuntimeError("watermark failed")

    monkeypatch.setattr(screenshot, "_apply_configured_watermark", fail_processing)
    failed_output = tmp_path / "failed.png"
    try:
        screenshot._capture_with_browser(
            item, job, 1, object(), 800, 600, 5000, tmp_path, failed_output,
            item["url"], False, None,
        )
    except RuntimeError as exc:
        assert str(exc) == "处理图片失败：watermark failed"
    else:
        raise AssertionError("capture should fail")
    assert not failed_output.exists()
    assert not list(tmp_path.glob(".capture-*.png"))
