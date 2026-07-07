from __future__ import annotations

import json
from zoneinfo import ZoneInfo
from typing import Any
from apscheduler.triggers.cron import CronTrigger


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


def build_inspection_triggers(schedule_config: dict[str, Any], timezone: ZoneInfo) -> list[CronTrigger]:
    """根据结构化配置构建上午/下午对应的 CronTrigger 触发器列表"""
    triggers = []
    frequency = schedule_config.get("frequency", "daily")
    day_of_week = "*" if frequency == "daily" else "mon-fri"
    
    # 检查是否包含随机 R 分钟配置，如果有，为触发器生成统一的随机分钟数以防笛卡尔积重复执行
    common_rand_min = None
    morning_time = str(schedule_config.get("morning_time") or "").strip()
    afternoon_time = str(schedule_config.get("afternoon_time") or "").strip()
    
    if (schedule_config.get("morning_enabled") and morning_time.upper().endswith(":R")) or \
       (schedule_config.get("afternoon_enabled") and afternoon_time.upper().endswith(":R")):
        import random
        common_rand_min = random.randint(0, 59)

    if schedule_config.get("morning_enabled"):
        t_str = schedule_config.get("morning_time", "09:05")
        h, m = parse_time_parts(t_str)
        if t_str.upper().endswith(":R") and common_rand_min is not None:
            m = common_rand_min
        triggers.append(CronTrigger(day_of_week=day_of_week, hour=h, minute=m, timezone=timezone))
        
    if schedule_config.get("afternoon_enabled"):
        t_str = schedule_config.get("afternoon_time", "17:05")
        h, m = parse_time_parts(t_str)
        if t_str.upper().endswith(":R") and common_rand_min is not None:
            m = common_rand_min
        triggers.append(CronTrigger(day_of_week=day_of_week, hour=h, minute=m, timezone=timezone))
        
    return triggers


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
    返回: (cron_expression, schedule_label, schedule_config_json_str)
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
    
    # 转换成兼容的单条 Cron 表达式
    common_rand_min = None
    if (morning_enabled and morning_time.upper().endswith(":R")) or \
       (afternoon_enabled and afternoon_time.upper().endswith(":R")):
        import random
        common_rand_min = random.randint(0, 59)

    m_list = []
    h_list = []
    if morning_enabled:
        mh, mm = parse_time_parts(morning_time, force_fixed=False)
        if morning_time.upper().endswith(":R") and common_rand_min is not None:
            mm = common_rand_min
        m_list.append(str(mm))
        h_list.append(str(mh))
    if afternoon_enabled:
        ah, am = parse_time_parts(afternoon_time, force_fixed=False)
        if afternoon_time.upper().endswith(":R") and common_rand_min is not None:
            am = common_rand_min
        m_list.append(str(am))
        h_list.append(str(ah))
        
    mins = sorted(list(set(m_list)), key=int)
    hours = sorted(list(set(h_list)), key=int)
    
    cron_mins = ",".join(mins)
    cron_hours = ",".join(hours)
    cron_dow = "*" if frequency == "daily" else "1-5"
    
    cron_expr = f"{cron_mins} {cron_hours} * * {cron_dow}"
    
    return cron_expr, label, json.dumps(cfg, ensure_ascii=False)


def try_migrate_cron_to_simple(cron_expression: str) -> dict[str, Any] | None:
    """
    尝试把旧的 cron 表达式反向转换并迁移为简单结构。
    成功转换返回简单配置字典，若无法简单转换返回 None。
    """
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return None
        
    minute_part, hour_part, day_part, month_part, dow_part = parts
    
    if day_part != "*" or month_part != "*":
        return None
        
    if dow_part in {"*", "?"}:
        frequency = "daily"
    elif dow_part in {"1-5", "mon-fri", "1,2,3,4,5"}:
        frequency = "workday"
    else:
        return None
        
    try:
        hours = [int(h) for h in hour_part.split(",")]
        minutes = [int(m) for m in minute_part.split(",")]
    except ValueError:
        return None
        
    if len(hours) > 2:
        return None
        
    if len(hours) == 1:
        h = hours[0]
        if len(minutes) != 1:
            return None
        m = minutes[0]
        time_str = f"{h:02d}:{m:02d}"
        
        if h < 12:
            return {
                "frequency": frequency,
                "morning_enabled": True,
                "morning_time": time_str,
                "afternoon_enabled": False,
                "afternoon_time": "17:05"
            }
        else:
            return {
                "frequency": frequency,
                "morning_enabled": False,
                "morning_time": "09:05",
                "afternoon_enabled": True,
                "afternoon_time": time_str
            }
    elif len(hours) == 2:
        h1, h2 = sorted(hours)
        if h1 >= 12 or h2 < 12:
            return None
            
        if len(minutes) == 1:
            m1 = m2 = minutes[0]
        elif len(minutes) == 2:
            m1, m2 = minutes
        else:
            return None
            
        return {
            "frequency": frequency,
            "morning_enabled": True,
            "morning_time": f"{h1:02d}:{m1:02d}",
            "afternoon_enabled": True,
            "afternoon_time": f"{h2:02d}:{m2:02d}"
        }
        
    return None


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
