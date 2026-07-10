from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_referer_origin_check_does_not_use_prefix_match() -> None:
    source = read_text("app/main.py")
    assert "referer.lower().startswith(base_origin)" not in source


def test_spa_logout_is_not_get_link() -> None:
    source = read_text("app/static/frontend/index.html")
    assert 'href="{{ url_for_frontend(\'logout\') }}"' not in source
    assert 'data-action="logout"' in source


def test_taskbar_template_selection_is_run_scoped() -> None:
    import types

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.BrowserContext = object
    sync_api.Page = object
    sync_api.TimeoutError = TimeoutError
    sync_api.sync_playwright = lambda: None
    sys.modules.setdefault("playwright", types.ModuleType("playwright"))
    sys.modules.setdefault("playwright.sync_api", sync_api)
    repository = types.ModuleType("app.repository")
    repository.get_auth_profile = lambda *_args, **_kwargs: None
    sys.modules.setdefault("app.repository", repository)
    storage_paths = types.ModuleType("app.storage_paths")
    storage_paths.get_screenshot_dir = lambda run_id: ROOT / "data" / "screenshots" / str(run_id)
    sys.modules.setdefault("app.storage_paths", storage_paths)

    from app import screenshot

    paths = screenshot._taskbar_template_paths()
    names = {path.name for path in paths}
    assert {"windows-taskbar-template.png", "1.png", "2.png", "3.png"} <= names
    assert screenshot._taskbar_template_path(12345) == screenshot._taskbar_template_path(12345)
    assert screenshot._taskbar_template_path(12345) in paths
if __name__ == "__main__":
    test_referer_origin_check_does_not_use_prefix_match()
    test_spa_logout_is_not_get_link()
    test_taskbar_template_selection_is_run_scoped()
