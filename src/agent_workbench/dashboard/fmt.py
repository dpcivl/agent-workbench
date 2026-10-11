"""Formatting helpers (pure Python). Missing values are shown as ``n/a``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Union

NA = "n/a"


def fmt_duration(value: Optional[timedelta]) -> str:
    """``1일 2시간 3분`` / ``26분 12초`` / ``45초``; ``n/a`` for None."""
    if value is None:
        return NA
    total = int(round(value.total_seconds()))
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if days:
        parts = [f"{days}일"] + ([f"{hours}시간"] if hours else []) + ([f"{minutes}분"] if minutes else [])
    elif hours:
        parts = [f"{hours}시간"] + ([f"{minutes}분"] if minutes else [])
    elif minutes:
        parts = [f"{minutes}분"] + ([f"{seconds}초"] if seconds else [])
    else:
        parts = [f"{seconds}초"]
    return sign + " ".join(parts)


def fmt_dt(value: Optional[datetime], tz: Optional[tzinfo] = None) -> str:
    """``2026-10-07 09:00`` in ``tz`` (default: this machine's local zone); ``n/a`` for None."""
    if value is None:
        return NA
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def fmt_int(value: Optional[int]) -> str:
    return NA if value is None else f"{value:,}"


def fmt_number(value: Union[int, float, None]) -> str:
    """Integers with thousands separators, floats with up to 4 significant decimals."""
    if value is None:
        return NA
    if isinstance(value, int) or float(value).is_integer() and abs(value) >= 1000:
        return f"{int(value):,}"
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return text or "0"
