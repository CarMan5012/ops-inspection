from __future__ import annotations

from pysqlcipher3 import dbapi2 as sqlite3
from pathlib import Path
from typing import Any
from contextlib import contextmanager, closing

from app.settings import settings


def _migrate_plain_database() -> None:
    import os
    import secrets
    import shutil
    import sqlite3 as std_sqlite

    db_path = settings.db_path
    db_key = settings.secret_key
    if not db_key:
        raise RuntimeError("SQLCipher database key is missing")
    # 对密钥中的单引号进行转义，防止特殊字符导致 SQL 注入或 PRAGMA 语法解析错
    db_key = db_key.replace("'", "''")

    if not os.path.exists(db_path):
        return

    # 探测是否为明文库；加密库不能由标准 SQLite 读取。
    test_conn = None
    try:
        test_conn = std_sqlite.connect(db_path, timeout=5.0)
        test_conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        is_plain = True
    except Exception:
        is_plain = False
    finally:
        if test_conn is not None:
            test_conn.close()

    if not is_plain:
        return

    temp_encrypted_path = Path(str(db_path) + ".encrypted_tmp")
    backup_path = Path(str(db_path) + ".backup_before_encrypt")
    if backup_path.exists():
        # 上次失败留下的备份必须保留，不能被本次尝试覆盖。
        backup_path = Path(f"{backup_path}.{os.getpid()}.{secrets.token_hex(4)}")
    if temp_encrypted_path.exists():
        temp_encrypted_path.unlink()

    # SQLite 的 backup API 会包含尚未 checkpoint 的 WAL 数据。
    with closing(std_sqlite.connect(db_path, timeout=5.0)) as source:
        checkpoint = source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and checkpoint[0]:
            raise RuntimeError("明文数据库仍有未完成的 WAL 写入，已取消加密迁移")
        with closing(std_sqlite.connect(backup_path, timeout=5.0)) as backup:
            source.backup(backup)
    try:
        backup_path.chmod(0o600)
    except OSError:
        pass

    def verify_encrypted(path: Path) -> None:
        connection = sqlite3.connect(str(path), timeout=5.0)
        try:
            connection.execute(f"PRAGMA key = '{db_key}'")
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"加密数据库完整性校验失败: {result}")
        finally:
            connection.close()

    replaced = False
    try:
        plain_conn = sqlite3.connect(str(db_path))
        try:
            escaped_path = temp_encrypted_path.as_posix().replace("'", "''")
            plain_conn.execute(f"ATTACH DATABASE '{escaped_path}' AS encrypted KEY '{db_key}'")
            plain_conn.execute("SELECT sqlcipher_export('encrypted')")
            plain_conn.execute("DETACH DATABASE encrypted")
        finally:
            plain_conn.close()

        # 先验证临时库，再以同目录原子替换原库。
        verify_encrypted(temp_encrypted_path)
        os.replace(temp_encrypted_path, db_path)
        replaced = True
        verify_encrypted(db_path)
    except Exception as exc:
        restore_error = None
        if replaced:
            try:
                shutil.copy2(backup_path, db_path)
            except Exception as restore_exc:
                restore_error = restore_exc
        if temp_encrypted_path.exists():
            try:
                temp_encrypted_path.unlink()
            except OSError:
                pass
        detail = f"自动将 SQLite 明文库加密热迁移失败: {exc}；明文备份保留在 {backup_path}"
        if restore_error is not None:
            detail += f"；恢复原库失败: {restore_error}"
        raise RuntimeError(detail) from exc

    # 加密库已通过验证后才删除明文备份。
    try:
        backup_path.unlink()
    except OSError as exc:
        raise RuntimeError(f"加密迁移已完成，但无法删除明文备份 {backup_path}: {exc}") from exc

@contextmanager
def connect():
    db_path = settings.db_path
    db_key = settings.secret_key
    if not db_key:
        raise RuntimeError("SQLCipher database key is missing")
    db_key = db_key.replace("'", "''")
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        conn.execute(f"PRAGMA key = '{db_key}'")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    finally:
        conn.close()



def _initialize_installation_state(conn: Any, existing_database: bool) -> None:
    row = conn.execute("SELECT state FROM installation_state WHERE id = 1").fetchone()
    if row:
        return
    if existing_database:
        raise RuntimeError("检测到未初始化的旧数据库；请使用全新数据目录部署")
    conn.execute("INSERT INTO installation_state (id, state) VALUES (1, 'pending')")


