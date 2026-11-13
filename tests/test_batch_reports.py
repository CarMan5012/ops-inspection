from contextlib import nullcontext
from datetime import date
import sqlite3
import sys
import types
from zipfile import ZipFile

import pytest


if "pysqlcipher3" not in sys.modules:
    sqlcipher = types.ModuleType("pysqlcipher3")
    sqlcipher.dbapi2 = sqlite3
    sys.modules["pysqlcipher3"] = sqlcipher

from app import batch_reports


@pytest.mark.parametrize("start,end", [
    ("2026-11-02", "2026-11-01"),
    ("2026-02-30", "2026-03-01"),
    ("2026-11", "2026-11-30"),
    ("2025-01-01", "2026-12-31"),
])
def test_invalid_date_range_is_rejected(start, end):
    with pytest.raises(ValueError):
        batch_reports.parse_range(start, end)


def test_selected_dates_zip_only_final_daily_reports(monkeypatch, tmp_path):
    from app.settings import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    first = data_dir / "reports/2026/11/01/daily.docx"
    second = data_dir / "reports/2026/11/01/daily-2.docx"
    next_day = data_dir / "reports/2026/11/02/next-day.docx"
    manual_template = data_dir / "reports/2026/11/01/manual-template.docx"
    for path, content in ((first, b"merged"), (second, b"other task"),
                          (next_day, b"next day"), (manual_template, b"template Word")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER, job_id INTEGER, started_at TEXT, report_path TEXT, schedule_slot TEXT)")
    conn.executemany("INSERT INTO run_records VALUES (?, ?, ?, ?, ?)", [
        (1, 10, "2026-11-01T09:00:00+08:00", str(first), "calendar-0900"),
        (2, 10, "2026-11-01T17:00:00+08:00", str(first), "calendar-1700"),
        (3, 11, "2026-11-01T18:00:00+08:00", str(second), "calendar-1800"),
        (4, 10, "2026-11-02T09:00:00+08:00", str(next_day), "calendar-0900"),
        (5, 10, "2026-11-03T09:00:00+08:00", str(next_day), "calendar-0900"),
        (6, 12, "2026-11-02T09:00:00+08:00", str(data_dir / "missing.docx"), "calendar-0900"),
        (7, 10, "2026-11-01T19:00:00+08:00", str(first), ""),
        (8, 10, "2026-11-01T20:00:00+08:00", str(manual_template), ""),
    ])
    monkeypatch.setattr(batch_reports, "connect", lambda: nullcontext(conn))

    start, end = batch_reports.parse_range("2026-11-01", "2026-11-02")
    reports = batch_reports.daily_reports(start, end)
    assert len(reports) == 3
    assert {run_id for _, _, run_id in reports} == {3, 4, 8}
    with batch_reports.build_archive(reports) as archive_file:
        with ZipFile(archive_file) as archive:
            assert set(archive.namelist()) == {
                "2026-11/next-day.docx",
                "2026-11/manual-template.docx",
                "2026-11/daily-2.docx",
            }
            assert archive.read("2026-11/manual-template.docx") == b"template Word"
    conn.close()


def test_one_day_range_is_inclusive():
    assert batch_reports.parse_range("2026-11-11", "2026-11-11") == (date(2026, 11, 11), date(2026, 11, 11))


def test_manual_template_word_is_available_by_month_and_date(monkeypatch, tmp_path):
    from app.main import api_daily_archive_info
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    report = tmp_path / "reports/2026/11/13/模板巡检-2026-11-13-手动巡检-1.docx"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"generated from template")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER, job_id INTEGER, started_at TEXT, report_path TEXT, schedule_slot TEXT)")
    conn.execute("INSERT INTO run_records VALUES (1, 7, '2026-11-13T10:00:00+08:00', ?, '')", (str(report),))
    monkeypatch.setattr(batch_reports, "connect", lambda: nullcontext(conn))

    assert api_daily_archive_info(months="2026-11")["count"] == 1
    assert api_daily_archive_info(start_date="2026-11-13", end_date="2026-11-13")["count"] == 1
    with batch_reports.build_archive(batch_reports.monthly_reports(["2026-11"])) as archive_file:
        with ZipFile(archive_file) as archive:
            assert archive.namelist() == ["2026-11/模板巡检-2026-11-13.docx"]
    conn.close()


def test_selected_months_skip_unselected_gap(monkeypatch, tmp_path):
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id INTEGER, job_id INTEGER, started_at TEXT, report_path TEXT, schedule_slot TEXT)")
    for run_id, month in enumerate(("2026-09", "2026-10", "2026-11"), start=1):
        year, number = month.split("-")
        path = tmp_path / "reports" / year / number / "08" / "daily.docx"
        path.parent.mkdir(parents=True)
        path.write_bytes(month.encode())
        conn.execute("INSERT INTO run_records VALUES (?, ?, ?, ?, ?)",
                     (run_id, 7, f"{month}-08T09:00:00+08:00", str(path), "calendar-0900"))
    manual = tmp_path / "reports/2026/11/08/manual.docx"
    manual.write_bytes(b"manual")
    conn.execute("INSERT INTO run_records VALUES (?, ?, ?, ?, ?)",
                 (4, 7, "2026-11-08T16:00:00+08:00", str(manual), ""))
    monkeypatch.setattr(batch_reports, "connect", lambda: nullcontext(conn))

    months = batch_reports.parse_months("2026-11,2026-09")
    assert months == ["2026-09", "2026-11"]
    reports = batch_reports.monthly_reports(months)
    with batch_reports.build_archive(reports) as archive_file:
        with ZipFile(archive_file) as archive:
            assert archive.namelist() == ["2026-09/daily.docx", "2026-11/manual.docx"]
    conn.close()


@pytest.mark.parametrize("raw", ["", "2026-13", "2026-00", "2026-1", "2026-11,,2026-12", ",".join(["2026-11"] * 13)])
def test_invalid_month_selection_is_rejected(raw):
    with pytest.raises(ValueError):
        batch_reports.parse_months(raw)


def test_human_readable_archive_names():
    assert batch_reports.archive_name_for_months(["2026-11"]) == "2026年11月巡检日报.zip"
    assert batch_reports.archive_name_for_months(["2026-09", "2026-11"]) == "2026年9月、11月巡检日报.zip"
    assert batch_reports.archive_name_for_months(["2026-11", "2027-01"]) == "2026年11月、2027年1月巡检日报.zip"
    assert batch_reports.archive_name_for_range(date(2026, 11, 5), date(2026, 11, 12)) == "2026年11月5日至12日巡检日报.zip"


def test_archive_info_preflight_returns_name_without_building_zip(monkeypatch, tmp_path):
    from app.main import api_daily_archive_info

    monkeypatch.setattr(batch_reports, "monthly_reports", lambda months: [("2026-11-08", tmp_path / "daily.docx", 1)])
    monkeypatch.setattr(batch_reports, "build_archive", lambda reports: pytest.fail("预检不应构建 ZIP"))
    assert api_daily_archive_info(months="2026-11") == {
        "count": 1, "filename": "2026年11月巡检日报.zip",
    }
