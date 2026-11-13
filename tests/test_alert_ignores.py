import json
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import db
from app.alert_ignores import (
    active_ignore_rules, apply_ignore_rules, create_ignore_rule, disable_ignore_rule,
    instance_identity, list_ignore_rules,
)
from app.monitoring import summarize_monitoring
from fastapi import HTTPException


def test_ignored_instance_does_not_hide_other_pod_or_source(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "data_dir", tmp_path)
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "app.db")
    monkeypatch.setattr(db.settings, "ensure_dirs", lambda: tmp_path.mkdir(exist_ok=True))
    monkeypatch.setattr(db, "_migrate_plain_database", lambda: None)
    db.init_db()

    first = {"name": "PodDown", "source_uid": "prom", "source": "Prometheus",
             "instance": "shared-target", "state": "firing",
             "labels": {"alertname": "PodDown", "instance": "shared-target", "pod": "pod-a"}}
    second = {**first, "labels": {**first["labels"], "pod": "pod-b"}}
    assert instance_identity(first) != instance_identity(second)
    create_ignore_rule(source_key="http://grafana", source_uid="prom", source_name="Prometheus",
                       alert_name="PodDown", scope="instance", instance=instance_identity(first),
                       instance_label="pod-a", reason="已确认无需处理", expires_at=None)
    snapshot = {"alerts": [dict(first), dict(second)],
                "sources": [{"uid": "prom", "name": "Prometheus", "alert_url": "http://prom:9090/alerts"}]}
    apply_ignore_rules(snapshot, "http://grafana/d/board/name")
    assert [alert["ignored"] for alert in snapshot["alerts"]] == [True, False]
    summary = summarize_monitoring([{"monitor_status": "alert", "monitor_data": json.dumps(snapshot)}])
    assert summary["status"] == "alert"
    assert len(summary["alerts"]) == 1 and len(summary["ignored"]) == 1

    other_source = {"alerts": [dict(first)], "sources": [
        {"uid": "prom", "name": "Prometheus", "alert_url": "http://another-prom:9090/alerts"}]}
    apply_ignore_rules(other_source, "http://other-grafana/d/board/name")
    assert other_source["alerts"][0]["ignored"] is False

    all_ignored = {"alerts": [dict(first)], "sources": snapshot["sources"]}
    apply_ignore_rules(all_ignored, "http://grafana/d/board/name")
    all_ignored_summary = summarize_monitoring([{
        "monitor_status": "normal", "monitor_data": json.dumps(all_ignored),
    }])
    assert all_ignored_summary["status"] == "normal"
    assert "已忽略 1 条" in all_ignored_summary["text"]


def test_rule_expiry_and_cancellation_restore_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "data_dir", tmp_path)
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "app.db")
    monkeypatch.setattr(db.settings, "ensure_dirs", lambda: tmp_path.mkdir(exist_ok=True))
    monkeypatch.setattr(db, "_migrate_plain_database", lambda: None)
    db.init_db()
    expired = create_ignore_rule(source_key="http://grafana", source_uid="prom", source_name="Prometheus",
                                 alert_name="PodDown", scope="rule", instance="", instance_label="",
                                 reason="临时忽略", expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    assert list_ignore_rules()[0]["expired"] is True
    assert active_ignore_rules() == []
    assert disable_ignore_rule(expired["id"])
    assert list_ignore_rules() == []


def test_ignore_api_uses_alert_from_saved_run(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(db, "sqlite3", sqlite3)
    monkeypatch.setattr(db.settings, "data_dir", tmp_path)
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "app.db")
    monkeypatch.setattr(db.settings, "ensure_dirs", lambda: tmp_path.mkdir(exist_ok=True))
    monkeypatch.setattr(db, "_migrate_plain_database", lambda: None)
    db.init_db()
    alert = {"name": "CPUHigh", "source_uid": "prom", "source": "Prometheus",
             "instance": "host-a", "labels": {"alertname": "CPUHigh", "instance": "host-a"}}
    result = {"id": 17, "item_id": 2, "monitor_data": json.dumps({"alerts": [alert],
              "sources": [{"uid": "prom", "name": "Prometheus"}]})}
    monkeypatch.setattr(main, "list_screenshot_results", lambda run_id: [result] if run_id == 5 else [])
    monkeypatch.setattr(main, "get_screenshot_item", lambda item_id: {"url": "http://grafana/d/board"})
    saved = main.api_create_alert_ignore({"run_id": 5, "result_id": 17, "alert_index": 0,
                                          "scope": "instance", "reason": "测试确认无需处理"})
    assert saved["rule"]["source_key"] == "http://grafana"
    assert saved["rule"]["instance_label"] == "host-a"
    assert len(active_ignore_rules()) == 1
    try:
        main.api_create_alert_ignore({"run_id": 5, "result_id": 17, "alert_index": -1,
                                      "scope": "rule", "reason": "无效下标"})
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("负数告警下标不得创建规则")
