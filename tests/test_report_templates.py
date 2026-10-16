import sqlite3
import sys
import types
from io import BytesIO

import pytest
from docx import Document

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import report_templates


def template_bytes(afternoon_section="1. 服务器资源"):
    doc = Document()
    doc.add_paragraph("yyyy年mm月dd日 巡检报告")
    doc.add_paragraph("巡检时间：上午")
    doc.add_paragraph("1. 服务器资源")
    doc.add_paragraph("巡检时间：下午")
    doc.add_paragraph(afternoon_section)
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def test_upload_template_is_validated_stored_and_cannot_delete_while_selected(tmp_path, monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE report_templates (id INTEGER PRIMARY KEY, name TEXT UNIQUE, stored_name TEXT UNIQUE,
            original_name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE report_jobs (id INTEGER PRIMARY KEY, template_id INTEGER);
    """)
    monkeypatch.setattr(report_templates, "connect", lambda: db)
    monkeypatch.setattr(report_templates.settings, "data_dir", tmp_path)

    uploaded = report_templates.create_template("生产日报", "模板.docx", template_bytes())
    assert report_templates.template_path(uploaded).is_file()
    assert report_templates.list_templates()[0]["name"] == "生产日报"
    assert report_templates.inspect_template(report_templates.template_path(uploaded).read_bytes()) == ["1. 服务器资源"]

    db.execute("INSERT INTO report_jobs (template_id) VALUES (?)", (uploaded["id"],))
    with pytest.raises(ValueError, match="正在使用"):
        report_templates.delete_template(uploaded["id"])
    db.execute("DELETE FROM report_jobs")
    report_templates.delete_template(uploaded["id"])
    assert not report_templates.template_path(uploaded).exists()
    db.close()


def test_upload_rejects_incompatible_sections():
    with pytest.raises(ValueError, match="必须一致"):
        report_templates.inspect_template(template_bytes("2. 网络设备"))
    with pytest.raises(ValueError, match="DOCX"):
        report_templates.inspect_template(b"not a word file")
