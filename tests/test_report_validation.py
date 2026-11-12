import sqlite3
import sys
import types
from pathlib import Path

if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from docx import Document
from docx.shared import Inches, Pt
from PIL import Image
import pytest

from app import report


def selected_template(tmp_path, monkeypatch):
    from app import report_templates

    template = tmp_path / "uploaded-template.docx"
    doc = Document()
    doc.add_paragraph("yyyy年mm月dd日 自动化巡检报告")
    for period in ("上午", "下午"):
        doc.add_paragraph(f"巡检时间：{period}")
        doc.add_paragraph("1. 服务器资源")
    doc.save(template)
    monkeypatch.setattr(report_templates, "get_template", lambda _id: {"id": 1, "stored_name": "0" * 32 + ".docx"})
    monkeypatch.setattr(report_templates, "template_path", lambda _template: template)
    return template


def test_no_data_does_not_add_an_automatic_word_diagnosis():
    doc = Document()
    doc.add_paragraph("自动化运维报告")
    report._insert_run_findings(doc, [{
        "item_name": "服务检查", "status": "success",
        "content_status": "no_data", "content_summary": "全部 1 个图表无数据，需确认",
    }], {"id": 1})
    assert not any("【截图内容】" in p.text for p in doc.paragraphs)


def test_word_validation_detects_missing_image_and_preserves_published_file(tmp_path):
    screenshot = tmp_path / "capture.png"
    Image.new("RGB", (120, 90), "green").save(screenshot)
    item = {
        "id": 7,
        "item_name": "Grafana",
        "section": "生产巡检",
        "status": "success",
        "file_path": str(screenshot),
    }
    doc = Document()
    doc.add_heading("生产巡检", level=2)
    report._add_report_picture(doc.add_paragraph().add_run(), screenshot, Inches(2), report._image_marker(3, item))
    target = tmp_path / "report.docx"
    report.save_docx_atomic(doc, target, 3, [item])
    original = target.read_bytes()
    report.validate_docx(target, 3, [item])

    missing = Document()
    missing.add_heading("生产巡检", level=2)
    with pytest.raises(RuntimeError, match="缺少截图"):
        report.save_docx_atomic(missing, target, 3, [item])
    assert target.read_bytes() == original


def test_word_validation_rejects_wrong_section(tmp_path):
    screenshot = tmp_path / "capture.png"
    Image.new("RGB", (120, 90), "blue").save(screenshot)
    item = {"id": 8, "item_name": "Grafana", "section": "生产巡检", "status": "success", "file_path": str(screenshot)}
    doc = Document()
    doc.add_heading("其他章节", level=2)
    report._add_report_picture(doc.add_paragraph().add_run(), screenshot, Inches(2), report._image_marker(4, item))
    path = tmp_path / "wrong.docx"
    doc.save(path)
    with pytest.raises(RuntimeError, match="错误章节"):
        report.validate_docx(path, 4, [item])


@pytest.mark.parametrize("item_name", ["01 CPU", "1. CPU 使用率"])
@pytest.mark.parametrize("use_template", [False, True])
def test_numbered_item_name_is_not_treated_as_report_section(
    tmp_path, monkeypatch, item_name, use_template,
):
    import app.repository as repository

    if use_template:
        selected_template(tmp_path, monkeypatch)
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)

    screenshot = tmp_path / "numbered-item.png"
    Image.new("RGB", (120, 90), "green").save(screenshot)
    section = "1. 服务器资源" if use_template else "系统资源"
    item = {
        "id": 42, "item_name": item_name, "section": section,
        "status": "success", "file_path": str(screenshot),
    }
    run = {"id": 420, "started_at": "2026-10-30T09:00:00+08:00"}
    job = {"id": 42, "name": "系统巡检", "report_title": "巡检报告"}
    if use_template:
        job["template_id"] = 1

    path = report.generate_docx(job, run, [item])
    report.validate_docx(Path(path), 420, [{**item, "section": item_name}] if not use_template else [item])
    doc = Document(path)
    expected_heading = f"1. {item_name}" if item_name == "01 CPU" else item_name
    assert [paragraph.text for paragraph in doc.paragraphs].count(expected_heading) == (0 if use_template else 1)
    assert len(doc.inline_shapes) == 1


