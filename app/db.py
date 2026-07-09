from __future__ import annotations

from pysqlcipher3 import dbapi2 as sqlite3
from pathlib import Path
from typing import Any

from app.settings import settings


def connect() -> sqlite3.Connection:
    import os
    import shutil

    db_path = settings.db_path
    db_key = os.getenv("SQLCIPHER_DB_KEY", settings.secret_key or "default-key-change-me")
    # 对密钥中的单引号进行转义，防止特殊字符导致 SQL 注入或 PRAGMA 语法解析错
    db_key = db_key.replace("'", "''")

    # 1. 数据库不存在时，直接创建加密数据库
    if not os.path.exists(db_path):
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        conn.execute(f"PRAGMA key = '{db_key}'")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    # 2. 数据库存在，探测其是明文还是已加密
    is_plain = False
    try:
        import sqlite3 as std_sqlite
        test_conn = std_sqlite.connect(db_path, timeout=5.0)
        test_conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        test_conn.close()
        is_plain = True
    except Exception:
        try:
            test_conn.close()
        except Exception:
            pass

    # 3. 探测到是明文，自动执行热迁移并删除原明文备份
    if is_plain:
        temp_encrypted_path = str(db_path) + ".encrypted_tmp"
        backup_path = str(db_path) + ".backup_before_encrypt"
        
        if os.path.exists(temp_encrypted_path):
            try:
                os.remove(temp_encrypted_path)
            except Exception:
                pass
                
        try:
            # 复制明文备份，预防迁移中途断电等意外故障
            shutil.copy2(db_path, backup_path)
            
            # 使用 ATTACH 将明文数据导入加密临时库
            plain_conn = sqlite3.connect(str(db_path))
            plain_conn.execute(f"ATTACH DATABASE '{temp_encrypted_path}' AS encrypted KEY '{db_key}'")
            plain_conn.execute("SELECT sqlcipher_export('encrypted')")
            plain_conn.execute("DETACH DATABASE encrypted")
            plain_conn.close()
            
            # 覆盖原库
            shutil.move(temp_encrypted_path, db_path)
            
            # 使用新密钥验证连接和查询是否正常
            verify_conn = sqlite3.connect(str(db_path), timeout=5.0)
            verify_conn.execute(f"PRAGMA key = '{db_key}'")
            verify_conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            verify_conn.close()
            
            # 验证成功，彻底物理删除明文备份
            if os.path.exists(backup_path):
                os.remove(backup_path)
                
        except Exception as e:
            # 出错清理临时残留
            for path in (temp_encrypted_path, backup_path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            raise RuntimeError(f"自动将 SQLite 明文库加密热迁移失败: {e}")

    # 4. 以加密连接打开
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute(f"PRAGMA key = '{db_key}'")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL;")
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
                browser_width INTEGER NOT NULL DEFAULT 3840,
                browser_height INTEGER NOT NULL DEFAULT 2160,
                browser_scale_factor REAL NOT NULL DEFAULT 1.5,
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
                capture_mode TEXT NOT NULL DEFAULT 'viewport',
                css_selector TEXT DEFAULT '',
                wait_selector TEXT DEFAULT '',
                wait_seconds REAL NOT NULL DEFAULT 3,
                timeout_seconds INTEGER NOT NULL DEFAULT 60,
                retry_count INTEGER NOT NULL DEFAULT 2,
                browser_width INTEGER,
                browser_height INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1,
                real_browser_capture INTEGER NOT NULL DEFAULT 1,
                watermark_enabled INTEGER NOT NULL DEFAULT 0,
                watermark_text TEXT DEFAULT '',
                watermark_opacity INTEGER NOT NULL DEFAULT 65,
                watermark_font_size INTEGER NOT NULL DEFAULT 24,
                watermark_gap_x INTEGER NOT NULL DEFAULT 140,
                watermark_gap_y INTEGER NOT NULL DEFAULT 140,
                watermark_angle INTEGER NOT NULL DEFAULT -45,
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
                send_on_timeout INTEGER DEFAULT 1,
                load_error TEXT DEFAULT '',
                enable_email INTEGER NOT NULL DEFAULT 0,
                dingtalk_enabled INTEGER NOT NULL DEFAULT 0,
                dingtalk_webhook TEXT DEFAULT '',
                dingtalk_secret TEXT DEFAULT '',
                dingtalk_keyword TEXT DEFAULT '',
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
                dingtalk_status TEXT DEFAULT '',
                error_summary TEXT DEFAULT '',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS storage_cleanup_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enabled INTEGER NOT NULL DEFAULT 1,
                allow_manual_cleanup INTEGER NOT NULL DEFAULT 1,
                cleanup_schedule_mode TEXT NOT NULL DEFAULT 'daily',
                cleanup_schedule_label TEXT NOT NULL DEFAULT '每天 02:30',
                cleanup_schedule_config TEXT NOT NULL DEFAULT '',
                periodic_sent_retention_days INTEGER NOT NULL DEFAULT 3,
                periodic_failed_retention_days INTEGER NOT NULL DEFAULT 30,
                screenshot_retention_days INTEGER NOT NULL DEFAULT 7,
                report_retention_days INTEGER NOT NULL DEFAULT 30,
                run_record_retention_days INTEGER NOT NULL DEFAULT 90,
                log_retention_days INTEGER NOT NULL DEFAULT 14,
                browser_state_retention_days INTEGER NOT NULL DEFAULT 30,
                browser_state_cleanup_enabled INTEGER NOT NULL DEFAULT 0,
                protect_recent_days INTEGER NOT NULL DEFAULT 3,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS storage_cleanup_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL DEFAULT 'auto',
                status TEXT NOT NULL DEFAULT 'success',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                deleted_files_count INTEGER NOT NULL DEFAULT 0,
                deleted_bytes INTEGER NOT NULL DEFAULT 0,
                deleted_screenshots_count INTEGER NOT NULL DEFAULT 0,
                deleted_reports_count INTEGER NOT NULL DEFAULT 0,
                deleted_periodic_archives_count INTEGER NOT NULL DEFAULT 0,
                deleted_logs_count INTEGER NOT NULL DEFAULT 0,
                deleted_browser_state_count INTEGER NOT NULL DEFAULT 0,
                deleted_run_records_count INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT DEFAULT '',
                detail_json TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS token_blacklist (
                token TEXT PRIMARY KEY,
                expired_at REAL NOT NULL
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
            ("periodic_report_settings", "enable_email", "INTEGER DEFAULT 0"),
            ("periodic_report_settings", "dingtalk_enabled", "INTEGER DEFAULT 0"),
            ("periodic_report_settings", "dingtalk_webhook", "TEXT DEFAULT ''"),
            ("periodic_report_settings", "dingtalk_secret", "TEXT DEFAULT ''"),
            ("periodic_report_settings", "dingtalk_keyword", "TEXT DEFAULT ''"),
            ("periodic_report_runs", "dingtalk_status", "TEXT DEFAULT ''"),
            ("screenshot_items", "real_browser_capture", "INTEGER DEFAULT 1"),
            ("report_jobs", "send_mail_on_complete", "INTEGER DEFAULT 1"),
            ("report_jobs", "send_mail_on_error", "INTEGER DEFAULT 1"),
            ("report_jobs", "dingtalk_enabled", "INTEGER DEFAULT 0"),
            ("report_jobs", "dingtalk_webhook", "TEXT DEFAULT ''"),
            ("report_jobs", "dingtalk_secret", "TEXT DEFAULT ''"),
            ("report_jobs", "dingtalk_keyword", "TEXT DEFAULT ''"),
            ("run_records", "dingtalk_status", "TEXT DEFAULT ''"),
            ("screenshot_items", "watermark_enabled", "INTEGER DEFAULT 0"),
            ("screenshot_items", "watermark_text", "TEXT DEFAULT ''"),
            ("screenshot_items", "watermark_opacity", "INTEGER DEFAULT 65"),
            ("screenshot_items", "watermark_font_size", "INTEGER DEFAULT 24"),
            ("screenshot_items", "watermark_gap_x", "INTEGER DEFAULT 140"),
            ("screenshot_items", "watermark_gap_y", "INTEGER DEFAULT 140"),
            ("screenshot_items", "watermark_angle", "INTEGER DEFAULT -45"),
            ("screenshot_items", "taskbar_enabled", "INTEGER DEFAULT 1"),
            ("report_jobs", "browser_scale_factor", "REAL DEFAULT 1.5"),
        ]
        for table, col, t in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {t}")
            except sqlite3.OperationalError:
                pass # 列已存在时直接忽略，保证平滑向后兼容

        # 兼容同步老数据中的邮件发送配置
        try:
            conn.execute("UPDATE report_jobs SET send_mail_on_complete = send_mail WHERE send_mail IS NOT NULL")
        except sqlite3.OperationalError:
            pass

        migrate_schema_defaults(conn)

        seed_system_defaults(conn)

        migrate_sensitive_values(conn)


def migrate_schema_defaults(conn: sqlite3.Connection) -> None:
    replacements = [
        ("report_jobs", "browser_width INTEGER NOT NULL DEFAULT 1920", "browser_width INTEGER NOT NULL DEFAULT 3840"),
        ("report_jobs", "browser_height INTEGER NOT NULL DEFAULT 1080", "browser_height INTEGER NOT NULL DEFAULT 2160"),
        ("screenshot_items", "capture_mode TEXT NOT NULL DEFAULT 'full_page'", "capture_mode TEXT NOT NULL DEFAULT 'viewport'"),
    ]
    changed = False
    conn.execute("PRAGMA writable_schema = ON")
    try:
        for table, old, new in replacements:
            cur = conn.execute(
                """
                 UPDATE sqlite_master
                 SET sql = replace(sql, ?, ?)
                 WHERE type = 'table' AND name = ? AND sql LIKE ?
                """,
                (old, new, table, f"%{old}%"),
            )
            changed = changed or cur.rowcount > 0
    finally:
        conn.execute("PRAGMA writable_schema = OFF")
    if changed:
        version = conn.execute("PRAGMA schema_version").fetchone()[0]
        conn.execute(f"PRAGMA schema_version = {int(version) + 1}")
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise sqlite3.DatabaseError(f"sqlite schema default migration failed: {result}")


def migrate_sensitive_values(conn: sqlite3.Connection) -> None:
    from app.utils import normalize_secret_for_storage

    for row in conn.execute("SELECT id, password, password_secret FROM auth_profiles").fetchall():
        data = dict(row)
        raw_secret = str(data.get("password_secret") or data.get("password") or "")
        encrypted = normalize_secret_for_storage(raw_secret, legacy_base64=True)
        if encrypted != (data.get("password_secret") or "") or (data.get("password") or "") != "":
            conn.execute(
                """
                UPDATE auth_profiles
                SET password = '', password_secret = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (encrypted, data["id"]),
            )

    for row in conn.execute("SELECT id, password, password_secret FROM mail_profiles").fetchall():
        data = dict(row)
        raw_secret = str(data.get("password_secret") or data.get("password") or "")
        encrypted = normalize_secret_for_storage(raw_secret, legacy_base64=True)
        if encrypted != (data.get("password_secret") or "") or (data.get("password") or "") != "":
            conn.execute(
                """
                UPDATE mail_profiles
                SET password = '', password_secret = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (encrypted, data["id"]),
            )

    for row in conn.execute("SELECT id, dingtalk_webhook, dingtalk_secret FROM report_jobs").fetchall():
        data = dict(row)
        webhook = normalize_secret_for_storage(str(data.get("dingtalk_webhook") or ""), legacy_base64=True)
        secret = normalize_secret_for_storage(str(data.get("dingtalk_secret") or ""), legacy_base64=True)
        if webhook != str(data.get("dingtalk_webhook") or "") or secret != str(data.get("dingtalk_secret") or ""):
            conn.execute(
                """
                UPDATE report_jobs
                SET dingtalk_webhook = ?, dingtalk_secret = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (webhook, secret, data["id"]),
            )


def seed_system_defaults(conn: sqlite3.Connection) -> None:
    weekly_count = conn.execute("SELECT COUNT(*) FROM periodic_report_settings WHERE report_type = 'weekly'").fetchone()[0]
    if weekly_count == 0:
        conn.execute(
            """
            INSERT INTO periodic_report_settings
            (report_type, enabled, name, schedule_mode, schedule_label, cron_expression, mail_profile_id,
             include_screenshots, include_docx, send_empty_report, wait_for_daily_jobs, schedule_config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "weekly",
                0,
                "每周汇总报告",
                "simple",
                "每周一 上午09:00",
                "0 9 * * 1",
                None,
                0,
                1,
                0,
                0,
                '{"frequency": "weekly", "day_of_week": "1", "day_of_month": "1", "times": [{"ampm": "am", "hour": 9, "minute": 0}]}',
            ),
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
            (
                "monthly",
                0,
                "每月汇总报告",
                "simple",
                "每月最后一天 下午18:00",
                "0 18 L * *",
                None,
                0,
                1,
                0,
                1,
                '{"frequency": "monthly_last", "day_of_week": "1", "day_of_month": "1", "times": [{"ampm": "pm", "hour": 6, "minute": 0}]}',
            ),
        )

    cleanup_count = conn.execute("SELECT COUNT(*) FROM storage_cleanup_settings").fetchone()[0]
    if cleanup_count == 0:
        conn.execute(
            """
            INSERT INTO storage_cleanup_settings (
                enabled, allow_manual_cleanup, cleanup_schedule_mode, cleanup_schedule_label, cleanup_schedule_config,
                periodic_sent_retention_days, periodic_failed_retention_days, screenshot_retention_days,
                report_retention_days, run_record_retention_days, log_retention_days,
                browser_state_retention_days, browser_state_cleanup_enabled, protect_recent_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1, 1, "daily", "每天 02:30",
                '{"frequency": "daily", "time": "02:30"}',
                90, 90, 90, 90, 90, 90, 30, 0, 7
            )
        )
    else:
        # 对老数据库执行一次自愈升级，将原来默认的 3/7/14/30 天升级为 90 天默认保留周期
        conn.execute(
            """
            UPDATE storage_cleanup_settings
            SET periodic_sent_retention_days = 90
            WHERE periodic_sent_retention_days = 3
            """
        )
        conn.execute(
            """
            UPDATE storage_cleanup_settings
            SET periodic_failed_retention_days = 90,
                screenshot_retention_days = 90,
                report_retention_days = 90,
                log_retention_days = 90
            WHERE report_retention_days = 30
            """
        )
        # 将默认的 3 天保护升级为 7 天
        conn.execute(
            """
            UPDATE storage_cleanup_settings
            SET protect_recent_days = 7
            WHERE protect_recent_days = 3
            """
        )
        # 将浏览器会话保留天数的系统默认升级值由 90 天下调为更合理的 30 天
        conn.execute(
            """
            UPDATE storage_cleanup_settings
            SET browser_state_retention_days = 30
            WHERE browser_state_retention_days = 90
            """
        )

    default_settings = {
        "swagger_enabled": "0",
        "mfa_enabled": "0",
        "mfa_totp_secret": "",
        "session_ttl_minutes": "30",
        "dingtalk_emoji_enabled": "0",
    }
    for key, value in default_settings.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO system_settings (key, value)
            VALUES (?, ?)
            """,
            (key, value),
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
            "system_settings",
            "storage_cleanup_runs",
            "storage_cleanup_settings",
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
        
        # 3. 仅恢复系统级默认配置，不再导入演示任务、认证或截图项。
        seed_system_defaults(conn)
