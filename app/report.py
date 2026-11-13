from __future__ import annotations

import logging
import json
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
from app.storage_paths import get_report_dir, resolve_artifact_path
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


def replace_template_title_date(doc: Document, report_date: str) -> str:
    """Replace only the date placeholder, retaining the template's title runs."""
    for paragraph in doc.paragraphs:
        full_text = "".join(run.text for run in paragraph.runs)
        token = next((value for value in ("yyyy年mm月dd日", "yyyy-mm-dd") if value in full_text), None)
        if token is None:
            continue
        start = full_text.index(token)
        end = start + len(token)
        offset = 0
        first = last = None
        for index, run in enumerate(paragraph.runs):
            next_offset = offset + len(run.text)
            if first is None and offset <= start < next_offset:
                first = (index, start - offset)
            if offset < end <= next_offset:
                last = (index, end - offset)
                break
            offset = next_offset
        if first is None or last is None:
            raise ReportValidationError("模板标题日期占位符无法替换")
        first_index, first_offset = first
        last_index, last_offset = last
        runs = paragraph.runs
        if first_index == last_index:
            value = runs[first_index].text
            runs[first_index].text = value[:first_offset] + report_date + value[last_offset:]
        else:
            runs[first_index].text = runs[first_index].text[:first_offset] + report_date
            for index in range(first_index + 1, last_index):
                runs[index].text = ""
            runs[last_index].text = runs[last_index].text[last_offset:]
        return (full_text[:start] + full_text[end:]).strip()
    return ""


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
    slot = str(run.get("schedule_slot") or "")
    if slot == "morning":
        return "上午"
    if slot == "afternoon":
        return "下午"
    if slot.startswith("calendar-") and len(slot) >= 11 and slot[9:11].isdigit():
        return "上午" if int(slot[9:11]) < 12 else "下午"
    return "上午" if _local_run_time(run, local_tz).hour < 12 else "下午"


def is_daily_schedule_slot(slot: Any) -> bool:
    value = str(slot or "")
    return value in {"morning", "afternoon"} or value.startswith("calendar-")


def _report_periods(job: dict[str, Any], run: dict[str, Any]) -> tuple[str, ...]:
    """Show only configured periods for scheduled reports; one for manual runs."""
    local_tz = ZoneInfo(settings.default_timezone)
    if not is_daily_schedule_slot(run.get("schedule_slot")):
        return (_run_period(run, local_tz),)
    try:
        config = json.loads(job.get("schedule_config") or "{}")
    except (TypeError, ValueError):
        config = {}
    if job.get("schedule_mode") == "calendar":
        times = config.get("times")
        if isinstance(times, list) and times:
            try:
                periods = {"上午" if int(str(value)[:2]) < 12 else "下午" for value in times}
            except ValueError:
                periods = set()
            if periods:
                return tuple(period for period in ("上午", "下午") if period in periods)
    elif job.get("schedule_mode") == "simple":
        periods = {"上午" if config.get("morning_enabled") else "", "下午" if config.get("afternoon_enabled") else ""}
        selected = tuple(period for period in ("上午", "下午") if period in periods)
        if selected:
            return selected
    return ("上午", "下午")


def _daily_periods(
    job: dict[str, Any], run: dict[str, Any], results: list[dict[str, Any]], local_tz: ZoneInfo,
) -> dict[str, dict[str, Any]]:
    """Use the latest completed run in each period, manual or scheduled."""
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
    """Insert results without editing or removing template paragraphs or styles."""
    headings: dict[tuple[str, str], Paragraph] = {}
    current_period = ""
    for paragraph in doc.paragraphs:
        label = paragraph.text.strip()
        if label.startswith("巡检时间："):
            current_period = "上午" if "上午" in label else "下午" if "下午" in label else ""
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
            anchor = target
            for item in items:
                name = str(item.get("item_name") or "未命名截图项")
                if item.get("status") != "success":
                    anchor = insert_paragraph_after(
                        anchor, f"截图失败：{name}。原因：{item.get('error_message') or '未提供失败原因'}"
                    )
                    continue
                image_path = resolve_artifact_path(item.get("file_path") or "")
                anchor = insert_paragraph_after(anchor)
                try:
                    _add_report_picture(
                        anchor.add_run(), image_path, Inches(6.0), _image_marker(snapshot["run"]["id"], item)
                    )
                except Exception as exc:
                    raise ReportValidationError(f"截图“{name}”写入 Word 失败：{exc}") from exc

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
    visible_periods = tuple(slot for slot in ("上午", "下午") if slot in daily_periods)
    if not job.get("template_id"):
        # Existing records may still carry old hand-entered chapter names.
        # Rebuild numbering from the complete day's screenshots on every run.
        caption_number = 0
        for slot in ("上午", "下午"):
            snapshot = daily_periods.get(slot)
            if snapshot is None:
                continue
            numbered_results = []
            for item in snapshot["results"]:
                name = str(item.get("item_name") or item.get("section") or "巡检截图").strip()
                if item.get("status") == "success":
                    caption_number += 1
                    name = re.sub(r"^\d+\s*[.．、]\s*", "", name).strip() or "巡检截图"
                    name = f"{caption_number}. {name}"
                numbered_results.append({**item, "section": name})
            snapshot["results"] = numbered_results
    daily_results = [item for slot in ("上午", "下午")
                     for item in daily_periods.get(slot, {}).get("results", [])]
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

    source_path = None
    if output_path.exists():
        logger.info("更新当前任务的当日日报: %s", output_path)

    if use_template:
        source_path = template_path
        logger.info(f"使用所选模板重建当天上午、下午报告: {source_path}")
    else:
        logger.info("未选择 Word 模板，自动排版生成报告")

            
    if source_path:
        try:
            doc = Document(source_path)
            template_title = replace_template_title_date(doc, report_date)
            if template_title:
                document_title = template_title
                report_name = safe_name(template_title)
                output_path = output_dir / f"{report_name}-{started_dt.strftime('%Y-%m-%d')}.docx"
            doc.core_properties.title = document_title
                        
            _fill_template(doc, daily_periods)
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
    _build_standalone_docx(doc, job, run, daily_periods, report_date, visible_periods)
    _format_report_typography(doc, report_date, document_title, auto_layout=True)
    save_docx_atomic(doc, output_path, run["id"], daily_results, document_title, report_date)
    logger.info(f"单独的独立巡检报告已成功保存至: {output_path}")
    return str(output_path)