def test_existing_template_inserts_and_validates_current_run(tmp_path, monkeypatch):
    import app.repository as repository

    selected_template(tmp_path, monkeypatch)
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    screenshot = tmp_path / "capture.png"
    Image.new("RGB", (120, 90), "green").save(screenshot)
    item = {"id": 99, "item_name": "服务器资源", "section": "1. 服务器资源", "status": "success", "file_path": str(screenshot)}
    run = {"id": 999, "started_at": "2026-10-08T16:00:00", "status": "success", "schedule_slot": "calendar-1600"}
    job = {"id": 1, "name": "服务器巡检", "report_title": "自动化巡检报告", "template_id": 1}
    path = report.generate_docx(job, run, [item])
    report.validate_docx(tmp_path / "自动化巡检报告-2026-10-08.docx", 999, [item])

    next_item = {**item, "id": 100}
    next_run = {**run, "id": 1000}
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: {"id": 999, "report_path": path})
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [{"id": 999, "started_at": run["started_at"], "report_path": path}])
    monkeypatch.setattr(repository, "list_screenshot_results", lambda _run_id: [item])
    merged_path = report.generate_docx(job, next_run, [next_item])
    assert merged_path == path
    report.validate_docx(tmp_path / "自动化巡检报告-2026-10-08.docx", 1000, [next_item])

    published_bytes = (tmp_path / "自动化巡检报告-2026-10-08.docx").read_bytes()
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: {"id": 1000, "report_path": path})
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [{"id": 1000, "started_at": next_run["started_at"], "report_path": path}])
    monkeypatch.setattr(report, "validate_docx", lambda *_args: (_ for _ in ()).throw(RuntimeError("缺图")))
    with pytest.raises(report.ReportValidationError, match="缺图"):
        report.generate_docx(job, {**run, "id": 1001}, [{**item, "id": 101}])
    assert (tmp_path / "自动化巡检报告-2026-10-08.docx").read_bytes() == published_bytes


def test_template_keeps_multiple_items_in_one_section(tmp_path, monkeypatch):
    import app.repository as repository

    template = selected_template(tmp_path, monkeypatch)
    section = report.list_template_sections(template)[0]
    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    pictures = []
    for index, color in enumerate(("red", "green"), 1):
        path = tmp_path / f"capture-{index}.png"
        Image.new("RGB", (120, 90), color).save(path)
        pictures.append({
            "id": index, "item_name": f"看板 {index}", "section": section,
            "status": "success", "file_path": str(path),
        })
    failed = {
        "id": 3, "item_name": "第三张看板", "section": section,
        "status": "failed", "file_path": "", "error_message": "页面加载超时",
    }
    run = {"id": 101, "started_at": "2026-10-10T09:05:00+08:00", "schedule_slot": "calendar-0905"}
    job = {"id": 1, "name": "生产巡检", "report_title": "生产日报", "template_id": 1}

    path = report.generate_docx(job, run, [*pictures, failed])
    doc = Document(path)
    texts = [paragraph.text for paragraph in doc.paragraphs]
    assert len(doc.inline_shapes) == 2
    assert "2026年10月10日生产日报" in texts
    assert "看板 1" not in texts and "看板 2" not in texts
    assert "截图失败：第三张看板。原因：页面加载超时" in texts

    afternoon_file = tmp_path / "afternoon.png"
    Image.new("RGB", (120, 90), "blue").save(afternoon_file)
    afternoon_item = {
        "id": 4, "item_name": "下午看板", "section": section,
        "status": "success", "file_path": str(afternoon_file),
    }
    afternoon_run = {"id": 102, "started_at": "2026-10-10T17:05:00+08:00", "schedule_slot": "calendar-1705"}
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: {**run, "report_path": path})
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [{**run, "report_path": path}])
    monkeypatch.setattr(repository, "list_screenshot_results", lambda _run_id: [*pictures, failed])
    second_path = report.generate_docx(job, afternoon_run, [afternoon_item])
    second_doc = Document(second_path)
    assert second_path == path
    assert len(second_doc.inline_shapes) == 3
    assert "截图失败：第三张看板。原因：页面加载超时" in [p.text for p in second_doc.paragraphs]


