from __future__ import annotations

import json
from typing import Any


def parse_time_parts(time_str: str, force_fixed: bool = False) -> tuple[int, int]:
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError("时间格式必须为 HH:MM")
    try:
        h = int(parts[0])
        if not (0 <= h <= 23):
            raise ValueError("小时数值超出范围，必须在 0 到 23 之间")
        
        m_str = parts[1].strip().upper()
        if m_str == "R":
            if force_fixed:
                m = 0
            else:
                import random
                m = random.randint(0, 59)
        else:
            m = int(m_str)
            if not (0 <= m <= 59):
                raise ValueError("分钟数值超出范围，必须在 0 到 59 之间")
        return h, m
    except ValueError as e:
        raise ValueError("时间数值超出范围，必须为有效的小时(0-23)和分钟(0-59)") from e


def build_inspection_schedule_label(schedule_config: dict[str, Any]) -> str:
    """根据配置构造可读的中文字符串"""
    frequency = schedule_config.get("frequency", "daily")
    freq_cn = "每天" if frequency == "daily" else "周一到周五"
    
    times = []
    for field in ("morning_time", "afternoon_time"):
        enabled_field = "morning_enabled" if field == "morning_time" else "afternoon_enabled"
        default_val = "09:05" if field == "morning_time" else "17:05"
        
        if schedule_config.get(enabled_field):
            t_val = str(schedule_config.get(field, default_val)).strip()
            if t_val.upper().endswith(":R"):
                t_val = t_val.upper().replace(":R", ":随机分钟")
            times.append(t_val)
        
    if not times:
        return "未启用定时巡检"
        
    return f"{freq_cn} {', '.join(times)} 巡检"


def parse_simple_inspection_schedule(payload: dict[str, Any]) -> tuple[str, str, str]:
    """
    解析前端表单的 payload 数据。
    返回: (空的旧表达式字段, schedule_label, schedule_config_json_str)
    """
    frequency = str(payload.get("frequency") or "daily").strip()
    
    # 支持布尔值或数值/字符串强转
    def to_bool(val: Any) -> bool:
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in {"1", "true", "yes", "on", "enabled"}

    morning_enabled = to_bool(payload.get("morning_enabled"))
    morning_time = str(payload.get("morning_time") or "09:05").strip()
    afternoon_enabled = to_bool(payload.get("afternoon_enabled"))
    afternoon_time = str(payload.get("afternoon_time") or "17:05").strip()
    
    if not morning_enabled and not afternoon_enabled:
        raise ValueError("上午巡检和下午巡检必须至少启用一个")
        
    # 校验时间合法性
    if morning_enabled:
        parse_time_parts(morning_time, force_fixed=True)
    if afternoon_enabled:
        parse_time_parts(afternoon_time, force_fixed=True)
        
    cfg = {
        "frequency": frequency,
        "morning_enabled": morning_enabled,
        "morning_time": morning_time,
        "afternoon_enabled": afternoon_enabled,
        "afternoon_time": afternoon_time
    }
    
    label = build_inspection_schedule_label(cfg)
    
    return "", label, json.dumps(cfg, ensure_ascii=False)

def validate_inspection_schedule(schedule_config: dict[str, Any]) -> None:
    """做基本的属性完整性校验"""
    morning_enabled = bool(schedule_config.get("morning_enabled"))
    afternoon_enabled = bool(schedule_config.get("afternoon_enabled"))
    if not morning_enabled and not afternoon_enabled:
        raise ValueError("必须至少启用上午或下午其中一个巡检时间点。")
    if morning_enabled:
        parse_time_parts(schedule_config.get("morning_time", ""))
    if afternoon_enabled:
        parse_time_parts(schedule_config.get("afternoon_time", ""))
