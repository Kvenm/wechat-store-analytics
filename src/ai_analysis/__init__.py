from __future__ import annotations

from .openai_client import OpenAIAnalysisClient, generate_multi_shop_report, generate_shop_report
from .payloads import build_multi_shop_payload, build_shop_payload
from .privacy import PrivacyOptions, mask_text, redact_payload, redact_record
from .reports import build_ai_report_record
from .schema import empty_report, validate_report

__all__ = [
    "OpenAIAnalysisClient",
    "PrivacyOptions",
    "build_ai_report_record",
    "build_multi_shop_payload",
    "build_shop_payload",
    "empty_report",
    "generate_multi_shop_report",
    "generate_shop_report",
    "mask_text",
    "redact_payload",
    "redact_record",
    "validate_report",
]