def test_auto_layout_uses_item_names_and_replaces_only_rerun_period(tmp_path, monkeypatch):
    import app.repository as repository

    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    previous: list[dict] = []
    results_by_run: dict[int, list[dict]] = {}
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: previous[-1] if previous else None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: list(reversed(previous)))
    monkeypatch.setattr(repository, "list_screenshot_results", lambda run_id: results_by_run[run_id])
    job = {"id": 2, "name": "生产巡检", "report_title": "生产日报"}
    morning = {"id": 201, "started_at": "2026-10-10T09:05:00+08:00", "schedule_slot": "calendar-0905"}
    afternoon = {"id": 202, "started_at": "2026-10-10T17:05:00+08:00", "schedule_slot": "calendar-1705"}
    rerun = {"id": 203, "started_at": "2026-10-10T17:30:00+08:00", "schedule_slot": "calendar-1705"}
    morning_file = tmp_path / "morning.png"
    afternoon_file = tmp_path / "afternoon.png"
    Image.new("RGB", (120, 90), "blue").save(morning_file)
    Image.new("RGB", (120, 90), "yellow").save(afternoon_file)
    morning_item = {
        "id": 11, "item_name": "上午看板", "section": "自定义业务章节",
        "status": "success", "file_path": str(morning_file),
    }
    failed_item = {
        "id": 12, "item_name": "下午看板", "section": "自定义业务章节",
        "status": "failed", "file_path": "", "error_message": "Grafana 返回 502",
    }
    afternoon_item = {**failed_item, "id": 13, "status": "success", "file_path": str(afternoon_file)}

    first_path = report.generate_docx(job, morning, [morning_item])
    results_by_run[morning["id"]] = [morning_item]
    previous.append({**morning, "report_path": first_path})
    second_path = report.generate_docx(job, afternoon, [failed_item])
    results_by_run[afternoon["id"]] = [failed_item]
    previous.append({**afternoon, "report_path": second_path})
    assert second_path == first_path
    second_doc = Document(second_path)
    second_text = [paragraph.text for paragraph in second_doc.paragraphs]
    assert "巡检时间：上午" in second_text and "巡检时间：下午" in second_text
    assert "截图失败：下午看板。原因：Grafana 返回 502" in second_text
    assert len(second_doc.inline_shapes) == 1

    third_path = report.generate_docx(job, rerun, [afternoon_item])
    assert third_path == first_path
    third_doc = Document(third_path)
    third_text = [paragraph.text for paragraph in third_doc.paragraphs]
    assert len(third_doc.inline_shapes) == 2
    assert third_text.count("1. 上午看板") == 1 and third_text.count("2. 下午看板") == 1
    assert "自定义业务章节" not in third_text
    assert not any("截图失败：下午看板" in text for text in third_text)


@pytest.mark.parametrize("slot,times,period", [
    ("calendar-0905", ["09:05"], "上午"),
    ("calendar-1705", ["17:05"], "下午"),
])
def test_single_enabled_period_omits_other_period_and_duplicate_panel_titles(
    tmp_path, monkeypatch, slot, times, period,
):
    import json
    import app.repository as repository

    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    names = ["主机资源与运行状态监控", "Pod 运行状态与资源监控 / Pod Status & Resource Monitoring"]
    results = []
    for index, name in enumerate(names, start=1):
        image = tmp_path / f"panel-{index}.png"
        Image.new("RGB", (120, 90), "blue").save(image)
        results.append({"id": index, "item_name": name, "section": name, "status": "success", "file_path": str(image)})
    job = {"id": 1, "name": "日常运检", "report_title": "巡检日报", "schedule_mode": "calendar",
           "schedule_config": json.dumps({"frequency": "daily", "times": times})}
    run = {"id": 10, "started_at": f"2026-10-10T{times[0]}:00+08:00", "schedule_slot": slot}
    path = report.generate_docx(job, run, results)
    doc = Document(path)
    texts = [paragraph.text for paragraph in doc.paragraphs]
    assert f"巡检时间：{period}" in texts
    assert f"巡检时间：{'下午' if period == '上午' else '上午'}" not in texts
    assert "尚未执行。" not in texts
    assert [text for text in texts if text in {f"1. {names[0]}", f"2. {names[1]}"}] == [
        f"1. {names[0]}", f"2. {names[1]}",
    ]
    assert len(doc.inline_shapes) == 2


