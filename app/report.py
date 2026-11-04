from __future__ import annotations

import logging
import os
import re
import tempfile
import hashlib
from io import BytesIO
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph

from app.settings import settings
from app.storage_paths import get_report_dir, resolve_artifact_path, unique_artifact_path
from app.utils import safe_name

logger = logging.getLogger("app.report")
ITEM_CAPTION_STYLE = "Inspection Item Caption"


class ReportValidationError(RuntimeError):
    pass


def _image_marker(run_id: int, item: dict[str, Any]) -> str:
    result_id = item.get("id") or item.get("item_id") or item.get("item_name") or "unknown"
    return f"inspection-run:{item.get('run_id') or run_id};result:{result_id}"


def _add_report_picture(run, path: Path, width, marker: str) -> None:
    shape = run.add_picture(str(path), width=width)
    shape._inline.docPr.set("descr", marker)


def validate_docx(
    path: Path,
    run_id: int,
    results: list[dict[str, Any]],
    expected_title: str = "",
    expected_date: str = "",
) -> None:
    """Reopen the saved file and match this run's screenshots to embedded media."""
    from PIL import Image

    doc = Document(path)
    if not any(paragraph.text.strip() for paragraph in doc.paragraphs):
        raise RuntimeError("Word 正文为空")
    if expected_title and doc.core_properties.title != expected_title:
        raise RuntimeError("Word 报告标题不正确")
    if expected_date and not any(expected_date in paragraph.text for paragraph in doc.paragraphs):
        raise RuntimeError("Word 报告日期缺失或不正确")
    if any("yyyy年mm月dd日" in paragraph.text for paragraph in doc.paragraphs):
        raise RuntimeError("Word 仍含有未替换的模板日期")
    success_items = [item for item in results if item.get("status") == "success"]
    expected = {
        _image_marker(run_id, item): item
        for item in success_items
    }
    if len(expected) != len(success_items):
        raise RuntimeError("Word 校验无法区分同名截图项")
    found: set[str] = set()
    section = ""
    for paragraph in doc.paragraphs:
        if is_header_paragraph(paragraph):
            section = paragraph.text.strip()
        for inline in paragraph._p.xpath(".//wp:inline"):
            marker = inline.docPr.get("descr") or ""
            if marker not in expected:
                continue
            if marker in found:
                raise RuntimeError(f"Word 中重复插入截图：{expected[marker].get('item_name')}")
            item = expected[marker]
            wanted_section = normalize_section_name(item.get("section") or item.get("item_name") or "")
            actual_section = normalize_section_name(section)
            if wanted_section and (
                not actual_section or not (wanted_section in actual_section or actual_section in wanted_section)
            ):
                raise RuntimeError(f"截图“{item.get('item_name')}”插入了错误章节：{section}")
            blips = inline.xpath(".//a:blip")
            if not blips:
                raise RuntimeError(f"截图“{item.get('item_name')}”在 Word 中没有图片数据")
            rid = blips[0].get(qn("r:embed"))
            image_part = doc.part.related_parts.get(rid)
            if image_part is None:
                raise RuntimeError(f"截图“{item.get('item_name')}”的图片关联丢失")
            source_path = resolve_artifact_path(item.get("file_path") or "")
            if not source_path.is_file():
                raise RuntimeError(f"截图“{item.get('item_name')}”的源文件缺失")
            if hashlib.sha256(image_part.blob).digest() != hashlib.sha256(source_path.read_bytes()).digest():
                raise RuntimeError(f"截图“{item.get('item_name')}”与 Word 内图片不一致")
            with Image.open(BytesIO(image_part.blob)) as image:
                image.verify()
            found.add(marker)
    missing = [str(item.get("item_name") or marker) for marker, item in expected.items() if marker not in found]
    if missing:
        raise RuntimeError(f"Word 缺少截图：{'、'.join(missing)}")
    for item in results:
        if item.get("status") != "success":
            label = (
                f"截图失败：{item.get('item_name') or '未命名截图项'}。"
                f"原因：{item.get('error_message') or '未提供失败原因'}"
            )
            if not any(label in paragraph.text for paragraph in doc.paragraphs):
                raise RuntimeError(f"Word 缺少失败项或失败原因：{item.get('item_name')}")
    if any("图片插入失败" in paragraph.text for paragraph in doc.paragraphs):
        raise RuntimeError("Word 中存在图片插入失败的占位文字")


