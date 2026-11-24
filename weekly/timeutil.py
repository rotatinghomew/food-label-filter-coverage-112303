"""时间解析与 ISO 周标签。表外固定上海时区，内部只做朴素时间比较。"""
from __future__ import annotations

from datetime import datetime, date

MIDNIGHT = "T00:00"


def parse_dt(value: str) -> datetime:
    """日期按当天零点解析，日期时间按 ISO 字符串解析。"""
    value = value.strip()
    if not value:
        raise ValueError("时间字段为空")
    if "T" not in value:
        value = f"{value}{MIDNIGHT}"
    return datetime.fromisoformat(value)


def parse_opt_dt(value: str) -> datetime | None:
    value = value.strip()
    return parse_dt(value) if value else None


def iso_week_label(at: datetime) -> str:
    """返回 2026-W35 形式的 ISO 周标签。"""
    iso = at.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def today_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)
