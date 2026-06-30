from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .privacy import PrivacyOptions, redact_payload


DEFAULT_TOP_N = 20

ORDER_METRIC_KEYS = (
    "total_order_count",
    "paid_order_count",
    "effective_order_count",
    "refund_order_count",
    "refund_order_rate",
    "gmv",
    "refund_amount",
    "net_sales_amount",
    "average_order_value",
)

SHOP_DAILY_METRIC_KEYS = (
    "row_count",
    "date_count",
    "date_from",
    "date_to",
    "exposure_user_count",
    "click_user_count",
    "click_count",
    "order_amount",
    "order_submit_count",
    "order_user_count",
    "order_count",
    "buyer_count",
    "sold_quantity",
    "payment_amount",
    "refund_amount",
    "net_payment_amount",
    "click_through_rate",
    "click_conversion_rate",
    "order_submit_rate",
    "refund_rate",
    "average_order_value",
    "average_buyer_value",
    "average_daily_exposure_user_count",
    "average_daily_click_count",
    "average_daily_payment_amount",
)

AUDIENCE_SEGMENT_KEYS = (
    "product_id",
    "product_name",
    "category",
    "dimension",
    "segment_label",
    "visitor_count",
    "add_to_cart_count",
    "order_count",
    "payment_amount",
    "conversion_rate",
    "refund_rate",
    "region",
    "gender",
    "age_group",
    "consumption_level",
    "active_hour",
)


def build_shop_payload(
    metrics: dict[str, Any],
    *,
    analysis_run_id: str | None = None,
    privacy_options: PrivacyOptions | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Build a de-identified, aggregated payload for one shop."""
    order_metrics = _safe_mapping(metrics.get("order_metrics"), ORDER_METRIC_KEYS)
    shop_daily_metrics = _safe_mapping(metrics.get("shop_daily_metrics"), SHOP_DAILY_METRIC_KEYS)
    product_metrics = list(metrics.get("product_metrics") or [])
    audience_metrics = dict(metrics.get("audience_metrics") or {})

    payload = {
        "payload_type": "shop_analysis",
        "schema_version": "ai-analysis-payload/v1",
        "generated_at": _now_iso(),
        "analysis_run_id": analysis_run_id,
        "scope": {
            "shop_id": metrics.get("shop_id"),
            "date_from": metrics.get("date_from"),
            "date_to": metrics.get("date_to"),
        },
        "metrics": {
            "orders": order_metrics,
            "shop_daily": shop_daily_metrics,
            "products": {
                "top_by_net_sales": _top_products(product_metrics, "net_amount", top_n),
                "risk_by_refund_rate": _risk_products(product_metrics, top_n),
                "growth_by_contribution": _top_products(product_metrics, "contribution_rate", top_n),
            },
            "audience": _audience_payload(audience_metrics, top_n),
        },
        "metadata": _safe_metadata(metrics.get("metadata") or {}),
        "privacy": {
            "source": "aggregated_metrics_only",
            "excluded": [
                "raw_orders",
                "raw_order_items",
                "phone_numbers",
                "addresses",
                "buyer_nicknames",
                "buyer_identifiers",
                "raw_order_ids",
                "raw_export_rows",
                "local_file_paths",
            ],
        },
    }
    return redact_payload(payload, privacy_options)


def build_multi_shop_payload(
    shop_metrics: list[dict[str, Any]],
    *,
    analysis_run_ids: list[str | None] | None = None,
    privacy_options: PrivacyOptions | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Build a de-identified comparison payload across multiple shops."""
    run_ids = analysis_run_ids or []
    shops = [
        build_shop_payload(
            metrics,
            analysis_run_id=run_ids[index] if index < len(run_ids) else None,
            privacy_options=privacy_options,
            top_n=top_n,
        )
        for index, metrics in enumerate(shop_metrics)
    ]
    payload = {
        "payload_type": "multi_shop_comparison",
        "schema_version": "ai-analysis-payload/v1",
        "generated_at": _now_iso(),
        "shop_count": len(shops),
        "analysis_run_ids": [run_id for run_id in run_ids if run_id],
        "shops": shops,
        "comparison": {
            "rankings": _shop_rankings(shops),
            "totals": _shop_totals(shops),
        },
        "privacy": {
            "source": "aggregated_metrics_only",
            "excluded": [
                "raw_orders",
                "raw_order_items",
                "phone_numbers",
                "addresses",
                "buyer_nicknames",
                "buyer_identifiers",
                "raw_order_ids",
            ],
        },
    }
    return redact_payload(payload, privacy_options)


def _top_products(products: list[dict[str, Any]], field: str, top_n: int) -> list[dict[str, Any]]:
    return [_product_row(row) for row in sorted(products, key=lambda row: _number(row.get(field)), reverse=True)[:top_n]]


def _risk_products(products: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in products
        if _number(row.get("refund_rate")) > 0 or _number(row.get("refund_amount")) > 0
    ]
    ranked = sorted(
        candidates,
        key=lambda row: (
            _number(row.get("refund_rate")),
            _number(row.get("refund_amount")),
            _number(row.get("order_count")),
        ),
        reverse=True,
    )
    return [_product_row(row) for row in ranked[:top_n]]


def _safe_mapping(value: Any, allowed_keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key) for key in allowed_keys if key in value}


