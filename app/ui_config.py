"""Business defaults and choices exposed to the frontend."""

from app.watermark import MAX_WATERMARK_TEXT_LENGTH, WATERMARK_DEFAULTS


JOB_FORM_DEFAULTS = {
    "name": "",
    "environment": "生产环境",
    "enabled": True,
    "schedule_mode": "calendar",
    "frequency": "daily",
    "days_of_week": [1],
    "days_of_month": [1],
    "times": ["09:05", "17:05"],
    "morning_enabled": True,
    "morning_time": "09:05",
    "afternoon_enabled": True,
    "afternoon_time": "17:05",
    "morning_random": False,
    "afternoon_random": False,
    "cron_expression": "0 9 * * *",
    "schedule_label": "",
    "report_title": "自动化巡检报告",
    "time_range_label": "最近24小时",
    "template_id": None,
    "dingtalk_enabled": False,
    "dingtalk_profile_id": None,
    "send_dingtalk_on_complete": True,
    "send_dingtalk_on_error": True,
    "browser_width": 3840,
    "browser_height": 2160,
    "browser_scale_factor": 1.5,
}

ITEM_FORM_DEFAULTS = {
    "name": "",
    "item_type": "grafana",
    "url": "",
    "section": "巡检截图",
    "auth_profile_id": "",
    "enabled": True,
    "real_browser_capture": True,
    "taskbar_template_key": "",
    "capture_mode": "viewport",
    "css_selector": "",
    "wait_selector": "",
    "wait_seconds": 3,
    "timeout_seconds": 120,
    "total_timeout_seconds": 240,
    "retry_count": 2,
    "browser_width": "",
    "browser_height": "",
    "sort_order": 100,
    "taskbar_enabled": True,
    **WATERMARK_DEFAULTS,
}

WATERMARK_RULES = {
    "text": {"max_length": MAX_WATERMARK_TEXT_LENGTH},
    "opacity": {"min": 0, "max": 255},
    "angle": {"min": -90, "max": 90, "base": WATERMARK_DEFAULTS["watermark_angle"]},
    "angle_offset": {"min": -30, "max": 30},
    "image_width": {"min": 800, "max": 7680},
    "image_height": {"min": 600, "max": 8640},
}

JOB_FREQUENCIES = (
    {"value": "daily", "label": "每天"},
    {"value": "weekdays", "label": "周一至周五"},
    {"value": "weekly", "label": "指定星期"},
    {"value": "monthly", "label": "每月几号"},
    {"value": "workday", "label": "工作日（含调休）", "requires_calendar": True},
    {"value": "non_workday", "label": "非工作日（含节假日）", "requires_calendar": True},
)
BROWSER_SCALE_FACTORS = (
    {"value": 1, "label": "100%"},
    {"value": 1.25, "label": "125%"},
    {"value": 1.5, "label": "150%"},
    {"value": 1.75, "label": "175%"},
    {"value": 2, "label": "200%"},
)
ITEM_TYPES = (
    {"value": "grafana", "label": "Grafana 看板"},
    {"value": "kibana", "label": "Kibana 仪表盘"},
    {"value": "web", "label": "普通网页"},
)
CAPTURE_MODES = (
    {"value": "viewport", "label": "可见区域"},
    {"value": "full_page", "label": "整个网页"},
    {"value": "selector", "label": "指定元素"},
)

RUN_PAGE_SIZES = (10, 20, 30, 40, 50, 100)
RUN_DEFAULT_PAGE_SIZE = RUN_PAGE_SIZES[0]

CLEANUP_SCOPES = (
    {"value": "inspection", "label": "巡检数据", "detail": "截图、Word 报告和运行记录"},
    {"value": "periodic", "label": "周期报告", "detail": "压缩包和生成记录"},
    {"value": "logs", "label": "日志文件", "detail": "系统运行日志"},
)
CLEANUP_PERIODS = (
    {"value": "7", "label": "7 天前"},
    {"value": "30", "label": "30 天前"},
    {"value": "90", "label": "90 天前"},
    {"value": "180", "label": "180 天前"},
    {"value": "365", "label": "1 年前"},
    {"value": "month", "label": "指定月份"},
)
CLEANUP_SCHEDULE_MODES = (
    {"value": "daily", "label": "每天"},
    {"value": "weekly", "label": "每周"},
    {"value": "monthly", "label": "每月"},
)


def frontend_config() -> dict:
    return {
        "job_defaults": JOB_FORM_DEFAULTS,
        "item_defaults": ITEM_FORM_DEFAULTS,
        "watermark_defaults": WATERMARK_DEFAULTS,
        "watermark_rules": WATERMARK_RULES,
        "job_frequencies": JOB_FREQUENCIES,
        "browser_scale_factors": BROWSER_SCALE_FACTORS,
        "item_types": ITEM_TYPES,
        "capture_modes": CAPTURE_MODES,
        "run_page_sizes": RUN_PAGE_SIZES,
        "run_default_page_size": RUN_DEFAULT_PAGE_SIZE,
        "cleanup_scopes": CLEANUP_SCOPES,
        "cleanup_periods": CLEANUP_PERIODS,
        "cleanup_schedule_modes": CLEANUP_SCHEDULE_MODES,
    }
