from __future__ import annotations

import logging
import os
import json
import sqlite3
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.settings import settings
from app.db import connect

logger = logging.getLogger("app.cleanup")


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
    screenshots_size = get_dir_size(settings.screenshot_dir)
    reports_size = get_dir_size(settings.report_dir)
    logs_size = get_dir_size(settings.log_dir)
    browser_state_size = get_dir_size(settings.browser_state_dir)
    periodic_reports_size = get_dir_size(periodic_dir)
    total_size = get_dir_size(settings.data_dir)

    # 预估清理大小
    est = estimate_cleanup()

    return {
        "total_bytes": total_size,
        "screenshots_bytes": screenshots_size,
        "reports_bytes": reports_size,
        "logs_bytes": logs_size,
        "browser_state_bytes": browser_state_size,
        "periodic_reports_bytes": periodic_reports_size,
        "sqlite_db_bytes": db_size,
        "estimated_cleanup_bytes": est["deleted_bytes"],
        "estimated_cleanup_files": est["deleted_files_count"],
    }


def is_safe_path(path: Path | str) -> bool:
    """安全路径校验，确保操作的文件/文件夹在 data 目录之下，避免路径穿越"""
    try:
        resolved = Path(path).resolve()
        data_dir_resolved = settings.data_dir.resolve()
        return data_dir_resolved in resolved.parents or resolved == data_dir_resolved
    except Exception:
        return False


def safe_delete_file(file_path: Path, dry_run: bool = False) -> tuple[int, int]:
    """安全删除单个物理文件，返回 (被删除文件数, 释放空间字节数)"""
    if not file_path.exists() or not file_path.is_file():
        return 0, 0
    if not is_safe_path(file_path):
        logger.warning(f"安全拦截：试图删除非 data 目录内的文件: {file_path}")
        return 0, 0
    try:
        size = file_path.stat().st_size
        if not dry_run:
            file_path.unlink()
        return 1, size
    except Exception as e:
        logger.error(f"删除物理文件 {file_path} 失败: {e}")
        return 0, 0


def safe_delete_dir(dir_path: Path, dry_run: bool = False) -> tuple[int, int]:
    """安全递归删除整个目录，返回 (被删除文件数, 释放空间字节数)"""
    if not dir_path.exists() or not dir_path.is_dir():
        return 0, 0
    if not is_safe_path(dir_path):
        logger.warning(f"安全拦截：试图删除非 data 目录内的文件夹: {dir_path}")
        return 0, 0

    files_deleted = 0
    bytes_deleted = 0
    try:
        for root, dirs, files in os.walk(dir_path, topdown=False):
            for file in files:
                f_path = Path(root) / file
                if is_safe_path(f_path):
                    try:
                        bytes_deleted += f_path.stat().st_size
                        if not dry_run:
                            f_path.unlink()
                        files_deleted += 1
                    except Exception:
                        pass
            for d in dirs:
                d_path = Path(root) / d
                if is_safe_path(d_path):
                    try:
                        if not dry_run:
                            d_path.rmdir()
                    except Exception:
                        pass
        if not dry_run:
            dir_path.rmdir()
    except Exception as e:
        logger.error(f"删除物理目录 {dir_path} 失败: {e}")
    return files_deleted, bytes_deleted


