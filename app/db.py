from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.settings import settings


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn



def init_db() -> None:
    settings.ensure_dirs()
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                auth_type TEXT NOT NULL DEFAULT 'none',
                login_url TEXT DEFAULT '',
                username_env TEXT DEFAULT '',
                password_env TEXT DEFAULT '',
                username_selector TEXT DEFAULT 'input[name="user"], input[name="username"], input[type="email"], input[placeholder*="username"], input[placeholder*="email"], input[placeholder*="user"]',
                password_selector TEXT DEFAULT 'input[name="password"], input[type="password"], input[placeholder*="password"]',
                submit_selector TEXT DEFAULT 'button[type="submit"]',
                success_selector TEXT DEFAULT '',
                storage_state_path TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mail_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                smtp_host TEXT NOT NULL DEFAULT '',
                smtp_port INTEGER NOT NULL DEFAULT 465,
                use_ssl INTEGER NOT NULL DEFAULT 1,
                use_starttls INTEGER NOT NULL DEFAULT 0,
                username TEXT DEFAULT '',
                password_env TEXT DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                recipients TEXT NOT NULL DEFAULT '',
                cc TEXT DEFAULT '',
                subject_template TEXT NOT NULL DEFAULT '自动化巡检报告 - {date}',
                body_template TEXT NOT NULL DEFAULT '巡检报告已生成，请查看附件。',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS report_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                environment TEXT NOT NULL DEFAULT '生产环境',
                enabled INTEGER NOT NULL DEFAULT 1,
                cron_expression TEXT NOT NULL DEFAULT '0 9 * * *',
                time_range_label TEXT NOT NULL DEFAULT '最近24小时',
                report_title TEXT NOT NULL DEFAULT '自动化巡检报告',
                mail_profile_id INTEGER,
                send_mail INTEGER NOT NULL DEFAULT 0,
                browser_width INTEGER NOT NULL DEFAULT 1920,
                browser_height INTEGER NOT NULL DEFAULT 1080,
                headless INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(mail_profile_id) REFERENCES mail_profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS screenshot_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                auth_profile_id INTEGER,
                name TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'web',
                url TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '巡检截图',
                capture_mode TEXT NOT NULL DEFAULT 'full_page',
                css_selector TEXT DEFAULT '',
                wait_selector TEXT DEFAULT '',
                wait_seconds REAL NOT NULL DEFAULT 3,
                timeout_seconds INTEGER NOT NULL DEFAULT 60,
                retry_count INTEGER NOT NULL DEFAULT 2,
                browser_width INTEGER,
                browser_height INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES report_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(auth_profile_id) REFERENCES auth_profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS run_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                job_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                report_path TEXT DEFAULT '',
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT DEFAULT '',
                mail_status TEXT DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES report_jobs(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS screenshot_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                item_id INTEGER,
                item_name TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '巡检截图',
                status TEXT NOT NULL,
                file_path TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(run_id) REFERENCES run_records(id) ON DELETE CASCADE,
                FOREIGN KEY(item_id) REFERENCES screenshot_items(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS periodic_report_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL,
                schedule_mode TEXT NOT NULL DEFAULT 'simple',
                schedule_label TEXT NOT NULL DEFAULT '',
                cron_expression TEXT NOT NULL,
                mail_profile_id INTEGER,
                include_screenshots INTEGER NOT NULL DEFAULT 0,
                include_docx INTEGER NOT NULL DEFAULT 1,
                send_empty_report INTEGER NOT NULL DEFAULT 0,
                wait_for_daily_jobs INTEGER NOT NULL DEFAULT 1,
                retry_until_time TEXT NOT NULL DEFAULT '23:50',
                retry_interval_minutes INTEGER NOT NULL DEFAULT 10,
                recipients_override TEXT DEFAULT '',
                schedule_config TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(mail_profile_id) REFERENCES mail_profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS periodic_report_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                zip_path TEXT DEFAULT '',
                mail_status TEXT DEFAULT '',
                error_summary TEXT DEFAULT '',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT
            );
            """
        )
        
        # Database schema migrations to support frontend config & credentials source status
        migrations = [
            ("auth_profiles", "username", "TEXT DEFAULT ''"),
            ("auth_profiles", "password", "TEXT DEFAULT ''"),
            ("auth_profiles", "username_source", "TEXT DEFAULT 'env'"),
            ("auth_profiles", "password_source", "TEXT DEFAULT 'env'"),
            ("auth_profiles", "username_value", "TEXT DEFAULT ''"),
            ("auth_profiles", "password_secret", "TEXT DEFAULT ''"),
            ("mail_profiles", "password", "TEXT DEFAULT ''"),
            ("mail_profiles", "password_source", "TEXT DEFAULT 'env'"),
            ("mail_profiles", "password_secret", "TEXT DEFAULT ''"),
            ("mail_profiles", "password_env", "TEXT DEFAULT ''"),
            ("report_jobs", "schedule_mode", "TEXT DEFAULT 'cron'"),
            ("report_jobs", "schedule_label", "TEXT DEFAULT '高级 Cron'"),
            ("report_jobs", "schedule_config", "TEXT DEFAULT ''"),
            ("report_jobs", "load_error", "TEXT DEFAULT ''"),
            ("periodic_report_settings", "send_on_timeout", "INTEGER DEFAULT 1"),
            ("periodic_report_settings", "load_error", "TEXT DEFAULT ''"),
        ]
        for table, col, t in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {t}")
            except sqlite3.OperationalError:
                pass # 列已存在时直接忽略，保证平滑向后兼容

        seed_defaults(conn)
        sync_from_env_if_empty()


def sync_from_env_if_empty() -> None:
    import os
    from app.utils import encrypt_secret
    
    # 1. 同步 Grafana 凭据
    grafana_url = os.getenv("GRAFANA_URL", "")
    grafana_user = os.getenv("GRAFANA_USER", "")
    grafana_pass = os.getenv("GRAFANA_PASS", "")
    
    with connect() as conn:
        row = conn.execute("SELECT * FROM auth_profiles WHERE name = ?", ("Grafana 用户名密码",)).fetchone()
        if row:
            p = dict(row)
            need_update = False
            update_data = {}
            if not p.get("username_value") and not p.get("username") and grafana_user:
                update_data["username_value"] = grafana_user
                update_data["username"] = grafana_user
                need_update = True
            if not p.get("password_secret") and not p.get("password") and grafana_pass:
                update_data["password_secret"] = encrypt_secret(grafana_pass)
                update_data["password"] = grafana_pass
                need_update = True
            if not p.get("login_url") and grafana_url:
                update_data["login_url"] = grafana_url
                need_update = True
                
            if need_update:
                set_clauses = ", ".join([f"{k} = ?" for k in update_data.keys()])
                conn.execute(
                    f"UPDATE auth_profiles SET {set_clauses}, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                    (*update_data.values(), "Grafana 用户名密码")
                )
                
        # 2. 同步 Kibana 凭据
        kibana_url = os.getenv("KIBANA_URL", "")
        kibana_user = os.getenv("KIBANA_USER", "")
        kibana_pass = os.getenv("KIBANA_PASS", "")
        
        row = conn.execute("SELECT * FROM auth_profiles WHERE name = ?", ("Kibana 用户名密码",)).fetchone()
        if row:
            p = dict(row)
            need_update = False
            update_data = {}
            if not p.get("username_value") and not p.get("username") and kibana_user:
                update_data["username_value"] = kibana_user
                update_data["username"] = kibana_user
                need_update = True
            if not p.get("password_secret") and not p.get("password") and kibana_pass:
                update_data["password_secret"] = encrypt_secret(kibana_pass)
                update_data["password"] = kibana_pass
                need_update = True
            if not p.get("login_url") and kibana_url:
                update_data["login_url"] = kibana_url
                need_update = True
                
            if need_update:
                set_clauses = ", ".join([f"{k} = ?" for k in update_data.keys()])
                conn.execute(
                    f"UPDATE auth_profiles SET {set_clauses}, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                    (*update_data.values(), "Kibana 用户名密码")
                )
                
        # 3. 同步 SMTP 邮件配置
        smtp_host = os.getenv("SMTP_HOST", "")
        smtp_port = os.getenv("SMTP_PORT", "")
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")
        smtp_from = os.getenv("SMTP_FROM", "")
        smtp_to = os.getenv("SMTP_TO", "")
        
        row = conn.execute("SELECT * FROM mail_profiles WHERE name = ?", ("默认邮件配置",)).fetchone()
        if row:
            p = dict(row)
            need_update = False
            update_data = {}
            if not p.get("smtp_host") and smtp_host:
                update_data["smtp_host"] = smtp_host
                need_update = True
            if not p.get("smtp_port") or p.get("smtp_port") == 465:
                if smtp_port:
                    try:
                        update_data["smtp_port"] = int(smtp_port)
                        need_update = True
                    except ValueError:
                        pass
            if not p.get("username") and smtp_user:
                update_data["username"] = smtp_user
                need_update = True
            if not p.get("password_secret") and not p.get("password") and smtp_pass:
                update_data["password_secret"] = encrypt_secret(smtp_pass)
                update_data["password"] = smtp_pass
                need_update = True
            if not p.get("sender") and smtp_from:
                update_data["sender"] = smtp_from
                need_update = True
            if not p.get("recipients") and smtp_to:
                update_data["recipients"] = smtp_to
                need_update = True
                
            if need_update:
                set_clauses = ", ".join([f"{k} = ?" for k in update_data.keys()])
                conn.execute(
                    f"UPDATE mail_profiles SET {set_clauses}, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                    (*update_data.values(), "默认邮件配置")
                )


def seed_defaults(conn: sqlite3.Connection) -> None:
    import os

    grafana_base_url = str(os.getenv("GRAFANA_URL") or "http://localhost:3000").rstrip("/")

    auth_count = conn.execute("SELECT COUNT(*) FROM auth_profiles").fetchone()[0]
    if auth_count == 0:
        conn.execute(
            """
            INSERT INTO auth_profiles
            (name, auth_type, login_url, username_env, password_env, success_selector)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("无需登录", "none", "", "", "", ""),
        )
        conn.execute(
            """
            INSERT INTO auth_profiles
            (name, auth_type, login_url, username_env, password_env, success_selector)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Grafana 用户名密码", "form", "", "", "", "body"),
        )
        conn.execute(
            """
            INSERT INTO auth_profiles
            (name, auth_type, login_url, username_env, password_env, success_selector)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Kibana 用户名密码", "form", "", "", "", "[data-test-subj='dashboardViewport']"),
        )
    else:
        conn.execute(
            """
            UPDATE auth_profiles
            SET success_selector = CASE
                    WHEN success_selector = '' OR success_selector = '.react-grid-layout' THEN ?
                    ELSE success_selector
                END,
                username_selector = CASE 
                    WHEN username_selector = 'input[name="user"], input[name="username"], input[type="email"]' OR username_selector = '' 
                    THEN 'input[name="user"], input[name="username"], input[type="email"], input[placeholder*="username"], input[placeholder*="email"], input[placeholder*="user"]'
                    ELSE username_selector
                END,
                password_selector = CASE 
                    WHEN password_selector = 'input[name="password"], input[type="password"]' OR password_selector = '' 
                    THEN 'input[name="password"], input[type="password"], input[placeholder*="password"]'
                    ELSE password_selector
                END
            WHERE name = ?
            """,
            ("body", "Grafana 用户名密码"),
        )

    mail_count = conn.execute("SELECT COUNT(*) FROM mail_profiles").fetchone()[0]
    if mail_count == 0:
        conn.execute(
            """
            INSERT INTO mail_profiles
            (name, smtp_host, smtp_port, use_ssl, username, password_env, sender, recipients)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("默认邮件配置", "", 465, 1, "", "", "", ""),
        )

    job_count = conn.execute("SELECT COUNT(*) FROM report_jobs").fetchone()[0]
    if job_count == 0:
        cur = conn.execute(
            """
            INSERT INTO report_jobs
            (name, environment, cron_expression, time_range_label, report_title, mail_profile_id, send_mail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("生产环境每日巡检", "生产环境", "0 9 * * *", "最近24小时", "自动化巡检报告", 1, 0),
        )
        job_id = int(cur.lastrowid)
    else:
        first_job = conn.execute("SELECT id FROM report_jobs ORDER BY id LIMIT 1").fetchone()
        job_id = int(first_job[0]) if first_job else None

    item_count = conn.execute("SELECT COUNT(*) FROM screenshot_items").fetchone()[0]
    if item_count == 0 and job_id:
        grafana_auth = conn.execute(
            "SELECT id FROM auth_profiles WHERE name = ?",
            ("Grafana 用户名密码",),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO screenshot_items
            (job_id, auth_profile_id, name, item_type, url, section, capture_mode, css_selector,
             wait_selector, wait_seconds, timeout_seconds, retry_count, browser_width, browser_height,
             sort_order, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                int(grafana_auth[0]) if grafana_auth else None,
                "本地 Grafana 测试看板",
                "grafana",
                f"{grafana_base_url}/d/local-inspection-test/local-grafana-inspection-test?orgId=1&from=now-1h&to=now&kiosk",
                "Grafana 看板",
                "viewport",
                "",
                ".react-grid-layout",
                5,
                90,
                2,
                1920,
                1080,
                100,
                1,
            ),
        )

    # Seed periodic report configurations (weekly / monthly)
    weekly_count = conn.execute("SELECT COUNT(*) FROM periodic_report_settings WHERE report_type = 'weekly'").fetchone()[0]
    if weekly_count == 0:
        conn.execute(
            """
            INSERT INTO periodic_report_settings
            (report_type, enabled, name, schedule_mode, schedule_label, cron_expression, mail_profile_id, 
             include_screenshots, include_docx, send_empty_report, wait_for_daily_jobs, schedule_config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("weekly", 0, "每周汇总报告", "simple", "每周一 上午09:00", "0 9 * * 1", 1, 0, 1, 0, 0,
             '{"frequency": "weekly", "day_of_week": "1", "day_of_month": "1", "times": [{"ampm": "am", "hour": 9, "minute": 0}]}'),
        )

    monthly_count = conn.execute("SELECT COUNT(*) FROM periodic_report_settings WHERE report_type = 'monthly'").fetchone()[0]
    if monthly_count == 0:
        conn.execute(
            """
            INSERT INTO periodic_report_settings
            (report_type, enabled, name, schedule_mode, schedule_label, cron_expression, mail_profile_id, 
             include_screenshots, include_docx, send_empty_report, wait_for_daily_jobs, schedule_config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("monthly", 0, "每月汇总报告", "simple", "每月最后一天 下午18:00", "0 18 L * *", 1, 0, 1, 0, 1,
             '{"frequency": "monthly_last", "day_of_week": "1", "day_of_month": "1", "times": [{"ampm": "pm", "hour": 6, "minute": 0}]}'),
        )


def reset_database_data() -> None:
    """一键删除重置所有本地测试数据及物理目录文件"""
    import shutil
    # 1. 物理清理截图、报告和会话状态目录
    for d in (settings.screenshot_dir, settings.report_dir, settings.browser_state_dir):
        if d.exists():
            try:
                shutil.rmtree(d)
            except Exception:
                pass
    settings.ensure_dirs()

    # 2. 清理数据库所有数据表
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        tables = [
            "periodic_report_runs",
            "periodic_report_settings",
            "screenshot_results",
            "run_records",
            "screenshot_items",
            "report_jobs",
            "mail_profiles",
            "auth_profiles",
        ]
        for t in tables:
            try:
                conn.execute(f"DELETE FROM {t}")
                # 清除自增序列
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t,))
            except sqlite3.OperationalError:
                pass  # 防御表不存在
        conn.execute("PRAGMA foreign_keys = ON")
        
        # 3. 重新导入默认种子数据
        seed_defaults(conn)
