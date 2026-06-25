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
    """
    获取截图项在新结构下的存放目录，并自动创建：
    data/screenshots/YYYY/MM/DD/run-{run_id}/
    """
    yyyy, mm, dd = get_run_date(run_id)
    target_dir = settings.screenshot_dir / yyyy / mm / dd / f"run-{run_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def get_report_dir(run_id: int | str) -> Path:
    """
    获取报告在新结构下的存放目录，并自动创建：
    data/reports/YYYY/MM/DD/run-{run_id}/
    """
    yyyy, mm, dd = get_run_date(run_id)
    target_dir = settings.report_dir / yyyy / mm / dd / f"run-{run_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


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
    """
    核心资源路径解析函数，支持容器绝对路径重映射与新旧存储结构双向 fallback。
    """
    if not file_path:
        return Path("")

    path = Path(file_path)

    # 1. 重映射容器内的 /app/data 路径到本地实际物理数据路径
    path_text = str(file_path).replace("\\", "/")
    app_data_prefix = "/app/data/"
    if path_text.startswith(app_data_prefix):
        path = settings.data_dir / path_text[len(app_data_prefix):]

    # 2. 如果当前文件已经存在，安全校验后直接返回
    if path.exists():
        if is_safe_path(path):
            return path.resolve()
        return path

    # 3. 如果文件物理不存在，尝试进行新旧路径转换查找
    filename = path.name
    run_id = None

    # 从父级目录名称提取 run_id
    if path.parent.name.startswith("run-"):
        try:
            run_id = int(path.parent.name[4:])
        except ValueError:
            pass
    elif path.parent.name.isdigit():
        run_id = int(path.parent.name)

    if run_id is not None:
        is_screenshot = "screenshots" in path_text
        is_report = "reports" in path_text

        if is_screenshot:
            # A. 尝试旧格式 fallback: screenshots/{run_id}/filename
            legacy_path = settings.screenshot_dir / str(run_id) / filename
            if legacy_path.exists() and is_safe_path(legacy_path):
                return legacy_path.resolve()

            # B. 尝试新格式 fallback: screenshots/YYYY/MM/DD/run-{run_id}/filename
            yyyy, mm, dd = get_run_date(run_id)
            new_path = settings.screenshot_dir / yyyy / mm / dd / f"run-{run_id}" / filename
            if new_path.exists() and is_safe_path(new_path):
                return new_path.resolve()

        elif is_report:
            # A. 尝试旧格式 fallback: reports/{run_id}/filename
            legacy_path = settings.report_dir / str(run_id) / filename
            if legacy_path.exists() and is_safe_path(legacy_path):
                return legacy_path.resolve()

            # B. 尝试新格式 fallback: reports/YYYY/MM/DD/run-{run_id}/filename
            yyyy, mm, dd = get_run_date(run_id)
            new_path = settings.report_dir / yyyy / mm / dd / f"run-{run_id}" / filename
            if new_path.exists() and is_safe_path(new_path):
                return new_path.resolve()

    return path
