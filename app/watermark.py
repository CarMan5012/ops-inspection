"""Screenshot watermark defaults and supported time formats."""

from datetime import datetime


TIME_FORMATS = (
    {"id": "datetime_seconds", "label": "2026-10-15 14:30:05", "strftime": "%Y-%m-%d %H:%M:%S"},
    {"id": "datetime_minutes", "label": "2026-10-15 14:30", "strftime": "%Y-%m-%d %H:%M"},
    {"id": "slash_seconds", "label": "2026/10/15 14:30:05", "strftime": "%Y/%m/%d %H:%M:%S"},
    {"id": "chinese_seconds", "label": "2026年10月15日 14:30:05", "strftime": "%Y年%m月%d日 %H:%M:%S"},
)
DEFAULT_TIME_FORMAT = TIME_FORMATS[0]["id"]
MAX_WATERMARK_TEXT_LENGTH = 200
WATERMARK_ALIGNMENTS = ("reference", "left", "center", "right")
WATERMARK_DEFAULTS = {
    "watermark_enabled": 1,
    "watermark_time_enabled": 1,
    "watermark_time_format": DEFAULT_TIME_FORMAT,
    "watermark_text": "王",
    "watermark_opacity": 20,
    "watermark_font_size": 53,
    "watermark_gap_x": 234,
    "watermark_gap_y": 594,
    "watermark_angle": 36,
    "watermark_alignment": "reference",
}


def validate_time_format(value: str) -> str:
    if value not in {option["id"] for option in TIME_FORMATS}:
        raise ValueError("水印时间格式无效")
    return value


def validate_watermark_alignment(value: str) -> str:
    if value not in WATERMARK_ALIGNMENTS:
        raise ValueError("水印文字对齐方式无效")
    return value


def format_watermark_time(now: datetime, format_id: str) -> str:
    selected = validate_time_format(format_id)
    pattern = next(option["strftime"] for option in TIME_FORMATS if option["id"] == selected)
    return now.strftime(pattern)


def watermark_options() -> list[dict[str, str]]:
    return [{"id": option["id"], "label": option["label"]} for option in TIME_FORMATS]