def _build_standalone_docx(
    doc: Document,
    job: dict[str, Any],
    run: dict[str, Any],
    periods: dict[str, dict[str, Any]],
    report_date: str,
    visible_periods: tuple[str, ...],
) -> None:
    """Build the daily report with separate morning and afternoon sections."""
    _setup_styles(doc)
    raw_title = str(job.get('report_title') or job.get('name') or '巡检记录').strip()
    main_title = f"{report_date} {raw_title}"

    title_p = doc.add_paragraph(style="Title")
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run(main_title)
    title_run.bold = True
    title_run.font.name = "Microsoft YaHei"
    title_run.font.size = Pt(20)
    title_run.font.color.rgb = RGBColor(0, 0, 0)
    title_p.paragraph_format.space_before = Pt(6)
    title_p.paragraph_format.space_after = Pt(26)
    all_results = [item for period in ("上午", "下午") for item in periods.get(period, {}).get("results", [])]
    _insert_run_findings(doc, all_results, run, periods)

    for period in visible_periods:
        doc.add_heading(f"巡检时间：{period}", level=1)
        snapshot = periods.get(period)
        if snapshot is None:
            doc.add_paragraph("尚未执行。")
            continue
        if not snapshot["results"]:
            doc.add_paragraph("本时段无截图结果。")
            continue
        for item in snapshot["results"]:
            name = str(item.get("item_name") or "未命名截图项").strip()
            if item.get("status") != "success":
                doc.add_paragraph(f"截图失败：{name}。原因：{item.get('error_message') or '未提供失败原因'}")
                continue
            heading = doc.add_heading(str(item.get("section") or name), level=2)
            heading.paragraph_format.keep_with_next = True
            image_path = resolve_artifact_path(item.get("file_path") or "")
            try:
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
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.space_after = Pt(6)
    for style_name, size, before, after in (
        ("Title", 20, 6, 26),
        ("Heading 1", 14, 12, 10),
        ("Heading 2", 12, 12, 12),
        ("Heading 3", 11, 8, 8),
    ):
        style = doc.styles[style_name]
        style.font.name = "Microsoft YaHei"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.line_spacing = 1.5
        style.paragraph_format.keep_with_next = True


def _format_report_typography(doc: Document, report_date: str, document_title: str, *, auto_layout: bool = False) -> None:
    """Use one readable black type scale for generated and uploaded-template reports."""
    black = RGBColor(0, 0, 0)
    for style in doc.styles:
        if hasattr(style, "font"):
            style.font.name = "Microsoft YaHei"
            style.font.color.rgb = black
            style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Microsoft YaHei")
    title_texts = {f"{report_date}{document_title}", f"{report_date} {document_title}"}

    def format_paragraph(paragraph: Paragraph) -> None:
        label = paragraph.text.strip()
        if not label:
            return
        if label in title_texts:
            size, before, after = 20, 6, 26 if auto_layout else 16
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for owner in (paragraph._p, paragraph.style._element):
                for border in owner.xpath("./w:pPr/w:pBdr"):
                    border.getparent().remove(border)
            for spacing in paragraph.style._element.xpath("./w:rPr/w:spacing"):
                spacing.getparent().remove(spacing)
        elif label.startswith("巡检时间："):
            size, before, after = 14, 12 if auto_layout else 14, 10 if auto_layout else 8
        elif is_header_paragraph(paragraph):
            size, before, after = 12, 12 if auto_layout else 11, 12 if auto_layout else 5
        else:
            size, before, after = 10.5, 0, 6
        paragraph.paragraph_format.space_before = Pt(before)
        paragraph.paragraph_format.space_after = Pt(after)
        if size >= 12:
            paragraph.paragraph_format.keep_with_next = True
            if auto_layout:
                paragraph.paragraph_format.line_spacing = 1.5
        for run in paragraph.runs:
            if not run.text:
                continue
            run.font.name = "Microsoft YaHei"
            run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Microsoft YaHei")
            run.font.size = Pt(size)
            run.font.color.rgb = black
            if size >= 12:
                run.bold = True

    def format_table(table) -> None:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    format_paragraph(paragraph)
                for nested in cell.tables:
                    format_table(nested)

    for paragraph in doc.paragraphs:
        format_paragraph(paragraph)
    for table in doc.tables:
        format_table(table)
    for section in doc.sections:
        for part in (section.header, section.footer):
            for paragraph in part.paragraphs:
                format_paragraph(paragraph)
            for table in part.tables:
                format_table(table)