def save_docx_atomic(
    doc: Document,
    output_path: Path,
    run_id: int,
    results: list[dict[str, Any]],
    expected_title: str = "",
    expected_date: str = "",
) -> None:
    """Validate a temporary Word before replacing the published report."""
    with tempfile.NamedTemporaryFile(dir=output_path.parent, suffix=".docx", delete=False) as temp_file:
        temp_path = Path(temp_file.name)
    try:
        doc.save(temp_path)
        try:
            validate_docx(temp_path, run_id, results, expected_title, expected_date)
        except Exception as exc:
            raise ReportValidationError(f"Word 校验失败：{exc}") from exc
        logger.info(f"Word 校验通过：当日报告的 {sum(item.get('status') == 'success' for item in results)} 张截图均已嵌入")
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)


def normalize_section_name(value: str) -> str:
    return re.sub(r"[\s\u3000:：，,。._\-]+", "", str(value or "")).lower()


def list_template_sections(template_path: Path | None = None) -> list[str]:
    if template_path is None:
        return []
    if not template_path.exists():
        return []

    try:
        doc = Document(template_path)
    except Exception as exc:
        logger.warning(f"读取 Word 模板章节失败: {exc}")
        return []

    sections: list[str] = []
    seen: set[str] = set()
    in_period = False
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        if text.startswith("巡检时间："):
            in_period = True
            continue
        if not in_period:
            continue
        if text[0].isdigit():
            key = normalize_section_name(text)
            if key and key not in seen:
                sections.append(text)
                seen.add(key)
    return sections


def is_header_paragraph(p) -> bool:
    text = p.text.strip()
    if not text:
        return False
    if p.style.name in {"Caption", ITEM_CAPTION_STYLE}:
        return False
    if p.style.name.startswith("Heading"):
        return True
    if text[0].isdigit():
        return True
    if text.startswith("巡检时间："):
        return True
    return False


