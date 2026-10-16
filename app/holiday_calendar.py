"""Chinese workday calendar with two independent online sources and a disk cache."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.settings import settings

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_online = False
_source = ""
_checked_at = ""
_years: dict[int, dict[str, bool]] = {}


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ops-inspection/1.0"})
    with urlopen(request, timeout=4) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("节假日响应过大")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("节假日响应格式错误")
    return data


def _primary_days(year: int, data: dict) -> dict[str, bool]:
    if data.get("code") != 0 or not isinstance(data.get("data"), list):
        raise ValueError("主接口返回的数据无效")
    days = {}
    for row in data["data"]:
        if not isinstance(row, dict):
            continue
        key = str(row.get("date") or "")
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if day.year != year or row.get("rest") not in (0, 1):
            continue
        days[key] = row["rest"] == 0
    if len(days) < 365:
        raise ValueError("主接口没有提供完整年度数据")
    return days


def _backup_days(year: int, data: dict) -> dict[str, bool]:
    if data.get("code") != 0 or not isinstance(data.get("holiday"), dict):
        raise ValueError("备用接口返回的数据无效")
    start = date(year, 1, 1)
    days = {(start + timedelta(days=i)).isoformat(): (start + timedelta(days=i)).isoweekday() <= 5 for i in range((date(year + 1, 1, 1) - start).days)}
    overrides = 0
    for key, row in data["holiday"].items():
        if not isinstance(row, dict):
            continue
        try:
            day = date.fromisoformat(f"{year}-{key}")
        except ValueError:
            continue
        if day.year != year or not isinstance(row.get("holiday"), bool):
            continue
        days[day.isoformat()] = not row["holiday"]
        overrides += 1
    if overrides < 5:
        raise ValueError("备用接口没有足够的年度调休数据")
    return days


def _cache_path(year: int) -> Path:
    return settings.data_dir / "holidays" / f"{year}.json"


def _load_cache(year: int) -> dict[str, bool] | None:
    try:
        path = _cache_path(year)
        if time.time() - path.stat().st_mtime > 30 * 86400:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        days = payload.get("days")
        if payload.get("year") == year and isinstance(days, dict) and len(days) >= 365 and all(isinstance(value, bool) for value in days.values()):
            return days
    except (OSError, ValueError, TypeError):
        pass
    return None


def refresh_year(year: int) -> bool:
    global _online, _source, _checked_at
    errors = []
    for source, template, parser in (("主接口", settings.holiday_primary_url, _backup_days), ("备用接口", settings.holiday_backup_url, _primary_days)):
        try:
            days = parser(year, _fetch_json(template.format(year=quote(str(year)))))
            path = _cache_path(year)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"year": year, "days": days}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            with _lock:
                _years[year] = days
                _online = True
                _source = source
                _checked_at = datetime.now().isoformat(timespec="seconds")
            return True
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    with _lock:
        _online = False
        _source = ""
        _checked_at = datetime.now().isoformat(timespec="seconds")
        if year not in _years:
            cached = _load_cache(year)
            if cached:
                _years[year] = cached
    logger.warning("节假日接口均不可用：%s", "; ".join(errors))
    return False


def refresh_current_year() -> None:
    refresh_year(datetime.now(ZoneInfo(settings.default_timezone)).year)


def status() -> dict:
    with _lock:
        return {"available": _online, "source": _source, "checked_at": _checked_at}


def is_workday(day: date) -> bool | None:
    with _lock:
        try:
            if time.time() - _cache_path(day.year).stat().st_mtime > 30 * 86400:
                return None
        except OSError:
            return None
        days = _years.get(day.year)
        if days is None:
            days = _load_cache(day.year)
            if days is not None:
                _years[day.year] = days
        return days.get(day.isoformat()) if days else None
