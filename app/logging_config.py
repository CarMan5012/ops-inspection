import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional


# ANSI 颜色转义码
RESET = "\033[0m"
DIM = "\033[2m"
GRAY = "\033[90m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD_RED = "\033[1;31m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"

LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO ",
    logging.WARNING: "WARN ",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "FATAL",
}

LEVEL_COLORS = {
    logging.DEBUG: CYAN,
    logging.INFO: GREEN,
    logging.WARNING: YELLOW,
    logging.ERROR: BOLD_RED,
    logging.CRITICAL: MAGENTA,
}


def _simplify_module_name(name: str) -> str:
    """提取简明且对齐的模块名称（固定截断/填充至 10 个字符）"""
    if name == "app":
        short = "core"
    elif name.startswith("app."):
        short = name[4:]
    elif name.startswith("uvicorn"):
        short = "web"
    else:
        short = name.split(".")[-1]

    if len(short) > 10:
        short = short[:10]
    return f"{short:<10}"


class AppLogFormatter(logging.Formatter):
    """
    统一的整齐对齐日志格式化器
    - use_color=True: 终端彩色输出，视觉辨识度高
    - use_color=False: 纯净文本对齐，适合持久化日志文件
    """

    def __init__(self, use_color: bool = False, timezone: str = "Asia/Shanghai"):
        super().__init__()
        self.use_color = use_color
        try:
            self.tz = ZoneInfo(timezone)
        except Exception:
            self.tz = None

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        if self.tz:
            dt = datetime.fromtimestamp(record.created, tz=self.tz)
        else:
            dt = datetime.fromtimestamp(record.created)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        asctime = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level_label = LEVEL_NAMES.get(record.levelno, f"{record.levelname[:5]:<5}")
        module_tag = _simplify_module_name(record.name)
        message = record.getMessage()

        if self.use_color:
            color = LEVEL_COLORS.get(record.levelno, RESET)
            header = f"{GRAY}{asctime}{RESET} {color}[{level_label}]{RESET} {BLUE}[{module_tag}]{RESET}"
        else:
            header = f"{asctime} [{level_label}] [{module_tag}]"

        result = f"{header} {message}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                result = f"{result}\n{record.exc_text}"

        if record.stack_info:
            result = f"{result}\n{self.formatStack(record.stack_info)}"

        return result


def is_color_supported(stream=None) -> bool:
    """检测控制台是否支持颜色输出"""
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    if hasattr(stream, "isatty") and stream.isatty():
        return True
    if os.getenv("DOCKER_CONTAINER") or os.path.exists("/.dockerenv"):
        return True
    return False
