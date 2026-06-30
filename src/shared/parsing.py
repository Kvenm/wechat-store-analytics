from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d",
)


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    try:
        return bool(value != value)
    except Exception:
        return False


def clean_cell(value: Any) -> Any:
    if is_blank(value):
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return value


def to_text(value: Any) -> str | None:
    value = clean_cell(value)
    if value is None:
        return None
    return str(value).strip() or None


def to_float(value: Any) -> float | None:
    value = clean_cell(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = (
        text.replace(",", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace("元", "")
        .replace("CNY", "")
        .replace("RMB", "")
        .replace("USD", "")
        .strip()
    )
    if text.endswith("%"):
        try:
            parsed = float(text[:-1]) / 100
            return -parsed if negative else parsed
        except ValueError:
            return None

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    parsed = float(match.group(0))
    return -parsed if negative else parsed


def to_iso_datetime(value: Any) -> str | None:
    value = clean_cell(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and value > 20000:
        excel_epoch = datetime(1899, 12, 30)
        parsed = excel_epoch + timedelta(days=float(value))
        return parsed.isoformat(sep=" ", timespec="seconds")

    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("T", " ").replace("/", "-")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.isoformat(sep=" ", timespec="seconds")
    except ValueError:
        pass

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.isoformat(sep=" ", timespec="seconds")
        except ValueError:
            continue
    return text


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
