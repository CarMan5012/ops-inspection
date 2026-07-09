from __future__ import annotations

import sqlite3
from typing import Any

from app.db import connect
from app.utils import normalize_secret_for_storage


def list_jobs() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT j.*, m.name AS mail_profile_name
            FROM report_jobs j
            LEFT JOIN mail_profiles m ON m.id = j.mail_profile_id
            ORDER BY j.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_job(job_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT j.*, m.name AS mail_profile_name
            FROM report_jobs j
            LEFT JOIN mail_profiles m ON m.id = j.mail_profile_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    if "browser_scale_factor" not in data or data["browser_scale_factor"] is None:
        data["browser_scale_factor"] = 1.5
    return data

def save_job(data: dict[str, Any], job_id: int | None = None) -> int:
    data = dict(data)
    for secret_field in ("dingtalk_webhook", "dingtalk_secret"):
        if data.get(secret_field):
            data[secret_field] = normalize_secret_for_storage(str(data.get(secret_field) or ""))

    fields = (
        "name",
        "environment",
        "enabled",
        "cron_expression",
        "time_range_label",
        "report_title",
        "mail_profile_id",
        "send_mail",
        "browser_width",
        "browser_height",
        "browser_scale_factor",
        "headless",
        "schedule_mode",
        "schedule_label",
        "schedule_config",
        "send_mail_on_complete",
        "send_mail_on_error",
        "dingtalk_enabled",
        "dingtalk_webhook",
        "dingtalk_secret",
        "dingtalk_keyword",
    )
    values = [data.get(field) for field in fields]
    with connect() as conn:
        if job_id:
            assignments = ", ".join(f"{field} = ?" for field in fields)
            conn.execute(
                f"UPDATE report_jobs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*values, job_id),
            )
            return job_id
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO report_jobs ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)