def test_auto_report_has_one_black_title_per_screenshot(tmp_path, monkeypatch):
    import app.repository as repository

    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    image = tmp_path / "panel.png"
    Image.new("RGB", (1280, 720), "navy").save(image)
    item = {"id": 4, "item_name": "主机资源监控", "section": "旧章节", "status": "success", "file_path": str(image)}
    path = report.generate_docx(
        {"id": 1, "name": "巡检", "report_title": "巡检报告"},
        {"id": 4, "started_at": "2026-11-12T16:00:00+08:00"},
        [item],
    )
    doc = Document(path)
    paragraphs = [p for p in doc.paragraphs if p.text.strip()]
    assert [p.text for p in paragraphs].count("1. 主机资源监控") == 1
    assert "旧章节" not in [p.text for p in paragraphs]
    assert paragraphs[0].style.name == "Title"
    screenshot_heading = [p for p in paragraphs if p.text == "1. 主机资源监控"][0]
    assert screenshot_heading.style.name == "Heading 2"
    assert paragraphs[0].paragraph_format.line_spacing == 1.5
    assert paragraphs[0].paragraph_format.space_after == Pt(26)
    assert screenshot_heading.paragraph_format.line_spacing == 1.5
    assert screenshot_heading.paragraph_format.space_after == Pt(12)
    image_paragraph = next(p for p in doc.paragraphs if p._p.xpath(".//wp:inline"))
    assert image_paragraph.paragraph_format.space_before == Pt(6)
    assert all(run.font.color.rgb == (0, 0, 0) for p in paragraphs for run in p.runs if run.text)


def test_uploaded_template_report_normalizes_text_to_black(tmp_path, monkeypatch):
    import app.repository as repository
    from docx.shared import RGBColor

    template = selected_template(tmp_path, monkeypatch)
    source = Document(template)
    for paragraph in source.paragraphs:
        for run in paragraph.runs:
            run.font.color.rgb = RGBColor(30, 80, 180)
    source.save(template)
    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    image = tmp_path / "panel.png"
    Image.new("RGB", (1280, 720), "navy").save(image)
    item = {"id": 5, "item_name": "主机资源监控", "section": "1. 服务器资源", "status": "success", "file_path": str(image)}
    path = report.generate_docx(
        {"id": 1, "name": "巡检", "report_title": "巡检报告", "template_id": 1},
        {"id": 5, "started_at": "2026-11-12T16:00:00+08:00"},
        [item],
    )
    doc = Document(path)
    assert len(doc.inline_shapes) == 1
    assert all(run.font.color.rgb == (0, 0, 0) for p in doc.paragraphs for run in p.runs if run.text)
    assert all(run.font.size is not None for p in doc.paragraphs for run in p.runs if run.text)
    assert any(run.font.color.rgb == RGBColor(30, 80, 180) for p in Document(template).paragraphs for run in p.runs)


def test_single_enabled_period_removes_unused_template_block(tmp_path, monkeypatch):
    import json
    import app.repository as repository

    selected_template(tmp_path, monkeypatch)
    monkeypatch.setattr(report, "get_report_dir", lambda _run_id: tmp_path)
    monkeypatch.setattr(repository, "get_previous_run_today", lambda *_args: None)
    monkeypatch.setattr(repository, "list_report_runs_for_day", lambda *_args: [])
    job = {"id": 1, "name": "巡检", "report_title": "巡检日报", "template_id": 1,
           "schedule_mode": "calendar", "schedule_config": json.dumps({"frequency": "daily", "times": ["17:05"]})}
    run = {"id": 11, "started_at": "2026-10-10T17:05:00+08:00", "schedule_slot": "calendar-1705"}
    image = tmp_path / "afternoon.png"
    Image.new("RGB", (120, 90), "blue").save(image)
    item = {"id": 1, "item_name": "服务器看板", "section": "1. 服务器资源", "status": "success", "file_path": str(image)}
    path = report.generate_docx(job, run, [item])
    doc = Document(path)
    texts = [paragraph.text for paragraph in doc.paragraphs]
    assert "巡检时间：下午" in texts
    assert "巡检时间：上午" not in texts
    assert "尚未执行。" not in texts
    assert "服务器看板" not in texts
    assert len(doc.inline_shapes) == 1
