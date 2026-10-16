from __future__ import annotations

import logging
import os
import json
import hashlib
import sqlite3
import shutil
import re
from contextvars import ContextVar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from app.settings import settings
from app.db import connect

logger = logging.getLogger("app.cleanup")
_preview_items: ContextVar[list[dict[str, Any]] | None] = ContextVar("cleanup_preview_items", default=None)


def _add_preview_item(item: dict[str, Any]) -> None:
    items = _preview_items.get()
    if items is not None:
        items.append(item)


def preview_cleanup(
    *, scopes: list[str], older_than_days: int | None = None,
    target_month: str | None = None, page: int = 1, page_size: int = 20,
) -> dict[str, Any]:
    """列出清理候选文件和记录，不执行删除，也不写清理历史。"""
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("请先选择清理内容")
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("分页参数无效")
    items: list[dict[str, Any]] = []
    token = _preview_items.set(items)
    try:
        result = run_cleanup(
            mode="manual", dry_run=True, target_month=target_month,
            scopes=scopes, older_than_days=older_than_days,
        )
    finally:
        _preview_items.reset(token)
    if result["status"] != "success":
        return {**result, "items": [], "total_items": 0, "page": page, "page_size": page_size}
    unique = {(item["kind"], item["path"]): item for item in items}
    ordered = sorted(unique.values(), key=lambda item: (item["kind"], item["path"]))
    fingerprint_source = {
        "scopes": sorted(scopes), "older_than_days": older_than_days,
        "target_month": target_month, "items": ordered,
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    offset = (page - 1) * page_size
    return {
        **result, "items": ordered[offset:offset + page_size],
        "total_items": len(ordered), "page": page, "page_size": page_size,
        "fingerprint": fingerprint,
    }


def get_dir_size(path: Path) -> int:
    """递归计算目录下所有文件的大小之和"""
    if not path.exists():
        return 0
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_dir_size(Path(entry.path))
    except Exception:
        pass
    return total


def get_storage_usage() -> dict[str, Any]:
    """获取 data 及其子目录的存储空间占用统计"""
    settings.ensure_dirs()
    periodic_dir = settings.data_dir / "periodic-reports"
    periodic_dir.mkdir(parents=True, exist_ok=True)

    db_size = settings.db_path.stat().st_size if settings.db_path.exists() else 0
    month_dirs = [path for path in settings.data_dir.glob("????/??") if path.is_dir()]
    day_dirs = [path for path in settings.data_dir.glob("????/??/??") if path.is_dir()]
    screenshots_size = get_dir_size(settings.screenshot_dir) + sum(
        file.stat().st_size for path in day_dirs for file in path.glob("*.png") if file.is_file()
    )
    reports_size = get_dir_size(settings.report_dir) + sum(
        file.stat().st_size for path in day_dirs for file in path.glob("*.docx") if file.is_file()
    )
    logs_size = get_dir_size(settings.log_dir)
    browser_state_size = get_dir_size(settings.browser_state_dir)
    periodic_reports_size = get_dir_size(periodic_dir) + sum(
        file.stat().st_size for path in month_dirs for file in path.glob("*.zip") if file.is_file()
    )
    total_size = get_dir_size(settings.data_dir)

    return {
        "total_bytes": total_size,
        "screenshots_bytes": screenshots_size,
        "reports_bytes": reports_size,
        "logs_bytes": logs_size,
        "browser_state_bytes": browser_state_size,
        "periodic_reports_bytes": periodic_reports_size,
        "sqlite_db_bytes": db_size,
        # 清理预估需要遍历历史记录和文件，交给显式的预估接口执行。
        "estimated_cleanup_bytes": None,
        "estimated_cleanup_files": None,
    }


def is_safe_path(path: Path | str) -> bool:
    """安全路径校验，确保操作的文件/文件夹在 data 目录之下，避免路径穿越"""
    try:
        resolved = Path(path).resolve()
        data_dir_resolved = settings.data_dir.resolve()
        return data_dir_resolved in resolved.parents or resolved == data_dir_resolved
    except Exception:
        return False


def _record_delete_error(errors: list[str] | None, message: str) -> None:
    logger.error(message)
    if errors is not None:
        errors.append(message)


def safe_delete_file(file_path: Path, dry_run: bool = False, errors: list[str] | None = None) -> tuple[int, int]:
    """安全删除单个物理文件，返回 (被删除文件数, 释放空间字节数)"""
    if not file_path.exists() or not file_path.is_file():
        return 0, 0
    if not is_safe_path(file_path):
        _record_delete_error(errors, f"安全拦截：文件不在 data 目录内: {file_path}")
        return 0, 0
    try:
        stat = file_path.stat()
        size = stat.st_size
        if dry_run and _preview_items.get() is not None:
            resolved = file_path.resolve()
            suffix = resolved.suffix.lower()
            kind = {
                ".png": "screenshot", ".docx": "report", ".zip": "periodic_file",
            }.get(suffix, "log" if resolved.is_relative_to(settings.log_dir.resolve()) else "file")
            _add_preview_item({
                "kind": kind, "name": resolved.name,
                "path": resolved.relative_to(settings.data_dir.resolve()).as_posix(),
                "date": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": size, "version": stat.st_mtime_ns,
            })
        if not dry_run:
            file_path.unlink()
        return 1, size
    except Exception as e:
        _record_delete_error(errors, f"删除物理文件 {file_path} 失败: {e}")
        return 0, 0


def safe_delete_dir(dir_path: Path, dry_run: bool = False, errors: list[str] | None = None) -> tuple[int, int]:
    """安全递归删除整个目录，返回 (被删除文件数, 释放空间字节数)"""
    if not dir_path.exists() or not dir_path.is_dir():
        return 0, 0
    if not is_safe_path(dir_path):
        _record_delete_error(errors, f"安全拦截：目录不在 data 目录内: {dir_path}")
        return 0, 0

    files_deleted = 0
    bytes_deleted = 0
    try:
        for root, dirs, files in os.walk(
            dir_path, topdown=False,
            onerror=lambda exc: _record_delete_error(errors, f"遍历目录 {dir_path} 失败: {exc}"),
        ):
            for file in files:
                f_path = Path(root) / file
                f_count, f_bytes = safe_delete_file(f_path, dry_run, errors)
                files_deleted += f_count
                bytes_deleted += f_bytes
            for d in dirs:
                d_path = Path(root) / d
                if is_safe_path(d_path):
                    try:
                        if not dry_run:
                            d_path.rmdir()
                    except Exception as e:
                        _record_delete_error(errors, f"删除子目录 {d_path} 失败: {e}")
                else:
                    _record_delete_error(errors, f"安全拦截：目录不在 data 目录内: {d_path}")
        if not dry_run:
            dir_path.rmdir()
    except Exception as e:
        _record_delete_error(errors, f"删除物理目录 {dir_path} 失败: {e}")
    return files_deleted, bytes_deleted


def parse_db_time(time_str: str) -> datetime:
    """兼容各种格式解析数据库时间字符串，统一转换到本地时区（支持 naive 与 timezone-aware 的混合比对）"""
    if not time_str:
        return datetime.min
    from zoneinfo import ZoneInfo
    from app.settings import settings
    local_tz = ZoneInfo(settings.default_timezone)

    # 1. 优先使用 fromisoformat 解析带时区偏移的 ISO-8601 格式
    try:
        dt = datetime.fromisoformat(time_str)
        if dt.tzinfo is not None:
            return dt.astimezone(local_tz).replace(tzinfo=None)
        return dt
    except ValueError:
        pass

    # 2. 备用原本的截取解析
    time_str = time_str.replace("T", " ")
    try:
        return datetime.strptime(time_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(time_str[:10], "%Y-%m-%d")
        except ValueError:
            return datetime.min


def estimate_cleanup(target_month: str | None = None) -> dict[str, Any]:
    """预估可清理空间，不真实发生物理删除，不落库运行历史"""
    return run_cleanup(mode="dry_run", dry_run=True, target_month=target_month)


def run_cleanup(
    mode: str = "manual", dry_run: bool = False, target_month: str | None = None,
    scopes: list[str] | None = None, older_than_days: int | None = None,
) -> dict[str, Any]:
    """
    一键清理/预估主入口
    """
    if target_month is not None:
        if not isinstance(target_month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", target_month):
            raise ValueError("月份格式应为 YYYY-MM")
        if target_month >= datetime.now(ZoneInfo(settings.default_timezone)).strftime("%Y-%m"):
            raise ValueError("只能清理已结束的月份")
    selected_scopes: set[str] | None = None
    if scopes is not None:
        if mode != "manual" or not isinstance(scopes, list) or not scopes or any(
            not isinstance(scope, str) or scope not in {"inspection", "periodic", "logs"}
            for scope in scopes
        ):
            raise ValueError("清理内容无效")
        selected_scopes = set(scopes)
        if target_month is None and (type(older_than_days) is not int or older_than_days not in {7, 30, 90, 180, 365}):
            raise ValueError("清理时间无效")
        if target_month is not None and older_than_days is not None:
            raise ValueError("指定月份时不能同时设置保留天数")
    elif older_than_days is not None:
        raise ValueError("请先选择清理内容")

    settings.ensure_dirs()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. 查询清理配置
    with connect() as conn:
        row = conn.execute("SELECT * FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
        cfg = dict(row) if row else {
            "enabled": 1,
            "allow_manual_cleanup": 1,
            "periodic_sent_retention_days": 90,
            "periodic_failed_retention_days": 90,
            "screenshot_retention_days": 90,
            "report_retention_days": 90,
            "run_record_retention_days": 90,
            "log_retention_days": 90,
            "browser_state_retention_days": 90,
            "browser_state_cleanup_enabled": 0,
            "protect_recent_days": 3,
        }

    now = datetime.now()
    # 手动选择了明确的时间范围时，以用户所选天数为准；仍保留 1 天安全缓冲。
    protect_days = 1 if selected_scopes is not None else int(cfg.get("protect_recent_days") or 3)
    protect_date = now - timedelta(days=protect_days)

    results = {
        "deleted_files_count": 0,
        "deleted_bytes": 0,
        "deleted_screenshots_count": 0,
        "deleted_reports_count": 0,
        "deleted_periodic_archives_count": 0,
        "deleted_logs_count": 0,
        "deleted_browser_state_count": 0,
        "deleted_run_records_count": 0,
        "deleted_periodic_run_records_count": 0,
        "selected_scopes": sorted(selected_scopes) if selected_scopes is not None else None,
        "older_than_days": older_than_days,
        "target_month": target_month,
        "errors": []
    }

    try:
        # A. 清理周期报告压缩包
        sent_days = older_than_days if selected_scopes is not None else int(cfg.get("periodic_sent_retention_days") or 90)
        failed_days = older_than_days if selected_scopes is not None else int(cfg.get("periodic_failed_retention_days") or 90)
        if selected_scopes is None or "periodic" in selected_scopes:
            _cleanup_periodic_archives(sent_days, failed_days, protect_date, dry_run, results, target_month=target_month)

        # B. 清理巡检截图
        sc_days = older_than_days if selected_scopes is not None else int(cfg.get("screenshot_retention_days") or 90)
        if selected_scopes is None or "inspection" in selected_scopes:
            _cleanup_screenshot_artifacts(sc_days, protect_date, dry_run, results, target_month=target_month)

        # C. 清理普通 Word 报告
        rep_days = older_than_days if selected_scopes is not None else int(cfg.get("report_retention_days") or 90)
        if selected_scopes is None or "inspection" in selected_scopes:
            _cleanup_report_artifacts(rep_days, protect_date, dry_run, results, target_month=target_month)

        # 按月清理仅处理页面明确列出的截图、报告、运行记录和压缩包。
        if (not target_month and selected_scopes is None) or (selected_scopes is not None and "logs" in selected_scopes):
            log_days = older_than_days if selected_scopes is not None else int(cfg.get("log_retention_days") or 90)
            _cleanup_logs(log_days, protect_date, dry_run, results, target_month=target_month)

        if not target_month and selected_scopes is None:
            bs_days = int(cfg.get("browser_state_retention_days") or 90)
            bs_enabled = int(cfg.get("browser_state_cleanup_enabled") or 0)
            _cleanup_browser_state(bs_days, protect_date, bs_enabled, dry_run, results)

        # F. 清理孤儿文件/目录 (仅在全局自动/常规手动清理时进行，指定月份清理时不执行)
        if not target_month and (selected_scopes is None or selected_scopes & {"inspection", "periodic"}):
            _cleanup_orphan_files(
                protect_date, dry_run, results,
                screenshot_retention_days=sc_days,
                report_retention_days=rep_days,
                periodic_retention_days=max(sent_days, failed_days),
                allowed_scopes=selected_scopes,
            )

        # G. 文件清理有错误时保留数据库引用，便于下次重试和追查失败文件。
        if not results["errors"] and (selected_scopes is None or "inspection" in selected_scopes):
            rec_days = older_than_days if selected_scopes is not None else int(cfg.get("run_record_retention_days") or 90)
            _cleanup_old_run_records(rec_days, protect_date, dry_run, results, target_month=target_month)

        # H. 按月预估和实际清理都统计周期报告运行记录。
        if target_month and not results["errors"] and (selected_scopes is None or "periodic" in selected_scopes):
            try:
                with connect() as conn:
                    if dry_run:
                        if _preview_items.get() is not None:
                            rows = conn.execute(
                                "SELECT id, started_at FROM periodic_report_runs WHERE started_at LIKE ?",
                                (f"{target_month}%",),
                            ).fetchall()
                            results["deleted_periodic_run_records_count"] = len(rows)
                            for row in rows:
                                _add_preview_item({
                                    "kind": "periodic_record", "name": f"周期报告记录 #{row['id']}",
                                    "path": f"periodic_record/{row['id']}", "date": row["started_at"] or "",
                                    "size": None,
                                })
                        else:
                            row = conn.execute(
                                "SELECT COUNT(*) FROM periodic_report_runs WHERE started_at LIKE ?",
                                (f"{target_month}%",),
                            ).fetchone()
                            results["deleted_periodic_run_records_count"] = int(row[0]) if row else 0
                    else:
                        cursor = conn.execute(
                            "DELETE FROM periodic_report_runs WHERE started_at LIKE ?",
                            (f"{target_month}%",),
                        )
                        results["deleted_periodic_run_records_count"] = max(0, cursor.rowcount)
                        logger.info(f"清理数据库中属于 {target_month} 的周期汇总历史记录完成")
            except Exception as pe_exc:
                results["errors"].append(f"删除周期历史记录失败: {pe_exc}")
        elif selected_scopes is not None and "periodic" in selected_scopes and not results["errors"]:
            _cleanup_old_periodic_run_records(older_than_days, protect_date, dry_run, results)

    except Exception as e:
        results["errors"].append(f"清理核心发生未捕获异常: {e}")

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "success" if not results["errors"] else (
        "partial_success" if results["deleted_files_count"] or results["deleted_run_records_count"] or results["deleted_periodic_run_records_count"] else "failed"
    )
    err_str = "; ".join(results["errors"])

    # 仅非预估模式时落库清理历史记录
    if not dry_run:
        try:
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO storage_cleanup_runs (
                        mode, status, started_at, finished_at,
                        deleted_files_count, deleted_bytes,
                        deleted_screenshots_count, deleted_reports_count,
                        deleted_periodic_archives_count, deleted_logs_count,
                        deleted_browser_state_count, deleted_run_records_count,
                        error_summary, detail_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"manual_{target_month}" if target_month else mode, status, started_at, finished_at,
                        results["deleted_files_count"], results["deleted_bytes"],
                        results["deleted_screenshots_count"], results["deleted_reports_count"],
                        results["deleted_periodic_archives_count"], results["deleted_logs_count"],
                        results["deleted_browser_state_count"], results["deleted_run_records_count"],
                        err_str, json.dumps(results, ensure_ascii=False)
                    )
                )
        except Exception as e:
            message = f"写入清理历史记录失败: {e}"
            results["errors"].append(message)
            logger.error(message)
            status = "failed" if results["deleted_files_count"] == 0 and results["deleted_run_records_count"] == 0 and results["deleted_periodic_run_records_count"] == 0 else "partial_success"
            err_str = "; ".join(results["errors"])

    return {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "deleted_files_count": results["deleted_files_count"],
        "deleted_bytes": results["deleted_bytes"],
        "deleted_screenshots_count": results["deleted_screenshots_count"],
        "deleted_reports_count": results["deleted_reports_count"],
        "deleted_periodic_archives_count": results["deleted_periodic_archives_count"],
        "deleted_logs_count": results["deleted_logs_count"],
        "deleted_browser_state_count": results["deleted_browser_state_count"],
        "deleted_run_records_count": results["deleted_run_records_count"],
        "deleted_periodic_run_records_count": results["deleted_periodic_run_records_count"],
        "error_summary": err_str
    }


def _cleanup_periodic_archives(sent_days: int, failed_days: int, protect_date: datetime, dry_run: bool, results: dict[str, Any], target_month: str | None = None) -> None:
    """清理周期报告打包的 Zip 文件"""
    now = datetime.now()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, zip_path, mail_status, finished_at, started_at FROM periodic_report_runs WHERE zip_path != '' AND zip_path IS NOT NULL"
        ).fetchall()

    for row in rows:
        run = dict(row)
        z_path = Path(run["zip_path"])
        if not z_path.exists():
            continue

        if target_month:
            if not (run["started_at"] and run["started_at"].startswith(target_month)):
                continue
        else:
            ref_time = parse_db_time(run["finished_at"] or run["started_at"])
            if ref_time >= protect_date:
                continue

            mail_status = str(run["mail_status"] or "").strip().lower()
            is_success = mail_status in ("success", "sent") or mail_status.startswith("skipped")
            
            limit_days = sent_days if is_success else failed_days
            limit_date = now - timedelta(days=limit_days)

            if ref_time >= limit_date:
                continue

        f_count, f_bytes = safe_delete_file(z_path, dry_run, results["errors"])
        results["deleted_files_count"] += f_count
        results["deleted_bytes"] += f_bytes
        results["deleted_periodic_archives_count"] += f_count


def _cleanup_screenshot_artifacts(retention_days: int | None, protect_date: datetime, dry_run: bool, results: dict[str, Any], target_month: str | None = None) -> None:
    """物理清理过期的巡检截图子目录"""
    if target_month is None and retention_days is None:
        raise ValueError("清理时间无效")
    limit_date = datetime.now() - timedelta(days=retention_days) if retention_days is not None else None

    with connect() as conn:
        rows = conn.execute("SELECT id, started_at, finished_at FROM run_records").fetchall()

    for row in rows:
        run = dict(row)
        if target_month:
            if not (run["started_at"] and run["started_at"].startswith(target_month)):
                continue
        else:
            ref_time = parse_db_time(run["finished_at"] or run["started_at"])
            if ref_time >= protect_date or ref_time >= limit_date:
                continue

        run_id = run["id"]
        from app.storage_paths import get_run_date, resolve_artifact_path
        yyyy, mm, dd = get_run_date(run_id)
        current_dir = settings.data_dir / yyyy / mm / dd
        with connect() as conn:
            current_files = conn.execute("SELECT file_path FROM screenshot_results WHERE run_id = ?", (run_id,)).fetchall()
        for file_row in current_files:
            if not file_row[0]:
                continue
            file_path = resolve_artifact_path(file_row[0])
            if file_path.parent == current_dir.resolve():
                f_count, f_bytes = safe_delete_file(file_path, dry_run, results["errors"])
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_screenshots_count"] += f_count
        # A. 清理新日期目录格式: YYYY/MM/DD/run-{id}
        try:
            new_dir = settings.screenshot_dir / yyyy / mm / dd / f"run-{run_id}"
            if new_dir.exists():
                f_count, f_bytes = safe_delete_dir(new_dir, dry_run, results["errors"])
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_screenshots_count"] += f_count
        except Exception as e:
            results["errors"].append(f"清理截图目录 {new_dir} 失败: {e}")

        # B. 清理旧格式目录: screenshots/{id}
        legacy_dir = settings.screenshot_dir / str(run_id)
        if legacy_dir.exists():
            f_count, f_bytes = safe_delete_dir(legacy_dir, dry_run, results["errors"])
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_screenshots_count"] += f_count


def _cleanup_report_artifacts(retention_days: int | None, protect_date: datetime, dry_run: bool, results: dict[str, Any], target_month: str | None = None) -> None:
    """物理清理过期的巡检 Word 报告子目录"""
    if target_month is None and retention_days is None:
        raise ValueError("清理时间无效")
    limit_date = datetime.now() - timedelta(days=retention_days) if retention_days is not None else None

    with connect() as conn:
        rows = conn.execute("SELECT id, started_at, finished_at, report_path FROM run_records").fetchall()

    from app.storage_paths import get_run_date, resolve_artifact_path

    def eligible(run: dict[str, Any]) -> bool:
        if target_month:
            return bool(run["started_at"] and run["started_at"].startswith(target_month))
        ref_time = parse_db_time(run["finished_at"] or run["started_at"])
        return ref_time < protect_date and ref_time < limit_date

    protected_paths = {
        resolve_artifact_path(run["report_path"]).resolve()
        for row in rows if (run := dict(row)).get("report_path") and not eligible(run)
    }
    counted_paths: set[Path] = set()

    for row in rows:
        run = dict(row)
        if not eligible(run):
            continue

        run_id = run["id"]
        yyyy, mm, dd = get_run_date(run_id)
        current_dir = settings.data_dir / yyyy / mm / dd
        if run.get("report_path"):
            file_path = resolve_artifact_path(run["report_path"])
            if file_path.parent == current_dir.resolve() and file_path not in protected_paths and file_path not in counted_paths:
                f_count, f_bytes = safe_delete_file(file_path, dry_run, results["errors"])
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_reports_count"] += f_count
                counted_paths.add(file_path)
        # A. 清理新日期目录格式: YYYY/MM/DD/run-{id}
        try:
            new_dir = settings.report_dir / yyyy / mm / dd / f"run-{run_id}"
            if new_dir.exists():
                f_count, f_bytes = safe_delete_dir(new_dir, dry_run, results["errors"])
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_reports_count"] += f_count
        except Exception as e:
            results["errors"].append(f"清理报告目录 {new_dir} 失败: {e}")

        # B. 清理旧格式目录: reports/{id}
        legacy_dir = settings.report_dir / str(run_id)
        if legacy_dir.exists():
            f_count, f_bytes = safe_delete_dir(legacy_dir, dry_run, results["errors"])
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_reports_count"] += f_count


def _cleanup_logs(retention_days: int | None, protect_date: datetime, dry_run: bool, results: dict[str, Any], target_month: str | None = None) -> None:
    """清理 logs 目录下超时的历史日志文件"""
    if not settings.log_dir.exists():
        return
    if target_month is None and retention_days is None:
        raise ValueError("清理时间无效")
    limit_date = datetime.now() - timedelta(days=retention_days) if retention_days is not None else None

    for entry in os.scandir(settings.log_dir):
        if not entry.is_file():
            continue
        f_path = Path(entry.path)
        mtime = datetime.fromtimestamp(entry.stat().st_mtime)

        if target_month:
            if mtime.strftime("%Y-%m") != target_month:
                continue
        else:
            if mtime >= protect_date or mtime >= limit_date:
                continue

        try:
            f_count, f_bytes = safe_delete_file(f_path, dry_run, results["errors"])
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_logs_count"] += f_count
        except Exception as e:
            results["errors"].append(f"日志文件 {f_path.name} 清理异常: {e}")


def _cleanup_browser_state(retention_days: int, protect_date: datetime, enabled: int, dry_run: bool, results: dict[str, Any], target_month: str | None = None) -> None:
    """清理过期的浏览器会话缓存状态文件 (JSON)"""
    if not enabled or not settings.browser_state_dir.exists():
        return
    now = datetime.now()
    limit_date = now - timedelta(days=retention_days)

    for entry in os.scandir(settings.browser_state_dir):
        if not entry.is_file() or not entry.name.endswith(".json"):
            continue
        f_path = Path(entry.path)
        mtime = datetime.fromtimestamp(entry.stat().st_mtime)

        if target_month:
            if mtime.strftime("%Y-%m") != target_month:
                continue
        else:
            if mtime >= protect_date or mtime >= limit_date:
                continue

        f_count, f_bytes = safe_delete_file(f_path, dry_run, results["errors"])
        results["deleted_files_count"] += f_count
        results["deleted_bytes"] += f_bytes
        results["deleted_browser_state_count"] += f_count


def _clean_empty_parent_dirs(root_dir: Path, dry_run: bool) -> None:
    """安全地递归删除指定根目录下的空子文件夹"""
    if not root_dir.exists() or not root_dir.is_dir():
        return
    for root, dirs, files in os.walk(root_dir, topdown=False):
        for d in dirs:
            d_path = Path(root) / d
            try:
                if is_safe_path(d_path) and d_path.is_dir() and not any(os.scandir(d_path)):
                    if not dry_run:
                        d_path.rmdir()
            except Exception:
                pass


def _cleanup_orphan_files(
    protect_date: datetime, dry_run: bool, results: dict[str, Any], *,
    screenshot_retention_days: int, report_retention_days: int, periodic_retention_days: int,
    allowed_scopes: set[str] | None = None,
) -> None:
    """清理与数据库记录脱钩的孤儿文件/文件夹"""
    allowed_scopes = allowed_scopes or {"inspection", "periodic"}
    now = datetime.now()
    cutoffs = {
        ".png": min(protect_date, now - timedelta(days=screenshot_retention_days)),
        ".docx": min(protect_date, now - timedelta(days=report_retention_days)),
        ".zip": min(protect_date, now - timedelta(days=periodic_retention_days)),
    }
    with connect() as conn:
        active_ids = {row[0] for row in conn.execute("SELECT id FROM run_records").fetchall()}

    # 日期目录没有运行 ID，通过数据库保存的文件路径识别仍被引用的产物。
    from app.storage_paths import resolve_artifact_path
    with connect() as conn:
        stored_paths = [row[0] for row in conn.execute("SELECT file_path FROM screenshot_results WHERE file_path != ''")]
        stored_paths += [row[0] for row in conn.execute("SELECT report_path FROM run_records WHERE report_path != ''")]
        stored_paths += [row[0] for row in conn.execute("SELECT zip_path FROM periodic_report_runs WHERE zip_path != ''")]
    referenced_files = {resolve_artifact_path(path).resolve() for path in stored_paths if path}

    def clean_unreferenced(directory: Path, extensions: dict[str, str]) -> None:
        for file_path in directory.iterdir():
            if not file_path.is_file():
                continue
            counter = extensions.get(file_path.suffix.lower())
            if not counter or file_path.resolve() in referenced_files:
                continue
            if datetime.fromtimestamp(file_path.stat().st_mtime) >= cutoffs[file_path.suffix.lower()]:
                continue
            f_count, f_bytes = safe_delete_file(file_path, dry_run, results["errors"])
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results[counter] += f_count

    def clean_orphan_directory(path: Path, counter: str, cutoff: datetime) -> None:
        if datetime.fromtimestamp(path.stat().st_mtime) >= cutoff:
            return
        if any(
            datetime.fromtimestamp(child.stat().st_mtime) >= cutoff
            for child in path.rglob("*") if child.is_file()
        ):
            return
        f_count, f_bytes = safe_delete_dir(path, dry_run, results["errors"])
        results["deleted_files_count"] += f_count
        results["deleted_bytes"] += f_bytes
        results[counter] += f_count

    for month_dir in settings.data_dir.glob("????/??"):
        if not month_dir.is_dir():
            continue
        if "periodic" in allowed_scopes:
            clean_unreferenced(month_dir, {".zip": "deleted_periodic_archives_count"})
        for day_dir in month_dir.glob("??"):
            if not day_dir.is_dir():
                continue
            if "inspection" in allowed_scopes:
                clean_unreferenced(day_dir, {".png": "deleted_screenshots_count", ".docx": "deleted_reports_count"})
            if not dry_run and not any(day_dir.iterdir()):
                day_dir.rmdir()
        if not dry_run and not any(month_dir.iterdir()):
            month_dir.rmdir()
            if not any(month_dir.parent.iterdir()):
                month_dir.parent.rmdir()

    # 1. 扫描 screenshots 目录下的孤儿子文件夹
    if "inspection" in allowed_scopes and settings.screenshot_dir.exists():
        # A. 扫描旧版数字目录
        for entry in os.scandir(settings.screenshot_dir):
            if not entry.is_dir():
                continue
            dir_name = entry.name
            try:
                val = int(dir_name)
                # 排除年份目录 (如 2000 到 2100) 以防误删
                if 1900 <= val <= 2100:
                    continue
                run_id = val
            except ValueError:
                continue

            if run_id not in active_ids:
                clean_orphan_directory(Path(entry.path), "deleted_screenshots_count", cutoffs[".png"])

        # B. 扫描新版 YYYY/MM/DD/run-{id} 目录
        for entry_path in settings.screenshot_dir.glob("????/??/??/run-*"):
            if not entry_path.is_dir():
                continue
            dir_name = entry_path.name
            try:
                run_id = int(dir_name[4:])
            except ValueError:
                continue

            if run_id not in active_ids:
                clean_orphan_directory(entry_path, "deleted_screenshots_count", cutoffs[".png"])

        _clean_empty_parent_dirs(settings.screenshot_dir, dry_run)

    # 2. 扫描 reports 目录下的孤儿子文件夹
    if "inspection" in allowed_scopes and settings.report_dir.exists():
        # A. 扫描旧版数字目录
        for entry in os.scandir(settings.report_dir):
            if not entry.is_dir():
                continue
            dir_name = entry.name
            try:
                val = int(dir_name)
                if 1900 <= val <= 2100:
                    continue
                run_id = val
            except ValueError:
                continue

            if run_id not in active_ids:
                clean_orphan_directory(Path(entry.path), "deleted_reports_count", cutoffs[".docx"])

        # B. 扫描新版 YYYY/MM/DD/run-{id} 目录
        for entry_path in settings.report_dir.glob("????/??/??/run-*"):
            if not entry_path.is_dir():
                continue
            dir_name = entry_path.name
            try:
                run_id = int(dir_name[4:])
            except ValueError:
                continue

            if run_id not in active_ids:
                clean_orphan_directory(entry_path, "deleted_reports_count", cutoffs[".docx"])

        _clean_empty_parent_dirs(settings.report_dir, dry_run)

    # 3. 扫描 periodic-reports 目录下的孤儿 ZIP 包
    periodic_dir = settings.data_dir / "periodic-reports"
    if "periodic" in allowed_scopes and periodic_dir.exists():
        for entry in os.scandir(periodic_dir):
            if not entry.is_file() or not entry.name.endswith(".zip"):
                continue
            file_path = Path(entry.path)
            if file_path.resolve() in referenced_files:
                continue
            if datetime.fromtimestamp(entry.stat().st_mtime) >= cutoffs[".zip"]:
                continue
            f_count, f_bytes = safe_delete_file(file_path, dry_run, results["errors"])
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_periodic_archives_count"] += f_count


def _cleanup_old_run_records(retention_days: int | None, protect_date: datetime, dry_run: bool, results: dict[str, Any], target_month: str | None = None) -> None:
    """清理数据库中过期的 run_records 运行历史数据 (级联清空截图结果明细)"""
    if target_month is None and retention_days is None:
        raise ValueError("清理时间无效")
    limit_date = datetime.now() - timedelta(days=retention_days) if retention_days is not None else None

    with connect() as conn:
        rows = conn.execute("SELECT id, started_at, finished_at FROM run_records").fetchall()

    expired_ids = []
    for row in rows:
        run = dict(row)
        eligible = False
        if target_month:
            if run["started_at"] and run["started_at"].startswith(target_month):
                eligible = True
        else:
            ref_time = parse_db_time(run["finished_at"] or run["started_at"])
            if ref_time < protect_date and ref_time < limit_date:
                eligible = True
        if eligible:
            expired_ids.append(run["id"])
            if dry_run:
                _add_preview_item({
                    "kind": "run_record", "name": f"巡检记录 #{run['id']}",
                    "path": f"run_record/{run['id']}", "date": run["started_at"] or "",
                    "size": None,
                })

    if not expired_ids:
        return

    if dry_run:
        results["deleted_run_records_count"] += len(expired_ids)
        return

    # SQLite 级联删除由 PRAGMA foreign_keys = ON 实现，这里直接 DELETE 主表。
    try:
        with connect() as conn:
            placeholders = ", ".join("?" for _ in expired_ids)
            deleted_count = conn.execute(f"DELETE FROM run_records WHERE id IN ({placeholders})", expired_ids).rowcount
        results["deleted_run_records_count"] += deleted_count
        logger.info("清理数据库过期运行历史完成。删除了记录数: %s", deleted_count)
    except Exception as e:
        results["errors"].append(f"删除数据库过期记录失败: {e}")


def _cleanup_old_periodic_run_records(
    retention_days: int, protect_date: datetime, dry_run: bool, results: dict[str, Any],
) -> None:
    """手动按时间清理周期报告运行记录；文件删除出错时不会调用这里。"""
    limit_date = datetime.now() - timedelta(days=retention_days)
    with connect() as conn:
        rows = conn.execute("SELECT id, started_at, finished_at FROM periodic_report_runs").fetchall()
    expired_ids = []
    for row in rows:
        ref_time = parse_db_time(row["finished_at"] or row["started_at"])
        if ref_time < protect_date and ref_time < limit_date:
            expired_ids.append(row["id"])
            if dry_run:
                _add_preview_item({
                    "kind": "periodic_record", "name": f"周期报告记录 #{row['id']}",
                    "path": f"periodic_record/{row['id']}", "date": row["started_at"] or "",
                    "size": None,
                })
    if not expired_ids:
        return
    if dry_run:
        results["deleted_periodic_run_records_count"] += len(expired_ids)
        return
    try:
        with connect() as conn:
            placeholders = ", ".join("?" for _ in expired_ids)
            deleted_count = conn.execute(
                f"DELETE FROM periodic_report_runs WHERE id IN ({placeholders})", expired_ids,
            ).rowcount
        results["deleted_periodic_run_records_count"] += max(0, deleted_count)
    except Exception as e:
        results["errors"].append(f"删除周期报告记录失败: {e}")
