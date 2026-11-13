"""Download existing final daily Word reports by month or date range."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO
from zipfile import ZIP_STORED, ZipFile
import re

from app.db import connect
from app.settings import settings
from app.storage_paths import is_safe_path, resolve_artifact_path
from app.utils import safe_name


MAX_DAYS = 366
MAX_MONTHS = 12


def parse_range(start_text: str, end_text: str) -> tuple[date, date]:
    if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in (start_text, end_text)):
        raise ValueError("请选择有效的开始日期和结束日期")
    try:
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
    except ValueError:
        raise ValueError("请选择有效的开始日期和结束日期") from None
    if start > end:
        raise ValueError("结束日期不能早于开始日期")
    if (end - start).days >= MAX_DAYS:
        raise ValueError("单次最多下载连续 366 天的日报")
    return start, end


def parse_months(raw: str) -> list[str]:
    values = [value.strip() for value in raw.split(",")]
    if not values or len(values) > MAX_MONTHS or any(not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value) for value in values):
        raise ValueError("请选择 1–12 个有效月份")
    try:
        for value in values:
            date.fromisoformat(f"{value}-01")
    except ValueError:
        raise ValueError("请选择 1–12 个有效月份") from None
    return sorted(set(values))


def archive_name_for_months(months: list[str]) -> str:
    labels: list[str] = []
    previous_year = ""
    for value in months:
        year, month = value.split("-")
        labels.append(f"{year}年{int(month)}月" if year != previous_year else f"{int(month)}月")
        previous_year = year
    return "、".join(labels) + "巡检日报.zip"


def archive_name_for_range(start: date, end: date) -> str:
    first = f"{start.year}年{start.month}月{start.day}日"
    if start == end:
        return first + "巡检日报.zip"
    if start.year != end.year:
        last = f"{end.year}年{end.month}月{end.day}日"
    elif start.month != end.month:
        last = f"{end.month}月{end.day}日"
    else:
        last = f"{end.day}日"
    return f"{first}至{last}巡检日报.zip"


def daily_reports(start: date, end: date) -> list[tuple[str, Path, int]]:
    """Select existing Word reports regardless of how a run was triggered."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, job_id, started_at, report_path
               FROM run_records
               WHERE substr(started_at, 1, 10) BETWEEN ? AND ?
                 AND report_path IS NOT NULL AND report_path != ''
               ORDER BY id DESC""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return _select_daily_reports(rows)


def monthly_reports(months: list[str]) -> list[tuple[str, Path, int]]:
    """Query exactly the chosen months, including nonconsecutive years."""
    placeholders = ",".join("?" for _ in months)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT id, job_id, started_at, report_path
                FROM run_records
                WHERE substr(started_at, 1, 7) IN ({placeholders})
                  AND report_path IS NOT NULL AND report_path != ''
                ORDER BY id DESC""",
            months,
        ).fetchall()
    return _select_daily_reports(rows)


def _select_daily_reports(rows) -> list[tuple[str, Path, int]]:
    selected: dict[tuple[str, str, str], tuple[str, Path, int]] = {}
    for row in rows:
        run_id = int(row["id"])
        day = str(row["started_at"])[:10]
        path = resolve_artifact_path(row["report_path"])
        if path.suffix.lower() != ".docx" or not path.is_file() or not is_safe_path(path):
            continue
        # Rows are newest first: one final Word per task and day.
        group = ("job", str(row["job_id"]), day) if row["job_id"] is not None else ("path", str(path.resolve()), day)
        selected.setdefault(group, (day, path, run_id))
    return sorted(selected.values(), key=lambda item: (item[0], item[1].name, item[2]))


def build_archive(reports: list[tuple[str, Path, int]]) -> BinaryIO:
    """Use a temporary disk file so the archive is never saved as app data."""
    archive = TemporaryFile(mode="w+b", dir=settings.data_dir)
    try:
        used_names: set[str] = set()
        with ZipFile(archive, "w", compression=ZIP_STORED, allowZip64=True) as output:
            for day, path, run_id in reports:
                base = safe_name(re.sub(r"-手动巡检-\d+$", "", path.stem), f"report-{run_id}")
                name = f"{day[:7]}/{base}.docx"
                counter = 2
                while name in used_names:
                    name = f"{day[:7]}/{base}-{counter}.docx"
                    counter += 1
                used_names.add(name)
                output.write(path, name)
        archive.seek(0)
        return archive
    except Exception:
        archive.close()
        raise