def parse_db_time(time_str: str) -> datetime:
    """兼容各种格式解析数据库时间字符串"""
    if not time_str:
        return datetime.min
    time_str = time_str.replace("T", " ")
    try:
        return datetime.strptime(time_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(time_str[:10], "%Y-%m-%d")
        except ValueError:
            return datetime.min


def estimate_cleanup() -> dict[str, Any]:
    """预估可清理空间，不真实发生物理删除，不落库运行历史"""
    return run_cleanup(mode="dry_run", dry_run=True)


def run_cleanup(mode: str = "manual", dry_run: bool = False) -> dict[str, Any]:
    """
    一键清理/预估主入口
    """
    settings.ensure_dirs()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. 查询清理配置
    with connect() as conn:
        row = conn.execute("SELECT * FROM storage_cleanup_settings ORDER BY id DESC LIMIT 1").fetchone()
        cfg = dict(row) if row else {
            "enabled": 1,
            "allow_manual_cleanup": 1,
            "periodic_sent_retention_days": 3,
            "periodic_failed_retention_days": 30,
            "screenshot_retention_days": 7,
            "report_retention_days": 30,
            "run_record_retention_days": 90,
            "log_retention_days": 14,
            "browser_state_retention_days": 30,
            "browser_state_cleanup_enabled": 0,
            "protect_recent_days": 3,
        }

    if mode == "manual" and not int(cfg.get("allow_manual_cleanup") or 1) and not dry_run:
        return {
            "status": "failed",
            "error_summary": "系统策略限制：已关闭手动清理功能。",
            "deleted_files_count": 0,
            "deleted_bytes": 0,
        }

    now = datetime.now()
    protect_days = int(cfg.get("protect_recent_days") or 3)
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
        "errors": []
    }

    try:
        # A. 清理周期报告压缩包
        sent_days = int(cfg.get("periodic_sent_retention_days") or 3)
        failed_days = int(cfg.get("periodic_failed_retention_days") or 30)
        _cleanup_periodic_archives(sent_days, failed_days, protect_date, dry_run, results)

        # B. 清理巡检截图
        sc_days = int(cfg.get("screenshot_retention_days") or 7)
        _cleanup_screenshot_artifacts(sc_days, protect_date, dry_run, results)

        # C. 清理普通 Word 报告
        rep_days = int(cfg.get("report_retention_days") or 30)
        _cleanup_report_artifacts(rep_days, protect_date, dry_run, results)

        # D. 清理日志文件
        log_days = int(cfg.get("log_retention_days") or 14)
        _cleanup_logs(log_days, protect_date, dry_run, results)

        # E. 清理浏览器登录状态
        bs_days = int(cfg.get("browser_state_retention_days") or 30)
        bs_enabled = int(cfg.get("browser_state_cleanup_enabled") or 0)
        _cleanup_browser_state(bs_days, protect_date, bs_enabled, dry_run, results)

        # F. 清理孤儿文件/目录
        _cleanup_orphan_files(protect_date, dry_run, results)

        # G. 清理旧运行记录（数据库清理，在最后执行以防前述目录遍历找不到 ID）
        rec_days = int(cfg.get("run_record_retention_days") or 90)
        _cleanup_old_run_records(rec_days, protect_date, dry_run, results)

    except Exception as e:
        results["errors"].append(f"清理核心发生未捕获异常: {e}")

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "success" if not results["errors"] else ("failed" if results["deleted_files_count"] == 0 else "partial_success")
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
                        mode, status, started_at, finished_at,
                        results["deleted_files_count"], results["deleted_bytes"],
                        results["deleted_screenshots_count"], results["deleted_reports_count"],
                        results["deleted_periodic_archives_count"], results["deleted_logs_count"],
                        results["deleted_browser_state_count"], results["deleted_run_records_count"],
                        err_str, json.dumps(results, ensure_ascii=False)
                    )
                )
        except Exception as e:
            logger.error(f"写入清理日志记录失败: {e}")

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
        "error_summary": err_str
    }


def _cleanup_periodic_archives(sent_days: int, failed_days: int, protect_date: datetime, dry_run: bool, results: dict[str, Any]) -> None:
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

        ref_time = parse_db_time(run["finished_at"] or run["started_at"])
        if ref_time >= protect_date:
            continue

        mail_status = str(run["mail_status"] or "").strip().lower()
        is_success = mail_status in ("success", "sent") or mail_status.startswith("skipped")
        
        limit_days = sent_days if is_success else failed_days
        limit_date = now - timedelta(days=limit_days)

        if ref_time < limit_date:
            f_count, f_bytes = safe_delete_file(z_path, dry_run)
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_periodic_archives_count"] += f_count


