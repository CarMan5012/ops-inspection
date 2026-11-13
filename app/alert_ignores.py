"""Application-side Prometheus alert ignores. Original alert snapshots are retained."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.db import connect

logger = logging.getLogger(__name__)


def source_identity(alert: dict[str, Any], sources: list[dict[str, Any]], page_url: str = "") -> tuple[str, str, str]:
    uid = str(alert.get("source_uid") or "").strip()
    name = str(alert.get("source") or "").strip()
    source = next((item for item in sources if isinstance(item, dict) and (
        (uid and str(item.get("uid") or "") == uid) or
        (not uid and str(item.get("name") or "") == name)
    )), {})
    uid = uid or str(source.get("uid") or "").strip()
    name = name or str(source.get("name") or "").strip()
    stored_key = str(alert.get("source_key") or "").strip()
    if stored_key:
        return stored_key, uid, name
    parts = urlsplit(page_url)
    if parts.scheme in {"http", "https"} and parts.hostname:
        path = parts.path.split("/d/", 1)[0].split("/d-solo/", 1)[0].rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", "")), uid, name
    return "", uid, name


def instance_identity(alert: dict[str, Any]) -> str:
    labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
    identifying = {str(key): str(value) for key, value in labels.items()
                   if key not in {"alertname", "severity"}}
    if identifying:
        payload = json.dumps(identifying, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    instance = str(alert.get("instance") or "").strip()
    if instance:
        return hashlib.sha256(instance.encode("utf-8")).hexdigest()
    return ""


def instance_label(alert: dict[str, Any]) -> str:
    labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
    namespace = str(labels.get("namespace") or "").strip()
    for key in ("pod", "service", "node", "instance"):
        value = str(labels.get(key) or "").strip()
        if value:
            return f"{namespace + ' / ' if namespace else ''}{value}"[:200]
    return str(alert.get("instance") or "")[:200]


def list_ignore_rules() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("""SELECT * FROM alert_ignore_rules WHERE active = 1
                               ORDER BY created_at DESC, id DESC""").fetchall()
    now = datetime.now(timezone.utc)
    result = [dict(row) for row in rows]
    for rule in result:
        rule["expired"] = bool(rule["expires_at"] and datetime.fromisoformat(rule["expires_at"]) <= now)
    return result


def active_ignore_rules() -> list[dict[str, Any]]:
    return [rule for rule in list_ignore_rules() if not rule["expired"]]


def apply_ignore_rules(snapshot: dict[str, Any], page_url: str) -> dict[str, Any]:
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else []
    try:
        rules = active_ignore_rules()
    except Exception:
        logger.exception("无法读取 Prometheus 告警忽略规则，所有告警仍按未忽略处理")
        rules = []
    ignored = 0
    for alert in snapshot.get("alerts", []):
        if not isinstance(alert, dict):
            continue
        source_key, source_uid, _ = source_identity(alert, sources, page_url)
        alert["source_key"] = source_key
        alert["source_uid"] = source_uid
        instance = instance_identity(alert)
        match = next((rule for rule in rules if rule["source_key"] == source_key
                      and rule["source_uid"] == source_uid
                      and rule["alert_name"] == str(alert.get("name") or "")
                      and (rule["scope"] == "rule" or rule["instance"] == instance)), None)
        alert["ignored"] = bool(match)
        if match:
            ignored += 1
            alert["ignore_reason"] = match["reason"]
            alert["ignore_rule_id"] = match["id"]
    snapshot["ignored_count"] = ignored
    return snapshot


def create_ignore_rule(*, source_key: str, source_uid: str, source_name: str,
                       alert_name: str, scope: str, instance: str, instance_label: str, reason: str,
                       expires_at: str | None) -> dict[str, Any]:
    with connect() as conn:
        conn.execute("""UPDATE alert_ignore_rules SET active = 0, disabled_at = CURRENT_TIMESTAMP
                        WHERE active = 1 AND source_key = ? AND source_uid = ?
                          AND alert_name = ? AND scope = ? AND instance = ?""",
                     (source_key, source_uid, alert_name, scope, instance))
        cursor = conn.execute("""INSERT INTO alert_ignore_rules
            (source_key, source_uid, source_name, alert_name, scope, instance, instance_label, reason, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_key, source_uid, source_name, alert_name, scope, instance, instance_label, reason, expires_at))
        row = conn.execute("SELECT * FROM alert_ignore_rules WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def disable_ignore_rule(rule_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute("""UPDATE alert_ignore_rules SET active = 0, disabled_at = CURRENT_TIMESTAMP
                                 WHERE id = ? AND active = 1""", (rule_id,))
        return cursor.rowcount > 0
