from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence

from .privacy import redact_payload
from .schema import validate_report


SENSITIVE_CONTEXT_KEYS = {
    "raw_json",
    "source_file",
    "source_path",
    "file_path",
    "download_path",
    "local_path",
    "markdown_path",
    "summary_csv_path",
    "product_csv_path",
    "audience_csv_path",
    "source_sheet",
    "source_row_number",
    "order_id",
    "order_no",
    "order_number",
    "transaction_id",
    "buyer",
    "buyer_id",
    "buyer_name",
    "customer",
    "customer_id",
    "customer_name",
    "nickname",
    "nick_name",
    "openid",
    "user_id",
    "user_name",
    "phone",
    "mobile",
    "tel",
    "buyer_phone",
    "receiver_phone",
    "contact_phone",
    "address",
    "buyer_address",
    "receiver_address",
    "shipping_address",
    "detail_address",
}

REDACTED_RECORD_KEYS = (
    "shop_id",
    "shop_name_snapshot",
    "task_id",
    "report_id",
    "report_type",
    "report_title",
    "report_date",
    "content",
    "metrics_json",
    "warnings_json",
    "source_file",
    "source_sheet",
    "source_row_number",
    "row_fingerprint",
    "raw_json",
)


def build_ai_report_record(
    report: dict[str, Any] | None,
    *,
    report_type: str,
    analysis_run_ids: Sequence[str | None],
    payload: Mapping[str, Any] | None = None,
    report_id: str | None = None,
    report_title: str | None = None,
    report_date: str | None = None,
    shop_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Return an ai_reports-compatible record dict without writing it."""
    run_ids = [str(run_id) for run_id in analysis_run_ids if run_id]
    normalized_report, validation_errors = validate_report(report)
    safe_payload = _safe_context(payload or {})
    timestamp = report_date or _now_iso()
    safe_report_type = str(report_type or safe_payload.get("payload_type") or "shop_analysis")
    safe_shop_id = shop_id or _shop_id_from_payload(safe_payload)
    safe_report_id = report_id or _report_id(safe_report_type, run_ids, safe_payload, normalized_report)
    content = _stable_json(normalized_report)
    metrics_context = {
        "analysis_run_ids": run_ids,
        "payload": safe_payload,
    }
    warnings_context = {
        "validation_errors": validation_errors,
    }
    raw_context = {
        "source": "ai_analysis",
        "dry_run": dry_run,
        "report_type": safe_report_type,
        "analysis_run_ids": run_ids,
        "payload_type": safe_payload.get("payload_type"),
        "payload_schema_version": safe_payload.get("schema_version"),
    }
    record = {
        "shop_id": safe_shop_id,
        "shop_name_snapshot": None,
        "task_id": run_ids[0] if run_ids else None,
        "report_id": safe_report_id,
        "report_type": safe_report_type,
        "report_title": report_title or _default_report_title(safe_report_type),
        "report_date": timestamp,
        "content": content,
        "metrics_json": _stable_json(metrics_context),
        "warnings_json": _stable_json(warnings_context),
        "source_file": None,
        "source_sheet": None,
        "source_row_number": None,
        "row_fingerprint": _fingerprint(
            "ai_report",
            safe_report_id,
            safe_report_type,
            run_ids,
            safe_shop_id,
            content,
        ),
        "raw_json": _stable_json(raw_context),
    }
    redacted = redact_payload(record)
    return {key: redacted.get(key) for key in REDACTED_RECORD_KEYS}


def _safe_context(value: Any) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SENSITIVE_CONTEXT_KEYS:
                continue
            safe[key_text] = _safe_context(item)
        return safe
    if isinstance(value, list):
        return [_safe_context(item) for item in value]
    return redact_payload(value)


def _shop_id_from_payload(payload: Mapping[str, Any]) -> str:
    scope = payload.get("scope")
    if isinstance(scope, Mapping) and scope.get("shop_id"):
        return str(scope["shop_id"])

    shops = payload.get("shops")
    if isinstance(shops, list) and len(shops) == 1 and isinstance(shops[0], Mapping):
        return _shop_id_from_payload(shops[0])
    if isinstance(shops, list) and len(shops) > 1:
        return "multi_shop"
    return "unknown_shop"


def _default_report_title(report_type: str) -> str:
    if report_type == "multi_shop_comparison":
        return "AI multi-shop comparison"
    return "AI shop analysis"


def _report_id(
    report_type: str,
    analysis_run_ids: Sequence[str],
    payload: Mapping[str, Any],
    report: Mapping[str, Any],
) -> str:
    return "air_" + _digest(
        {
            "report_type": report_type,
            "analysis_run_ids": list(analysis_run_ids),
            "payload": payload,
            "report": report,
        }
    )[:16]


def _fingerprint(*parts: Any) -> str:
    return "ai_report:" + _digest(parts)


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