def _cleanup_screenshot_artifacts(retention_days: int, protect_date: datetime, dry_run: bool, results: dict[str, Any]) -> None:
    """物理清理过期的巡检截图子目录"""
    now = datetime.now()
    limit_date = now - timedelta(days=retention_days)

    with connect() as conn:
        rows = conn.execute("SELECT id, started_at, finished_at FROM run_records").fetchall()

    for row in rows:
        run = dict(row)
        ref_time = parse_db_time(run["finished_at"] or run["started_at"])
        if ref_time >= protect_date or ref_time >= limit_date:
            continue

        run_id = run["id"]
        # A. 清理新日期目录格式: YYYY/MM/DD/run-{id}
        from app.storage_paths import get_run_date
        yyyy, mm, dd = get_run_date(run_id)
        new_dir = settings.screenshot_dir / yyyy / mm / dd / f"run-{run_id}"
        if new_dir.exists():
            f_count, f_bytes = safe_delete_dir(new_dir, dry_run)
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_screenshots_count"] += f_count

        # B. 清理旧格式目录: screenshots/{id}
        legacy_dir = settings.screenshot_dir / str(run_id)
        if legacy_dir.exists():
            f_count, f_bytes = safe_delete_dir(legacy_dir, dry_run)
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_screenshots_count"] += f_count


def _cleanup_report_artifacts(retention_days: int, protect_date: datetime, dry_run: bool, results: dict[str, Any]) -> None:
    """物理清理过期的巡检 Word 报告子目录"""
    now = datetime.now()
    limit_date = now - timedelta(days=retention_days)

    with connect() as conn:
        rows = conn.execute("SELECT id, started_at, finished_at FROM run_records").fetchall()

    for row in rows:
        run = dict(row)
        ref_time = parse_db_time(run["finished_at"] or run["started_at"])
        if ref_time >= protect_date or ref_time >= limit_date:
            continue

        run_id = run["id"]
        # A. 清理新日期目录格式: YYYY/MM/DD/run-{id}
        from app.storage_paths import get_run_date
        yyyy, mm, dd = get_run_date(run_id)
        new_dir = settings.report_dir / yyyy / mm / dd / f"run-{run_id}"
        if new_dir.exists():
            f_count, f_bytes = safe_delete_dir(new_dir, dry_run)
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_reports_count"] += f_count

        # B. 清理旧格式目录: reports/{id}
        legacy_dir = settings.report_dir / str(run_id)
        if legacy_dir.exists():
            f_count, f_bytes = safe_delete_dir(legacy_dir, dry_run)
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_reports_count"] += f_count


def _cleanup_logs(retention_days: int, protect_date: datetime, dry_run: bool, results: dict[str, Any]) -> None:
    """清理 logs 目录下超时的历史日志文件"""
    if not settings.log_dir.exists():
        return
    now = datetime.now()
    limit_date = now - timedelta(days=retention_days)

    for entry in os.scandir(settings.log_dir):
        if not entry.is_file():
            continue
        f_path = Path(entry.path)
        mtime = datetime.fromtimestamp(entry.stat().st_mtime)

        if mtime >= protect_date or mtime >= limit_date:
            continue

        try:
            f_count, f_bytes = safe_delete_file(f_path, dry_run)
            results["deleted_files_count"] += f_count
            results["deleted_bytes"] += f_bytes
            results["deleted_logs_count"] += f_count
        except Exception as e:
            results["errors"].append(f"日志文件 {f_path.name} 清理异常: {e}")


def _cleanup_browser_state(retention_days: int, protect_date: datetime, enabled: int, dry_run: bool, results: dict[str, Any]) -> None:
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

        if mtime >= protect_date or mtime >= limit_date:
            continue

        f_count, f_bytes = safe_delete_file(f_path, dry_run)
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


