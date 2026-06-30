from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from shared.paths import ensure_dir
from sync.business_mapper import upsert_business_records
from sync.product_mapper import upsert_product_records
from warehouse.repository import (
    connect,
    initialize_database,
    upsert_raw_api_response,
    upsert_sync_run,
    upsert_sync_run_item,
)


SOURCE_KIND_API_PULL = "api_pull"
CONNECTOR_NAME = "mock_wechat"
SCHEMA_VERSION = "mock-wechat-v1"
SENSITIVE_KEY_PARTS = (
    "access_token",
    "authorization",
    "cookie",
    "secret",
    "session",
    "token",
)


@dataclass(frozen=True)
class MockEndpoint:
    endpoint: str
    export_type: str
    table_hint: str
    records: tuple[Mapping[str, Any], ...]


def run_mock_sync(
    *,
    db_path: str | Path,
    archive_dir: str | Path,
    shop_id: str,
    shop_name: str | None,
    date_from: str | None,
    date_to: str | None,
    sync_run_id: str,
    endpoints: Iterable[str] | None = None,
) -> dict[str, Any]:
    endpoint_names = tuple(endpoints or ("products", "orders", "aftersale", "funds"))
    endpoint_data = [_endpoint_fixture(name) for name in endpoint_names]
    run_archive_dir = ensure_dir(Path(archive_dir) / sync_run_id / shop_id / "api")
    started_at = _utc_like_now()
    warnings: list[dict[str, Any]] = []
    item_results: list[dict[str, Any]] = []

    with connect(db_path) as conn:
        initialize_database(conn)
        upsert_sync_run(
            conn,
            {
                "sync_run_id": sync_run_id,
                "shop_id": shop_id,
                "shop_name_snapshot": shop_name,
                "source_kind": SOURCE_KIND_API_PULL,
                "connector": CONNECTOR_NAME,
                "status": "running",
                "date_from": date_from,
                "date_to": date_to,
                "started_at": started_at,
                "params": _redact_mapping(
                    {
                        "shop_id": shop_id,
                        "shop_name": shop_name,
                        "date_from": date_from,
                        "date_to": date_to,
                        "endpoints": endpoint_names,
                    }
                ),
                "summary": {},
                "warnings": warnings,
            },
        )

        for page_number, endpoint in enumerate(endpoint_data, start=1):
            item_result = _persist_endpoint(
                conn=conn,
                run_archive_dir=run_archive_dir,
                shop_id=shop_id,
                shop_name=shop_name,
                sync_run_id=sync_run_id,
                endpoint=endpoint,
                page_number=page_number,
            )
            item_results.append(item_result)

        finished_at = _utc_like_now()
        upsert_sync_run(
            conn,
            {
                "sync_run_id": sync_run_id,
                "shop_id": shop_id,
                "shop_name_snapshot": shop_name,
                "source_kind": SOURCE_KIND_API_PULL,
                "connector": CONNECTOR_NAME,
                "status": "completed",
                "date_from": date_from,
                "date_to": date_to,
                "started_at": started_at,
                "finished_at": finished_at,
                "params": _redact_mapping(
                    {
                        "shop_id": shop_id,
                        "shop_name": shop_name,
                        "date_from": date_from,
                        "date_to": date_to,
                        "endpoints": endpoint_names,
                    }
                ),
                "summary": {
                    "endpoint_count": len(item_results),
                    "record_count": sum(int(item["row_count"]) for item in item_results),
                    "archive_dir": str(run_archive_dir),
                },
                "warnings": warnings,
            },
        )

    return {
        "sync_run_id": sync_run_id,
        "shop_id": shop_id,
        "source_kind": SOURCE_KIND_API_PULL,
        "connector": CONNECTOR_NAME,
        "status": "completed",
        "archive_dir": str(run_archive_dir),
        "items": item_results,
        "warnings": warnings,
    }


