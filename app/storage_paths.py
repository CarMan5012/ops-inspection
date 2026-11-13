from __future__ import annotations

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.settings import settings
from app.db import connect


def get_run_date(run_id: int | str) -> tuple[str, str, str]:
    """
    优先从数据库查询 run_records 中的 started_at 时间，解析出其对应的年、月、日。
    若查询失败或格式异常，fallback 使用本地当前时间。
    """
    started_at = None
    try:
        with connect() as conn:
            row = conn.execute("SELECT started_at FROM run_records WHERE id = ?", (int(run_id),)).fetchone()
            if row:
                started_at = row[0]
    except Exception:
        pass

    dt = None
    if started_at:
        try:
            started_at_clean = str(started_at).replace("T", " ")
            # 支持 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD
            dt = datetime.strptime(started_at_clean[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(started_at_clean[:10], "%Y-%m-%d")
            except ValueError:
                pass

    if not dt:
        local_tz = ZoneInfo(settings.default_timezone)
        dt = datetime.now(local_tz)

    return dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d")


def get_screenshot_dir(run_id: int | str) -> Path:
    """截图和日报共用 reports/YYYY/MM/DD/ 日期目录。"""
    yyyy, mm, dd = get_run_date(run_id)
    target_dir = settings.report_dir / yyyy / mm / dd
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def get_report_dir(run_id: int | str) -> Path:
    """报告与截图放在同一个日期目录。"""
    return get_screenshot_dir(run_id)


def unique_artifact_path(directory: Path, stem: str, suffix: str) -> Path:
    """同名文件追加序号，避免同一天的重复执行覆盖已有产物。"""
    path = directory / f"{stem}{suffix}"
    number = 2
    while path.exists():
        path = directory / f"{stem}-{number}{suffix}"
        number += 1
    return path


def is_safe_path(path: Path | str) -> bool:
    """
    安全路径校验，确保操作的文件/文件夹在 data 目录之下，避免路径穿越
    """
    try:
        resolved = Path(path).resolve()
        data_dir_resolved = settings.data_dir.resolve()
        return data_dir_resolved in resolved.parents or resolved == data_dir_resolved
    except Exception:
        return False


def resolve_artifact_path(file_path: str | Path) -> Path:
    """Resolve a stored artifact path across container and local data mounts."""
    if not file_path:
        return Path("")

    path = Path(file_path)

    # Map paths recorded in the container to the current data mount.
    path_text = str(file_path).replace("\\", "/")
    app_data_prefix = "/app/data/"
    if path_text.startswith(app_data_prefix):
        path = settings.data_dir / path_text[len(app_data_prefix):]

    if path.exists() and is_safe_path(path):
        return path.resolve()

    return path