def delete_job(job_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM report_jobs WHERE id = ?", (job_id,))


def list_screenshot_items(job_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, a.name AS auth_profile_name
            FROM screenshot_items s
            LEFT JOIN auth_profiles a ON a.id = s.auth_profile_id
            WHERE s.job_id = ?
            ORDER BY s.sort_order ASC, s.id ASC
            """,
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_screenshot_item(item_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, a.name AS auth_profile_name
            FROM screenshot_items s
            LEFT JOIN auth_profiles a ON a.id = s.auth_profile_id
            WHERE s.id = ?
            """,
            (item_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def save_screenshot_item(data: dict[str, Any], item_id: int | None = None) -> int:
    fields = (
        "job_id",
        "auth_profile_id",
        "name",
        "item_type",
        "url",
        "section",
        "capture_mode",
        "css_selector",
        "wait_selector",
        "wait_seconds",
        "timeout_seconds",
        "retry_count",
        "browser_width",
        "browser_height",
        "sort_order",
        "enabled",
        "real_browser_capture",
        "watermark_enabled",
        "watermark_text",
        "watermark_opacity",
        "watermark_font_size",
        "watermark_gap_x",
        "watermark_gap_y",
        "watermark_angle",
        "taskbar_enabled",
    )
    defaults = {
        "real_browser_capture": 1,
        "watermark_enabled": 0,
        "watermark_text": "",
        "watermark_opacity": 65,
        "watermark_font_size": 24,
        "watermark_gap_x": 140,
        "watermark_gap_y": 140,
        "watermark_angle": -45,
        "taskbar_enabled": 1,
    }
    values = [
        defaults[field] if field in defaults and data.get(field) is None else data.get(field)
        for field in fields
    ]
    with connect() as conn:
        if item_id:
            assignments = ", ".join(f"{field} = ?" for field in fields)
            conn.execute(
                f"UPDATE screenshot_items SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*values, item_id),
            )
            return item_id
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO screenshot_items ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)


def delete_screenshot_item(item_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM screenshot_items WHERE id = ?", (item_id,))


def list_auth_profiles() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM auth_profiles ORDER BY id ASC").fetchall()
    return [dict(row) for row in rows]


def get_auth_profile(auth_id: int | None) -> dict[str, Any] | None:
    if not auth_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM auth_profiles WHERE id = ?", (auth_id,)).fetchone()
    return dict(row) if row is not None else None


def save_auth_profile(data: dict[str, Any], auth_id: int | None = None) -> int:
    data = dict(data)
    if data.get("password"):
        data["password_secret"] = normalize_secret_for_storage(str(data.get("password") or ""))
    elif data.get("password_secret"):
        data["password_secret"] = normalize_secret_for_storage(str(data.get("password_secret") or ""))
    data["password"] = ""

    fields = (
        "name",
        "auth_type",
        "login_url",
        "username",
        "password",
        "username_source",
        "password_source",
        "username_value",
        "password_secret",
        "username_env",
        "password_env",
        "username_selector",
        "password_selector",
        "submit_selector",
        "success_selector",
        "storage_state_path",
    )
    values = [data.get(field) for field in fields]
    with connect() as conn:
        if auth_id:
            assignments = ", ".join(f"{field} = ?" for field in fields)
            conn.execute(
                f"UPDATE auth_profiles SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*values, auth_id),
            )
            return auth_id
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO auth_profiles ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)


def delete_auth_profile(auth_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM auth_profiles WHERE id = ?", (auth_id,))


def list_mail_profiles() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM mail_profiles ORDER BY id ASC").fetchall()
    return [dict(row) for row in rows]


def get_mail_profile(mail_id: int | None) -> dict[str, Any] | None:
    if not mail_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM mail_profiles WHERE id = ?", (mail_id,)).fetchone()
    return dict(row) if row is not None else None


def save_mail_profile(data: dict[str, Any], mail_id: int | None = None) -> int:
    data = dict(data)
    if data.get("password"):
        data["password_secret"] = normalize_secret_for_storage(str(data.get("password") or ""))
    elif data.get("password_secret"):
        data["password_secret"] = normalize_secret_for_storage(str(data.get("password_secret") or ""))
    data["password"] = ""

    fields = (
        "name",
        "smtp_host",
        "smtp_port",
        "use_ssl",
        "use_starttls",
        "username",
        "password",
        "password_source",
        "password_secret",
        "password_env",
        "sender",
        "recipients",
        "cc",
        "subject_template",
        "body_template",
    )
    values = [data.get(field) for field in fields]
    with connect() as conn:
        if mail_id:
            assignments = ", ".join(f"{field} = ?" for field in fields)
            conn.execute(
                f"UPDATE mail_profiles SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*values, mail_id),
            )
            return mail_id
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO mail_profiles ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)


def delete_mail_profile(mail_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM mail_profiles WHERE id = ?", (mail_id,))


def create_run(job_id: int | None, job_name: str) -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.settings import settings
    local_tz = ZoneInfo(settings.default_timezone)
    now_str = datetime.now(local_tz).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO run_records (job_id, job_name, started_at) VALUES (?, ?, ?)",
            (job_id, job_name, now_str),
        )
        return int(cur.lastrowid)


def update_run(run_id: int, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    with connect() as conn:
        conn.execute(f"UPDATE run_records SET {assignments} WHERE id = ?", (*values, run_id))


def add_screenshot_result(data: dict[str, Any]) -> int:
    fields = (
        "run_id",
        "item_id",
        "item_name",
        "section",
        "status",
        "file_path",
        "error_message",
    )
    values = [data.get(field) for field in fields]
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO screenshot_results ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            values,
        )
        return int(cur.lastrowid)


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM run_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_runs_page(page: int = 1, page_size: int = 10) -> tuple[list[dict[str, Any]], int]:
    offset = (page - 1) * page_size
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM run_records").fetchone()[0]
        rows = conn.execute(
            """
            SELECT *
            FROM run_records
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
    return [dict(row) for row in rows], total


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM run_records WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row is not None else None


def list_screenshot_results(run_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM screenshot_results
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_previous_run_today(job_id: int, current_run_id: int) -> dict[str, Any] | None:
    from pathlib import Path
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app.settings import settings

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM run_records
            WHERE job_id = ? AND id < ? AND report_path != '' AND report_path IS NOT NULL
            ORDER BY id DESC
            LIMIT 20
            """,
            (job_id, current_run_id),
        ).fetchall()

    local_tz = ZoneInfo(settings.default_timezone)
    today_str = datetime.now(local_tz).strftime("%Y-%m-%d")

    for row in rows:
        run = dict(row)
        started_at_str = run.get("started_at")
        if not started_at_str:
            continue
        try:
            if "T" in started_at_str:
                dt = datetime.fromisoformat(started_at_str)
            else:
                dt = datetime.strptime(started_at_str, "%Y-%m-%d %H:%M:%S")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_date = dt.astimezone(local_tz).strftime("%Y-%m-%d")
            if local_date == today_str:
                p_path = run.get("report_path")
                if p_path and Path(p_path).exists():
                    return run
        except Exception:
            continue
    return None


def list_periodic_report_settings() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM periodic_report_settings ORDER BY id ASC").fetchall()
    return [dict(row) for row in rows]


def get_periodic_report_setting(report_type: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM periodic_report_settings WHERE report_type = ?", (report_type,)).fetchone()
    return dict(row) if row is not None else None


def save_periodic_report_setting(report_type: str, data: dict[str, Any]) -> None:
    # 拷贝一份以避免直接改写外部传入的 dict 引用
    data = dict(data)
    
    # 自动对钉钉加密存储敏感字段
    for secret_field in ("dingtalk_webhook", "dingtalk_secret"):
        if data.get(secret_field):
            data[secret_field] = normalize_secret_for_storage(str(data.get(secret_field) or ""))

    fields = (
        "enabled",
        "name",
        "schedule_mode",
        "schedule_label",
        "cron_expression",
        "mail_profile_id",
        "include_screenshots",
        "include_docx",
        "send_empty_report",
        "wait_for_daily_jobs",
        "retry_until_time",
        "retry_interval_minutes",
        "recipients_override",
        "schedule_config",
        "send_on_timeout",
        "enable_email",
        "dingtalk_enabled",
        "dingtalk_webhook",
        "dingtalk_secret",
        "dingtalk_keyword",
    )
    values = [data.get(field) for field in fields]
    with connect() as conn:
        assignments = ", ".join(f"{field} = ?" for field in fields)
        conn.execute(
            f"UPDATE periodic_report_settings SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE report_type = ?",
            (*values, report_type),
        )


def list_periodic_report_runs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM periodic_report_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_periodic_report_run(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM periodic_report_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row is not None else None


def save_periodic_report_run(data: dict[str, Any], run_id: int | None = None) -> int:
    fields = (
        "report_type",
        "period_start",
        "period_end",
        "status",
        "zip_path",
        "mail_status",
        "dingtalk_status",
        "error_summary",
        "finished_at",
    )
    values = [data.get(field) for field in fields]
    with connect() as conn:
        if run_id:
            assignments = ", ".join(f"{field} = ?" for field in fields)
            conn.execute(
                f"UPDATE periodic_report_runs SET {assignments} WHERE id = ?",
                (*values, run_id),
            )
            return run_id
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO periodic_report_runs ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)


def delete_periodic_report_run(run_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM periodic_report_runs WHERE id = ?", (run_id,))


def get_system_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        try:
            row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:
            return default
    return str(row["value"]) if row is not None else default


def set_system_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            REPLACE INTO system_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (key, value),
        )