def _cleanup_orphan_files(protect_date: datetime, dry_run: bool, results: dict[str, Any]) -> None:
    """清理与数据库记录脱钩的孤儿文件/文件夹"""
    with connect() as conn:
        active_ids = {row[0] for row in conn.execute("SELECT id FROM run_records").fetchall()}

    # 1. 扫描 screenshots 目录下的孤儿子文件夹
    if settings.screenshot_dir.exists():
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

            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime >= protect_date:
                continue

            if run_id not in active_ids:
                f_count, f_bytes = safe_delete_dir(Path(entry.path), dry_run)
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_screenshots_count"] += f_count

        # B. 扫描新版 YYYY/MM/DD/run-{id} 目录
        for entry_path in settings.screenshot_dir.glob("????/??/??/run-*"):
            if not entry_path.is_dir():
                continue
            dir_name = entry_path.name
            try:
                run_id = int(dir_name[4:])
            except ValueError:
                continue

            mtime = datetime.fromtimestamp(entry_path.stat().st_mtime)
            if mtime >= protect_date:
                continue

            if run_id not in active_ids:
                f_count, f_bytes = safe_delete_dir(entry_path, dry_run)
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_screenshots_count"] += f_count

        _clean_empty_parent_dirs(settings.screenshot_dir, dry_run)

    # 2. 扫描 reports 目录下的孤儿子文件夹
    if settings.report_dir.exists():
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

            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime >= protect_date:
                continue

            if run_id not in active_ids:
                f_count, f_bytes = safe_delete_dir(Path(entry.path), dry_run)
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_reports_count"] += f_count

        # B. 扫描新版 YYYY/MM/DD/run-{id} 目录
        for entry_path in settings.report_dir.glob("????/??/??/run-*"):
            if not entry_path.is_dir():
                continue
            dir_name = entry_path.name
            try:
                run_id = int(dir_name[4:])
            except ValueError:
                continue

            mtime = datetime.fromtimestamp(entry_path.stat().st_mtime)
            if mtime >= protect_date:
                continue

            if run_id not in active_ids:
                f_count, f_bytes = safe_delete_dir(entry_path, dry_run)
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_reports_count"] += f_count

        _clean_empty_parent_dirs(settings.report_dir, dry_run)

    # 3. 扫描 periodic-reports 目录下的孤儿 ZIP 包
    periodic_dir = settings.data_dir / "periodic-reports"
    if periodic_dir.exists():
        with connect() as conn:
            active_zips = {str(row[0]).strip() for row in conn.execute("SELECT zip_path FROM periodic_report_runs WHERE zip_path != ''").fetchall()}

        for entry in os.scandir(periodic_dir):
            if not entry.is_file() or not entry.name.endswith(".zip"):
                continue
            
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime >= protect_date:
                continue

            zip_full_path = str(Path(entry.path).resolve())
            
            # 判断数据库里有没有这条 zip_path 的记录
            matched = False
            for active_zip in active_zips:
                try:
                    if Path(active_zip).resolve() == Path(zip_full_path).resolve():
                        matched = True
                        break
                except Exception:
                    pass
            
            if not matched:
                f_count, f_bytes = safe_delete_file(Path(entry.path), dry_run)
                results["deleted_files_count"] += f_count
                results["deleted_bytes"] += f_bytes
                results["deleted_periodic_archives_count"] += f_count


def _cleanup_old_run_records(retention_days: int, protect_date: datetime, dry_run: bool, results: dict[str, Any]) -> None:
    """清理数据库中过期的 run_records 运行历史数据 (级联清空截图结果明细)"""
    now = datetime.now()
    limit_date = now - timedelta(days=retention_days)

    with connect() as conn:
        rows = conn.execute("SELECT id, started_at, finished_at FROM run_records").fetchall()

    expired_ids = []
    for row in rows:
        run = dict(row)
        ref_time = parse_db_time(run["finished_at"] or run["started_at"])
        if ref_time < protect_date and ref_time < limit_date:
            expired_ids.append(run["id"])

    if not expired_ids:
        return

    results["deleted_run_records_count"] += len(expired_ids)
    
    if not dry_run:
        # SQLite 级联删除由 PRAGMA foreign_keys = ON 实现，这里直接 DELETE 主表
        try:
            with connect() as conn:
                placeholders = ", ".join("?" for _ in expired_ids)
                conn.execute(f"DELETE FROM run_records WHERE id IN ({placeholders})", expired_ids)
                logger.info(f"清理数据库过期运行历史完成。删除了记录数: {len(expired_ids)}")
        except Exception as e:
            results["errors"].append(f"删除数据库过期记录失败: {e}")