def _persist_endpoint(
    *,
    conn: Any,
    run_archive_dir: Path,
    shop_id: str,
    shop_name: str | None,
    sync_run_id: str,
    endpoint: MockEndpoint,
    page_number: int,
) -> dict[str, Any]:
    sync_item_id = _stable_id("sync_item", sync_run_id, shop_id, endpoint.endpoint, page_number)
    raw_response_id = _stable_id("raw_api", sync_run_id, shop_id, endpoint.endpoint, page_number)
    request_id = _stable_id("request", sync_run_id, endpoint.endpoint, page_number)
    rid = _stable_id("rid", sync_run_id, endpoint.endpoint, page_number)
    pulled_at = _utc_like_now()
    payload = {
        "endpoint": endpoint.endpoint,
        "errcode": 0,
        "errmsg": "ok",
        "request_id": request_id,
        "rid": rid,
        "records": [dict(record) for record in endpoint.records],
    }
    sanitized_payload = _redact_mapping(payload)
    storage_path = run_archive_dir / f"{endpoint.endpoint.replace('/', '_')}_{page_number}.json"
    _write_json(storage_path, sanitized_payload)
    file_stats = _file_stats(storage_path)
    record_count = len(endpoint.records)

    raw_index = {
        "raw_response_id": raw_response_id,
        "sync_run_id": sync_run_id,
        "sync_item_id": sync_item_id,
        "shop_id": shop_id,
        "source_kind": SOURCE_KIND_API_PULL,
        "endpoint": endpoint.endpoint,
        "request_id": request_id,
        "rid": rid,
        "status": "completed",
        "storage_path": str(storage_path),
        "sha256": file_stats["sha256"],
        "size_bytes": file_stats["size_bytes"],
        "record_count": record_count,
        "schema_version": SCHEMA_VERSION,
        "pulled_at": pulled_at,
        "metadata": {
            "redacted": True,
            "content_type": "application/json",
            "fixture": CONNECTOR_NAME,
        },
    }
    upsert_raw_api_response(conn, raw_index)
    if endpoint.table_hint == "products":
        upsert_product_records(
            conn,
            endpoint.records,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=sync_run_id,
            source_file=str(storage_path),
            source_sheet=endpoint.endpoint,
        )
    elif endpoint.table_hint in {"orders", "refunds", "fund_flows"}:
        upsert_business_records(
            conn,
            endpoint.table_hint,
            endpoint.records,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=sync_run_id,
            source_file=str(storage_path),
            source_sheet=endpoint.endpoint,
        )

    item = {
        "sync_item_id": sync_item_id,
        "sync_run_id": sync_run_id,
        "shop_id": shop_id,
        "source_kind": SOURCE_KIND_API_PULL,
        "endpoint": endpoint.endpoint,
        "export_type": endpoint.export_type,
        "table_hint": endpoint.table_hint,
        "status": "completed",
        "cursor": None,
        "page_number": page_number,
        "request_id": request_id,
        "rid": rid,
        "http_status": 200,
        "error_code": None,
        "error_message": None,
        "raw_response_id": raw_response_id,
        "row_count": record_count,
        "started_at": pulled_at,
        "finished_at": pulled_at,
        "raw": {
            "redacted": True,
            "raw_response_id": raw_response_id,
            "storage_path": str(storage_path),
        },
    }
    upsert_sync_run_item(conn, item)
    return {
        "sync_item_id": sync_item_id,
        "raw_response_id": raw_response_id,
        "endpoint": endpoint.endpoint,
        "export_type": endpoint.export_type,
        "table_hint": endpoint.table_hint,
        "row_count": record_count,
        "storage_path": str(storage_path),
        **file_stats,
    }


def _endpoint_fixture(name: str) -> MockEndpoint:
    normalized = name.strip().casefold()
    if normalized in {"products", "product", "商品"}:
        return MockEndpoint(
            endpoint="/channels-shop-product/shop/getproductlist",
            export_type="products",
            table_hint="products",
            records=(
                {
                    "product_id": "mock-p001",
                    "product_name": "Mock 连衣裙",
                    "status": "online",
                    "price": 199,
                    "stock": 20,
                    "sku_list": (
                        {"sku_id": "mock-sku-p001-red-m", "sku_name": "红色 / M", "price": 199, "stock": 8, "barcode": "690mock001"},
                    ),
                },
                {"product_id": "mock-p002", "product_name": "Mock 防晒帽", "status": "online", "price": 89, "stock": 50},
            ),
        )
    if normalized in {"orders", "order", "订单"}:
        return MockEndpoint(
            endpoint="/channels-shop-order/getorderlist",
            export_type="orders",
            table_hint="orders",
            records=(
                {
                    "order_id": "mock-o001",
                    "buyer_id": "mock-buyer-001",
                    "status": "paid",
                    "payment_amount": 199,
                    "order_created_at": "2026-06-01 10:00:00",
                    "pay_time": "2026-06-01 10:01:00",
                    "freight": 0,
                    "discount": 10,
                    "currency": "CNY",
                    "items": (
                        {
                            "order_item_id": "mock-oi001",
                            "product_id": "mock-p001",
                            "product_name": "Mock 连衣裙",
                            "sku_id": "mock-sku-p001-red-m",
                            "sku_name": "红色 / M",
                            "quantity": 1,
                            "item_amount": 199,
                        },
                    ),
                },
                {
                    "order_id": "mock-o002",
                    "buyer_id": "mock-buyer-002",
                    "status": "finished",
                    "payment_amount": 89,
                    "order_created_at": "2026-06-02 10:00:00",
                    "items": (
                        {
                            "order_item_id": "mock-oi002",
                            "product_id": "mock-p002",
                            "product_name": "Mock 防晒帽",
                            "quantity": 1,
                            "item_amount": 89,
                        },
                    ),
                },
            ),
        )
    if normalized in {"aftersale", "refunds", "refund", "售后", "退款"}:
        return MockEndpoint(
            endpoint="/channels-shop-aftersale/aftersale/getaftersalelist",
            export_type="refunds",
            table_hint="refunds",
            records=(
                {
                    "refund_id": "mock-r001",
                    "order_id": "mock-o001",
                    "product_id": "mock-p001",
                    "product_name": "Mock 连衣裙",
                    "sku_id": "mock-sku-p001-red-m",
                    "refund_status": "completed",
                    "refund_amount": 20,
                    "refund_created_at": "2026-06-03 09:00:00",
                    "refund_completed_at": "2026-06-03 12:00:00",
                    "reason": "尺码不合适",
                },
            ),
        )
    if normalized in {"funds", "fund", "资金"}:
        return MockEndpoint(
            endpoint="/funds/funds/getfundsflowlist",
            export_type="funds",
            table_hint="fund_flows",
            records=(
                {
                    "flow_id": "mock-f001",
                    "flow_date": "2026-06-02",
                    "flow_type": "payment",
                    "biz_type": "order",
                    "order_id": "mock-o001",
                    "amount": 199,
                    "currency": "CNY",
                    "direction": "in",
                    "balance": 199,
                    "remark": "订单入账",
                },
            ),
        )
    raise ValueError(f"unsupported mock endpoint: {name}")


def _redact_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = _redact_mapping(item)
        return redacted
    if isinstance(value, list):
        return [_redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_mapping(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _file_stats(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:16]}"


def _utc_like_now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")