def _safe_rows(rows: Any, allowed_keys: tuple[str, ...], top_n: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [
        _safe_mapping(row, allowed_keys)
        for row in rows[:top_n]
        if isinstance(row, dict)
    ]


def _product_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": row.get("product_id"),
        "product_name": row.get("product_name"),
        "sku_id": row.get("sku_id"),
        "sku_name": row.get("sku_name"),
        "order_count": row.get("order_count"),
        "refund_order_count": row.get("refund_order_count"),
        "refund_rate": row.get("refund_rate"),
        "gross_amount": row.get("gross_amount"),
        "refund_amount": row.get("refund_amount"),
        "net_amount": row.get("net_amount"),
        "contribution_rate": row.get("contribution_rate"),
    }


def _audience_payload(audience_metrics: dict[str, Any], top_n: int) -> dict[str, Any]:
    return {
        "primary_segments": _safe_rows(audience_metrics.get("primary_segments"), AUDIENCE_SEGMENT_KEYS, top_n),
        "potential_segments": _safe_rows(audience_metrics.get("potential_segments"), AUDIENCE_SEGMENT_KEYS, top_n),
        "risk_segments": _safe_rows(audience_metrics.get("risk_segments"), AUDIENCE_SEGMENT_KEYS, top_n),
        "segment_count": audience_metrics.get("segment_count", 0),
        "dimensions": list(audience_metrics.get("dimensions") or []),
    }


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "orders_scanned",
        "orders_in_period",
        "order_items_scanned",
        "products_scanned",
        "refunds_scanned",
        "audience_insights_scanned",
        "shop_daily_scanned",
        "shop_daily_in_period",
        "refund_attribution",
    }
    return {key: metadata.get(key) for key in allowed_keys if key in metadata}


def _shop_rankings(shops: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows = []
    for shop in shops:
        order_metrics = shop.get("metrics", {}).get("orders", {})
        rows.append(
            {
                "shop_id": shop.get("scope", {}).get("shop_id"),
                "analysis_run_id": shop.get("analysis_run_id"),
                "net_sales_amount": order_metrics.get("net_sales_amount"),
                "paid_order_count": order_metrics.get("paid_order_count"),
                "effective_order_count": order_metrics.get("effective_order_count"),
                "average_order_value": order_metrics.get("average_order_value"),
                "refund_order_rate": order_metrics.get("refund_order_rate"),
            }
        )
    return {
        "by_net_sales": sorted(rows, key=lambda row: _number(row.get("net_sales_amount")), reverse=True),
        "by_average_order_value": sorted(rows, key=lambda row: _number(row.get("average_order_value")), reverse=True),
        "by_refund_order_rate": sorted(rows, key=lambda row: _number(row.get("refund_order_rate")), reverse=True),
    }


def _shop_totals(shops: list[dict[str, Any]]) -> dict[str, Any]:
    order_rows = [shop.get("metrics", {}).get("orders", {}) for shop in shops]
    paid_order_count = sum(_number(row.get("paid_order_count")) for row in order_rows)
    effective_order_count = sum(_number(row.get("effective_order_count")) for row in order_rows)
    gmv = sum(_number(row.get("gmv")) for row in order_rows)
    net_sales_amount = sum(_number(row.get("net_sales_amount")) for row in order_rows)
    return {
        "paid_order_count": round(paid_order_count, 2),
        "effective_order_count": round(effective_order_count, 2),
        "net_sales_amount": round(net_sales_amount, 2),
        "refund_amount": round(sum(_number(row.get("refund_amount")) for row in order_rows), 2),
        "gmv": round(gmv, 2),
        "average_order_value": round(gmv / paid_order_count, 2) if paid_order_count else 0,
    }


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