def _item_caption_style(doc: Document):
    """Mark generated item names as captions without changing their appearance."""
    try:
        return doc.styles[ITEM_CAPTION_STYLE]
    except KeyError:
        style = doc.styles.add_style(ITEM_CAPTION_STYLE, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = doc.styles["Normal"]
        return style


def insert_paragraph_after(paragraph, text=None, style=None):
    new_p = OxmlElement('w:p')
    paragraph._p.addnext(new_p)
    new_para = Paragraph(new_p, paragraph._parent)
    if text:
        new_para.text = text
    if style:
        new_para.style = style
    return new_para



def _local_run_time(run: dict[str, Any], local_tz: ZoneInfo) -> datetime:
    try:
        started = datetime.fromisoformat(str(run.get("started_at") or ""))
    except ValueError:
        return datetime.now(local_tz)
    return started.astimezone(local_tz) if started.tzinfo else started.replace(tzinfo=local_tz)


def _run_period(run: dict[str, Any], local_tz: ZoneInfo) -> str:
    return "上午" if _local_run_time(run, local_tz).hour < 13 else "下午"


def _daily_periods(
    job: dict[str, Any], run: dict[str, Any], results: list[dict[str, Any]], local_tz: ZoneInfo,
) -> dict[str, dict[str, Any]]:
    """Use the latest completed run in each period, then replace this run's period."""
    from app.repository import list_report_runs_for_day, list_screenshot_results

    report_day = _local_run_time(run, local_tz).date()
    periods: dict[str, dict[str, Any]] = {}
    for previous in list_report_runs_for_day(job["id"], run["id"], report_day.isoformat()):
        if _local_run_time(previous, local_tz).date() != report_day:
            continue
        period = _run_period(previous, local_tz)
        if period not in periods:
            periods[period] = {"run": previous, "results": list_screenshot_results(previous["id"])}
    periods[_run_period(run, local_tz)] = {"run": run, "results": results}
    for period, snapshot in periods.items():
        snapshot["results"] = [
            {**item, "run_id": snapshot["run"]["id"], "report_period": period}
            for item in snapshot["results"]
        ]
    return periods


def _fill_template(doc: Document, periods: dict[str, dict[str, Any]]) -> None:
    """Fill each template section once, keeping every screenshot in that section."""
    headings: dict[tuple[str, str], Paragraph] = {}
    period_headings: dict[str, Paragraph] = {}
    used_headings: set[tuple[str, str]] = set()
    current_period = ""
    for paragraph in doc.paragraphs:
        label = paragraph.text.strip()
        if label.startswith("巡检时间："):
            current_period = "上午" if "上午" in label else "下午" if "下午" in label else ""
            if current_period:
                period_headings[current_period] = paragraph
        elif current_period and label and label[0].isdigit():
            headings[(current_period, normalize_section_name(label))] = paragraph

    for period, snapshot in periods.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in snapshot["results"]:
            grouped[normalize_section_name(item.get("section") or item.get("item_name") or "")].append(item)
        for section, items in grouped.items():
            target = headings.get((period, section))
            if target is None:
                raise ValueError(f"模板中没有“{period} / {items[0].get('section') or section}”章节")
            used_headings.add((period, section))
            # 清理章节标题与下一个标题之间的旧内容、占位符或碍眼的说明文字（如 〔...〕）
            while True:
                index = next(i for i, paragraph in enumerate(doc.paragraphs) if paragraph._p is target._p) + 1
                if index >= len(doc.paragraphs) or is_header_paragraph(doc.paragraphs[index]):
                    break
                element = doc.paragraphs[index]._element
                element.getparent().remove(element)
            anchor = target
            for item in items:
                name = str(item.get("item_name") or "未命名截图项")
                if item.get("status") != "success":
                    anchor = insert_paragraph_after(
                        anchor, f"截图失败：{name}。原因：{item.get('error_message') or '未提供失败原因'}"
                    )
                    anchor.runs[0].font.color.rgb = RGBColor(185, 28, 28)
                    continue
                image_path = resolve_artifact_path(item.get("file_path") or "")
                anchor = insert_paragraph_after(anchor, name, style=_item_caption_style(doc))
                anchor.paragraph_format.space_before = Pt(8)
                anchor.paragraph_format.space_after = Pt(3)
                anchor = insert_paragraph_after(anchor)
                anchor.alignment = WD_ALIGN_PARAGRAPH.CENTER
                anchor.paragraph_format.space_before = Pt(6)
                anchor.paragraph_format.space_after = Pt(14)
                try:
                    _add_report_picture(
                        anchor.add_run(), image_path, Inches(6.0), _image_marker(snapshot["run"]["id"], item)
                    )
                except Exception as exc:
                    raise ReportValidationError(f"截图“{name}”写入 Word 失败：{exc}") from exc

    for key, heading in headings.items():
        if key in used_headings:
            continue
        while True:
            index = next(i for i, paragraph in enumerate(doc.paragraphs) if paragraph._p is heading._p) + 1
            if index >= len(doc.paragraphs) or is_header_paragraph(doc.paragraphs[index]):
                break
            element = doc.paragraphs[index]._element
            element.getparent().remove(element)
        heading._element.getparent().remove(heading._element)
    for period in ("上午", "下午"):
        heading = period_headings.get(period)
        if heading is None:
            continue
        if period not in periods:
            insert_paragraph_after(heading, "尚未执行。")
        elif not periods[period]["results"]:
            insert_paragraph_after(heading, "本时段无截图结果。")


def generate_docx(
    job: dict[str, Any],
    run: dict[str, Any],
    results: list[dict[str, Any]],
) -> str:
    output_dir = get_report_dir(run["id"])

    logger.info(f"开始为任务 '{job['name']}' 生成巡检报告 Word 文档 (运行 ID: {run['id']})")

    # Rebuild both periods from run records so the afternoon cannot erase the morning.
    local_tz = ZoneInfo(settings.default_timezone)
    now_local = datetime.now(local_tz)
    started_dt = _local_run_time(run, local_tz)
    period = _run_period(run, local_tz)
    daily_periods = _daily_periods(job, run, results, local_tz)
    daily_results = [
        item for slot in ("上午", "下午") for item in daily_periods.get(slot, {}).get("results", [])
    ]
    report_date = started_dt.strftime("%Y年%m月%d日")
    document_title = str(job.get("report_title") or job.get("name") or "巡检报告")
    report_name = safe_name(str(job.get("report_title") or job.get("name") or "巡检报告"))
    stem = f"{report_name}-{started_dt.strftime('%Y-%m-%d')}"
    output_path = output_dir / f"{stem}.docx"
    logger.info(f"当前时间为 {now_local.strftime('%Y-%m-%d %H:%M:%S')}，匹配巡检时段: '{period}'")

    from app.report_templates import get_template, template_path as stored_template_path

    selected_template = None
    if job.get("template_id"):
        selected_template = get_template(int(job["template_id"]))
        if not selected_template:
            raise ReportValidationError("所选报告模板不存在，请在任务设置中重新选择")
    template_path = stored_template_path(selected_template) if selected_template else None
    if template_path and not template_path.is_file():
        raise ReportValidationError("所选报告模板文件缺失，请在任务设置中重新选择")
    template_sections = list_template_sections(template_path)
    if template_path and not template_sections:
        raise ReportValidationError("所选报告模板无法读取章节")
    template_normalized_set = {normalize_section_name(s) for s in template_sections}

    # 以当日报告全部时段决定版式，保留上午已有的自定义章节和截图。
    has_custom_sections = False
    if daily_results:
        has_custom_sections = any(
            normalize_section_name(item.get("section") or item.get("item_name") or "") not in template_normalized_set
            for item in daily_results
        )

    if template_path and has_custom_sections:
        raise ReportValidationError("截图项章节与所选报告模板不匹配，请调整章节或切换为自动排版")
    use_template = bool(template_path)

    from app.repository import get_previous_run_today
    source_path = None
    reuse_previous_path = False
    prev_run = get_previous_run_today(job["id"], run["id"])
    if prev_run and prev_run.get("report_path"):
        prev_report_path = resolve_artifact_path(prev_run["report_path"])
        # 同一任务当天更新同一份日报；其他任务重名时另取序号。
        if prev_report_path.exists() and prev_report_path.parent == output_dir.resolve():
            output_path = prev_report_path
            reuse_previous_path = True
            logger.info(f"查找到当天此任务较早执行记录 (运行 ID: {prev_run['id']})，将更新同一份日报: {output_path}")

    if use_template:
        source_path = template_path
        logger.info(f"使用所选模板重建当天上午、下午报告: {source_path}")
    else:
        logger.info("未选择 Word 模板，自动排版生成报告")

    if output_path == output_dir / f"{stem}.docx" and output_path.exists() and not reuse_previous_path:
        output_path = unique_artifact_path(output_dir, stem, ".docx")
            
    if source_path:
        try:
            doc = Document(source_path)
            doc.core_properties.title = document_title
            # 模板仅提供版式，正文标题始终使用任务配置的报告标题。
            today_cn_str = report_date
            for p in doc.paragraphs:
                if "yyyy年mm月dd日" in p.text or "yyyy-mm-dd" in p.text:
                    new_text = f"{today_cn_str}{document_title}"
                    # Keep the uploaded template's title style and paragraph layout.
                    if p.runs:
                        p.runs[0].text = new_text
                        for remaining in p.runs[1:]:
                            remaining.text = ""
                    else:
                        p.add_run(new_text)
                    logger.info(f"已更新模板的主标题为: {new_text}")
                    break
                        
            # 2. 删除旧版本曾插入的执行明细段落。模板正文只保留巡检项和截图。
            for p in list(doc.paragraphs):
                if "巡检执行时间：" in p.text or "巡检耗时：" in p.text:
                    p._element.getparent().remove(p._element)
                        
            _fill_template(doc, daily_periods)
            _insert_run_findings(doc, daily_results, run, daily_periods)
            save_docx_atomic(doc, output_path, run["id"], daily_results, document_title, report_date)
            logger.info(f"基于模板的报告已成功保存至: {output_path}")
            return str(output_path)
        except ReportValidationError:
            raise
        except Exception as exc:
            raise ReportValidationError(f"所选模板生成报告失败：{exc}") from exc
            
    # 4. 生成单独独立的 Word 文档（结构极简直接：主标题、巡检时间、各章节名称及截图）
    doc = Document()
    doc.core_properties.title = document_title
    _build_standalone_docx(doc, job, run, daily_periods, report_date)
    save_docx_atomic(doc, output_path, run["id"], daily_results, document_title, report_date)
    logger.info(f"单独的独立巡检报告已成功保存至: {output_path}")
    return str(output_path)


def _build_standalone_docx(
    doc: Document,
    job: dict[str, Any],
    run: dict[str, Any],
    periods: dict[str, dict[str, Any]],
    report_date: str,
) -> None:
    """Build the daily report with separate morning and afternoon sections."""
    _setup_styles(doc)
    raw_title = str(job.get('report_title') or job.get('name') or '巡检记录').strip()
    main_title = f"{report_date} {raw_title}"

    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run(main_title)
    title_run.bold = True
    title_run.font.name = "Microsoft YaHei"
    title_run.font.size = Pt(22)
    title_run.font.color.rgb = RGBColor(15, 44, 89)
    title_p.paragraph_format.space_before = Pt(8)
    title_p.paragraph_format.space_after = Pt(20)
    all_results = [item for period in ("上午", "下午") for item in periods.get(period, {}).get("results", [])]
    _insert_run_findings(doc, all_results, run, periods)

    for period in ("上午", "下午"):
        doc.add_heading(f"巡检时间：{period}", level=1)
        snapshot = periods.get(period)
        if snapshot is None:
            doc.add_paragraph("尚未执行。")
            continue
        if not snapshot["results"]:
            doc.add_paragraph("本时段无截图结果。")
            continue
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in snapshot["results"]:
            grouped[str(item.get("section") or item.get("item_name") or "巡检截图").strip()].append(item)
        for section_name, items in grouped.items():
            doc.add_heading(section_name, level=2)
            for item in items:
                name = str(item.get("item_name") or "未命名截图项")
                if item.get("status") != "success":
                    warning = doc.add_paragraph(
                        f"截图失败：{name}。原因：{item.get('error_message') or '未提供失败原因'}"
                    )
                    warning.runs[0].font.color.rgb = RGBColor(185, 28, 28)
                    continue
                image_path = resolve_artifact_path(item.get("file_path") or "")
                try:
                    caption = doc.add_paragraph(name, style=_item_caption_style(doc))
                    caption.paragraph_format.space_before = Pt(8)
                    caption.paragraph_format.space_after = Pt(3)
                    paragraph = doc.add_paragraph()
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    paragraph.paragraph_format.space_before = Pt(6)
                    paragraph.paragraph_format.space_after = Pt(14)
                    _add_report_picture(
                        paragraph.add_run(), image_path, Inches(6.5), _image_marker(snapshot["run"]["id"], item)
                    )
                except Exception as exc:
                    raise ReportValidationError(f"截图“{name}”写入 Word 失败：{exc}") from exc



def _insert_run_findings(
    doc: Document,
    results: list[dict[str, Any]],
    run: dict[str, Any],
    periods: dict[str, dict[str, Any]] | None = None,
) -> None:
    """清理历史遗留的概况段落；按用户要求，Word 报告中暂时不需要插入智能告警分析与无数据指标摘要"""
    prefixes = ("【监测结果】", "【告警】", "【截图内容】", "【截图失败汇总】", "【执行异常】")
    for paragraph in list(doc.paragraphs):
        if paragraph.text == "本次运行概况" or paragraph.text.startswith(prefixes):
            paragraph._element.getparent().remove(paragraph._element)
    return


def _setup_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10.5)
    for style_name in ("Title", "Heading 1", "Heading 2"):
        style = doc.styles[style_name]
        style.font.name = "Microsoft YaHei"