def init_db() -> None:
    settings.ensure_dirs()
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    existing_database = settings.db_path.exists() and settings.db_path.stat().st_size > 0
    _migrate_plain_database()
    with connect() as conn:
        # Journal mode persists in the database; setting it on every connection
        # adds a schema operation to every request and can contend with writers.
        conn.execute("PRAGMA journal_mode = WAL")
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

            CREATE TABLE IF NOT EXISTS dingtalk_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                webhook TEXT NOT NULL DEFAULT '',
                secret TEXT DEFAULT '',
                keyword TEXT DEFAULT '',
                emoji_enabled INTEGER NOT NULL DEFAULT 1,
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

            CREATE TABLE IF NOT EXISTS report_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                stored_name TEXT NOT NULL UNIQUE,
                original_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS taskbar_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                stored_name TEXT NOT NULL UNIQUE,
                original_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                total_timeout_seconds INTEGER NOT NULL DEFAULT 240,
                retry_count INTEGER NOT NULL DEFAULT 2,
                browser_width INTEGER,
                browser_height INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1,
                real_browser_capture INTEGER NOT NULL DEFAULT 0,
                watermark_enabled INTEGER NOT NULL DEFAULT 1,
                watermark_time_enabled INTEGER NOT NULL DEFAULT 1,
                watermark_time_format TEXT NOT NULL DEFAULT 'datetime_seconds',
                watermark_text TEXT DEFAULT '王',
                watermark_opacity INTEGER NOT NULL DEFAULT 20,
                watermark_font_size INTEGER NOT NULL DEFAULT 53,
                watermark_gap_x INTEGER NOT NULL DEFAULT 234,
                watermark_gap_y INTEGER NOT NULL DEFAULT 594,
                watermark_angle INTEGER NOT NULL DEFAULT 36,
                watermark_alignment TEXT NOT NULL DEFAULT 'reference',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES report_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(auth_profile_id) REFERENCES auth_profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS run_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                job_name TEXT NOT NULL,
                schedule_slot TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                report_path TEXT DEFAULT '',
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT DEFAULT '',
                mail_status TEXT DEFAULT '',
                monitor_status TEXT NOT NULL DEFAULT 'not_checked',
                monitor_summary TEXT DEFAULT '',
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
                duration_seconds REAL DEFAULT 0.0,
                content_status TEXT NOT NULL DEFAULT 'not_applicable',
                content_summary TEXT DEFAULT '',
                monitor_status TEXT NOT NULL DEFAULT 'not_applicable',
                monitor_data TEXT DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS alert_ignore_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                source_uid TEXT NOT NULL,
                source_name TEXT NOT NULL DEFAULT '',
                alert_name TEXT NOT NULL,
                scope TEXT NOT NULL CHECK(scope IN ('rule', 'instance')),
                instance TEXT NOT NULL DEFAULT '',
                instance_label TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                expires_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                disabled_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_alert_ignore_active
                ON alert_ignore_rules(active, source_key, source_uid, alert_name);

            CREATE TABLE IF NOT EXISTS token_blacklist (
                token TEXT PRIMARY KEY,
                expired_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                last_activity REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                client_key TEXT PRIMARY KEY,
                window_start REAL NOT NULL,
                attempts INTEGER NOT NULL,
                blocked_until REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS admin_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS installation_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL
            );
            """
        )
        _initialize_installation_state(conn, existing_database)
        
        # Database schema migrations to support frontend config & credentials source status
        old_item_columns = {row[1] for row in conn.execute("PRAGMA table_info(screenshot_items)")}
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
            ("screenshot_items", "real_browser_capture", "INTEGER DEFAULT 0"),
            ("screenshot_items", "total_timeout_seconds", "INTEGER NOT NULL DEFAULT 240"),
            ("screenshot_results", "duration_seconds", "REAL DEFAULT 0.0"),
            ("screenshot_results", "content_status", "TEXT NOT NULL DEFAULT 'not_applicable'"),
            ("screenshot_results", "content_summary", "TEXT DEFAULT ''"),
            ("report_jobs", "send_mail_on_complete", "INTEGER DEFAULT 1"),
            ("report_jobs", "send_mail_on_error", "INTEGER DEFAULT 1"),
            ("report_jobs", "dingtalk_enabled", "INTEGER DEFAULT 0"),
            ("report_jobs", "dingtalk_webhook", "TEXT DEFAULT ''"),
            ("report_jobs", "dingtalk_secret", "TEXT DEFAULT ''"),
            ("report_jobs", "dingtalk_keyword", "TEXT DEFAULT ''"),
            ("run_records", "dingtalk_status", "TEXT DEFAULT ''"),
            ("run_records", "schedule_slot", "TEXT DEFAULT ''"),
            ("run_records", "monitor_status", "TEXT NOT NULL DEFAULT 'not_checked'"),
            ("run_records", "monitor_summary", "TEXT DEFAULT ''"),
            ("screenshot_results", "monitor_status", "TEXT NOT NULL DEFAULT 'not_applicable'"),
            ("screenshot_results", "monitor_data", "TEXT DEFAULT ''"),
            ("screenshot_items", "watermark_enabled", "INTEGER DEFAULT 0"),
            ("screenshot_items", "watermark_time_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("screenshot_items", "watermark_time_format", "TEXT NOT NULL DEFAULT 'datetime_seconds'"),
            ("screenshot_items", "watermark_text", "TEXT DEFAULT ''"),
            ("screenshot_items", "watermark_opacity", "INTEGER DEFAULT 65"),
            ("screenshot_items", "watermark_font_size", "INTEGER DEFAULT 24"),
            ("screenshot_items", "watermark_gap_x", "INTEGER DEFAULT 140"),
            ("screenshot_items", "watermark_gap_y", "INTEGER DEFAULT 140"),
            ("screenshot_items", "watermark_angle", "INTEGER DEFAULT -45"),
            ("screenshot_items", "watermark_alignment", "TEXT NOT NULL DEFAULT 'reference'"),
            ("screenshot_items", "taskbar_enabled", "INTEGER DEFAULT 1"),
            ("screenshot_items", "taskbar_template_key", "TEXT NOT NULL DEFAULT ''"),
            ("report_jobs", "browser_scale_factor", "REAL DEFAULT 1.5"),
            ("report_jobs", "dingtalk_profile_id", "INTEGER DEFAULT NULL"),
            ("report_jobs", "template_id", "INTEGER DEFAULT NULL"),
            ("report_jobs", "send_dingtalk_on_complete", "INTEGER DEFAULT 1"),
            ("report_jobs", "send_dingtalk_on_error", "INTEGER DEFAULT 1"),
            ("periodic_report_settings", "dingtalk_profile_id", "INTEGER DEFAULT NULL"),
        ]
        for table, col, t in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {t}")
            except sqlite3.OperationalError:
                pass # 列已存在时直接忽略，保证平滑向后兼容

        if "watermark_time_enabled" not in old_item_columns:
            conn.execute("""
                UPDATE screenshot_items
                SET watermark_time_enabled = CASE
                        WHEN instr(COALESCE(watermark_text, ''), '{time}') > 0 OR
                             (trim(COALESCE(watermark_text, '')) = '' AND watermark_enabled = 1) THEN 1
                        ELSE 0 END,
                    watermark_text = CASE
                        WHEN trim(COALESCE(watermark_text, '')) = '' AND watermark_enabled = 1
                        THEN '省客户服务中心' || char(10) || 'dwangchengyi7(王诚毅)'
                        ELSE trim(replace(COALESCE(watermark_text, ''), '{time}', ''), char(10) || char(13) || ' ')
                    END
            """)

        # This data migration must run once. A later user-selected opacity of 26
        # is a valid custom setting and must survive subsequent application starts.
        preset_migration_key = "watermark_reference_preset_v2_migrated"
        preset_migrated = conn.execute(
            "SELECT 1 FROM system_settings WHERE key = ?", (preset_migration_key,)
        ).fetchone()
        if not preset_migrated:
            conn.execute("""
                UPDATE screenshot_items
                SET watermark_font_size = 53, watermark_gap_x = 234,
                    watermark_gap_y = 594, watermark_angle = 36,
                    watermark_opacity = 20,
                    watermark_alignment = CASE WHEN watermark_alignment IN ('right', 'center')
                        THEN 'reference' ELSE watermark_alignment END
                WHERE watermark_enabled = 1 AND watermark_time_enabled = 1
                  AND trim(COALESCE(watermark_text, '')) = '王'
                  AND ((watermark_font_size = 24 AND watermark_gap_x = 140
                        AND watermark_gap_y = 140 AND watermark_angle = -45
                        AND watermark_opacity = 65)
                    OR (watermark_font_size = 52 AND watermark_gap_x = 500
                        AND watermark_gap_y = 300 AND watermark_angle = 45
                        AND watermark_opacity IN (52, 65)))
            """)
            conn.execute("""
                UPDATE screenshot_items SET watermark_alignment = 'reference',
                    watermark_opacity = 20
                WHERE watermark_enabled = 1 AND watermark_time_enabled = 1
                  AND trim(COALESCE(watermark_text, '')) = '王'
                  AND watermark_font_size = 53 AND watermark_gap_x = 234
                  AND watermark_gap_y = 594 AND watermark_angle = 36
                  AND watermark_opacity IN (26, 52)
                  AND watermark_alignment IN ('center', 'reference')
            """)
            conn.execute(
                "INSERT INTO system_settings (key, value) VALUES (?, '1')",
                (preset_migration_key,),
            )

        # 兼容同步老数据中的邮件发送配置
        try:
            conn.execute("UPDATE report_jobs SET send_mail_on_complete = send_mail WHERE send_mail IS NOT NULL")
        except sqlite3.OperationalError:
            pass

        migrate_schema_defaults(conn)

        # 兼容同步全局钉钉配置到 dingtalk_profiles
        try:
            bot_count = conn.execute("SELECT COUNT(*) FROM dingtalk_profiles").fetchone()[0]
            if bot_count == 0:
                row = conn.execute("SELECT value FROM system_settings WHERE key = 'dingtalk_webhook'").fetchone()
                if row and row[0]:
                    old_webhook = row[0]
                    sec_row = conn.execute("SELECT value FROM system_settings WHERE key = 'dingtalk_secret'").fetchone()
                    old_sec = sec_row[0] if sec_row else ""
                    kw_row = conn.execute("SELECT value FROM system_settings WHERE key = 'dingtalk_keyword'").fetchone()
                    old_kw = kw_row[0] if kw_row else ""
                    em_row = conn.execute("SELECT value FROM system_settings WHERE key = 'dingtalk_emoji_enabled'").fetchone()
                    old_em = 1 if (em_row and em_row[0] in ("1", "true")) else 0
                    conn.execute(
                        "INSERT INTO dingtalk_profiles (name, webhook, secret, keyword, emoji_enabled) VALUES (?, ?, ?, ?, ?)",
                        ("默认钉钉机器人", old_webhook, old_sec, old_kw, old_em)
                    )
        except Exception:
            pass

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

    # Convert secrets written by the older local cipher, and seal any old raw values.
    for table, columns in (
        ("dingtalk_profiles", ("webhook", "secret")),
        ("periodic_report_settings", ("dingtalk_webhook", "dingtalk_secret")),
    ):
        selected = ", ".join(("id", *columns))
        for row in conn.execute(f"SELECT {selected} FROM {table}").fetchall():
            changes = {
                column: normalize_secret_for_storage(str(row[column] or ""), legacy_base64=True)
                for column in columns if row[column]
            }
            changes = {column: value for column, value in changes.items() if value != row[column]}
            if changes:
                assignments = ", ".join(f"{column} = ?" for column in changes)
                conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?",
                             (*changes.values(), row["id"]))

    secret_setting_keys = (
        "dingtalk_webhook", "dingtalk_secret", "mfa_totp_secret", "mfa_totp_pending_secret"
    )
    placeholders = ", ".join("?" for _ in secret_setting_keys)
    for row in conn.execute(
        f"SELECT key, value FROM system_settings WHERE key IN ({placeholders})",
        secret_setting_keys,
    ).fetchall():
        if not row["value"]:
            continue
        encrypted = normalize_secret_for_storage(
            str(row["value"]), legacy_base64=row["key"].startswith("dingtalk_")
        )
        if encrypted != row["value"]:
            conn.execute("UPDATE system_settings SET value = ? WHERE key = ?",
                         (encrypted, row["key"]))


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
    # Existing retention values are user settings. Never rewrite them at startup.

    default_settings = {
        "swagger_enabled": "0",
        "mfa_enabled": "0",
        "mfa_totp_secret": "",
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
