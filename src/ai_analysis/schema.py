from __future__ import annotations

from copy import deepcopy
from typing import Any


REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "summary",
        "key_findings",
        "product_actions",
        "audience_insights",
        "risk_products",
        "growth_products",
        "next_steps",
    ],
    "properties": {
        "summary": {"type": "string"},
        "key_findings": {"type": "array", "items": {"type": "object"}},
        "product_actions": {"type": "array", "items": {"type": "object"}},
        "audience_insights": {"type": "array", "items": {"type": "object"}},
        "risk_products": {"type": "array", "items": {"type": "object"}},
        "growth_products": {"type": "array", "items": {"type": "object"}},
        "next_steps": {"type": "array", "items": {"type": "object"}},
    },
    "additionalProperties": False,
}


REPORT_DEFAULT: dict[str, Any] = {
    "summary": "",
    "key_findings": [],
    "product_actions": [],
    "audience_insights": [],
    "risk_products": [],
    "growth_products": [],
    "next_steps": [],
}


def empty_report() -> dict[str, Any]:
    return deepcopy(REPORT_DEFAULT)


def validate_report(report: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Return a report normalized to the fixed schema plus validation errors."""
    if not isinstance(report, dict):
        return empty_report(), ["report must be an object"]

    errors: list[str] = []
    normalized = empty_report()
    for key, default_value in REPORT_DEFAULT.items():
        value = report.get(key, default_value)
        if key == "summary":
            if value is None:
                value = ""
            if not isinstance(value, str):
                errors.append("summary must be a string")
                value = str(value)
        elif not isinstance(value, list):
            errors.append(f"{key} must be an array")
            value = []
        normalized[key] = value

    extra_keys = sorted(set(report) - set(REPORT_DEFAULT))
    if extra_keys:
        errors.append(f"unexpected keys: {', '.join(extra_keys)}")
    return normalized, errors
