from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from analytics.metrics import calculate_metrics
from reporting.render import generate_report
from shared.ids import fingerprint, stable_json
from shared.parsing import to_float, to_iso_datetime, to_text
from shared.paths import ensure_dir
from shared.table_catalog import FIELD_KIND_AMOUNT, FIELD_KIND_DATETIME, specs_for_table
from sync.business_mapper import upsert_business_records
from sync.product_mapper import upsert_product_records
from warehouse.repository import (
    connect,
    create_analysis_run,
    initialize_database,
    upsert_raw_api_response,
    upsert_records,
    upsert_sync_run,
    upsert_sync_run_item,
)


SOURCE_KIND_API_PULL = "api_pull"
CONNECTOR_NAME = "wechat_api"
SCHEMA_VERSION = "wechat-api-v1"
DEFAULT_API_BASE_URL = "https://api.weixin.qq.com"
DEFAULT_ENDPOINTS = ("products", "orders", "aftersale", "funds")
SENSITIVE_KEY_PARTS = (
    "access_token",
    "appsecret",
    "authorization",
    "cookie",
    "credential",
    "refresh_token",
    "secret",
    "session",
    "token",
)
CHINA_TZ = timezone(timedelta(hours=8))

HttpPost = Callable[[str, Mapping[str, Any], int], tuple[int, Mapping[str, Any]]]


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    endpoint: str
    export_type: str
    table_hint: str
    date_mode: str = "none"
    records_keys: tuple[str, ...] = ("records", "items", "list", "data")
    id_keys: tuple[str, ...] = ()
    detail_endpoint: str | None = None
    detail_request_key: str | None = None
    detail_record_keys: tuple[str, ...] = ()
    page_param: str | None = None


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "products": EndpointSpec(
        name="products",
        endpoint="/channels/ec/product/list/get",
        export_type="product_list",
        table_hint="products",
        records_keys=("products", "product_list", "productList", "items", "list", "data"),
        id_keys=("product_ids", "product_id_list"),
        detail_endpoint="/channels/ec/product/get",
        detail_request_key="product_id",
        detail_record_keys=("product", "data"),
    ),
    "orders": EndpointSpec(
        name="orders",
        endpoint="/channels/ec/order/list/get",
        export_type="orders",
        table_hint="orders",
        date_mode="order_create_range",
        records_keys=("orders", "order_list", "orderList", "items", "list", "data"),
        id_keys=("order_id_list", "order_ids"),
        detail_endpoint="/channels/ec/order/get",
        detail_request_key="order_id",
        detail_record_keys=("order", "data"),
    ),
    "aftersale": EndpointSpec(
        name="aftersale",
        endpoint="/channels/ec/aftersale/getaftersalelist",
        export_type="refunds",
        table_hint="refunds",
        date_mode="aftersale_create_range",
        records_keys=("aftersale_orders", "after_sale_orders", "refunds", "items", "list", "data"),
        id_keys=("after_sale_order_id_list", "aftersale_order_id_list", "aftersale_ids", "refund_ids"),
        detail_endpoint="/channels/ec/aftersale/getaftersaleorder",
        detail_request_key="after_sale_order_id",
        detail_record_keys=("aftersale_order", "after_sale_order", "refund", "data"),
    ),
    "funds": EndpointSpec(
        name="funds",
        endpoint="/channels/ec/funds/getfundsflowlist",
        export_type="funds",
        table_hint="fund_flows",
        date_mode="flat_time_range",
        records_keys=("funds_flows", "funds_flow_list", "fund_flows", "items", "list", "data"),
        id_keys=("flow_ids", "funds_flow_ids"),
        detail_endpoint="/channels/ec/funds/getfundsflowdetail",
        detail_request_key="flow_id",
        detail_record_keys=("funds_flow", "fund_flow", "data"),
        page_param="page",
    ),
    "compass_shop": EndpointSpec(
        name="compass_shop",
        endpoint="/channels/ec/compass/shop/overall/get",
        export_type="products",
        table_hint="shop_daily",
        date_mode="compass_ds",
        records_keys=("records", "shop_daily", "data", "list", "items"),
    ),
    "compass_product": EndpointSpec(
        name="compass_product",
        endpoint="/channels/ec/compass/shop/product/list/get",
        export_type="product_daily",
        table_hint="product_daily",
        date_mode="compass_ds",
        records_keys=("product_list", "productList", "records", "data", "list", "items"),
    ),
    "compass_audience": EndpointSpec(
        name="compass_audience",
        endpoint="/channels/ec/compass/shop/sale/profile/data/get",
        export_type="compass",
        table_hint="audience_insights",
        date_mode="compass_ds",
        records_keys=("profile_data", "audience", "records", "data", "list", "items"),
    ),
}
ENDPOINT_ALIASES = {
    "product": "products",
    "product_list": "products",
    "订单": "orders",
    "order": "orders",
    "售后": "aftersale",
    "退款": "aftersale",
    "refund": "aftersale",
    "refunds": "aftersale",
    "fund": "funds",
    "资金": "funds",
    "shop_daily": "compass_shop",
    "product_daily": "compass_product",
    "compass_products": "compass_product",
    "audience": "compass_audience",
    "compass": "compass_audience",
}
GENERIC_STANDARD_TABLES = {
    "reviews",
    "shop_daily",
    "product_daily",
    "traffic_sources",
    "ad_spend",
    "audience_insights",
}
RECORD_HINT_KEYS = {
    "id",
    "product_id",
    "order_id",
    "refund_id",
    "flow_id",
    "stat_date",
    "date",
    "product_name",
    "review_content",
    "visitor_count",
    "payment_amount",
    "pay_gmv",
    "pay_order_cnt",
    "product_click_uv",
    "spend_amount",
    "segment_label",
}


def run_wechat_api_sync(
    *,
    db_path: str | Path,
    archive_dir: str | Path,
    shop_id: str,
    shop_name: str | None,
    date_from: str | None,
    date_to: str | None,
    sync_run_id: str,
    access_token: str,
    api_base_url: str = DEFAULT_API_BASE_URL,
    endpoints: Iterable[str] | None = None,
    page_size: int = 30,
    max_pages: int = 20,
    timeout: int = 20,
    endpoint_params: Mapping[str, Mapping[str, Any]] | None = None,
    http_post: HttpPost | None = None,
) -> dict[str, Any]:
    if not str(access_token or "").strip():
        raise ValueError("access_token is required for WeChat API sync")

    endpoint_specs = [_endpoint_spec(name) for name in (endpoints or DEFAULT_ENDPOINTS)]
    client = WechatApiClient(api_base_url, access_token, http_post=http_post)
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
                        "endpoints": [spec.name for spec in endpoint_specs],
                        "page_size": page_size,
                        "max_pages": max_pages,
                    }
                ),
                "summary": {},
                "warnings": warnings,
            },
        )

        failed = False
        for spec in endpoint_specs:
            page_sequence = 0
            for window_from, window_to in _date_windows(spec, date_from, date_to):
                cursor: str | None = None
                for page_number in range(1, max_pages + 1):
                    page_sequence += 1
                    item_result = _pull_endpoint_page(
                        conn=conn,
                        client=client,
                        run_archive_dir=run_archive_dir,
                        shop_id=shop_id,
                        shop_name=shop_name,
                        sync_run_id=sync_run_id,
                        spec=spec,
                        date_from=window_from,
                        date_to=window_to,
                        page_size=page_size,
                        page_number=page_sequence,
                        cursor=cursor,
                        timeout=timeout,
                        endpoint_params=endpoint_params or {},
                    )
                    item_results.append(item_result)
                    if item_result["status"] != "completed":
                        warnings.append(
                            {
                                "code": "wechat_api_endpoint_failed",
                                "endpoint": spec.endpoint,
                                "message": item_result.get("error_message") or "endpoint failed",
                            }
                        )
                        failed = True
                        break
                    cursor = item_result.get("next_cursor")
                    if not item_result.get("has_more") or not cursor:
                        break
                if failed:
                    break

        finished_at = _utc_like_now()
        status = "failed" if failed else "completed"
        upsert_sync_run(
            conn,
            {
                "sync_run_id": sync_run_id,
                "shop_id": shop_id,
                "shop_name_snapshot": shop_name,
                "source_kind": SOURCE_KIND_API_PULL,
                "connector": CONNECTOR_NAME,
                "status": status,
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
                        "endpoints": [spec.name for spec in endpoint_specs],
                        "page_size": page_size,
                        "max_pages": max_pages,
                    }
                ),
                "summary": {
                    "endpoint_count": len(endpoint_specs),
                    "item_count": len(item_results),
                    "record_count": sum(int(item.get("row_count") or 0) for item in item_results),
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
        "status": status,
        "archive_dir": str(run_archive_dir),
        "items": item_results,
        "warnings": warnings,
    }


def create_api_sync_report(
    *,
    db_path: str | Path,
    reports_dir: str | Path,
    shop_id: str,
    date_from: str | None,
    date_to: str | None,
    sync_run_id: str,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        initialize_database(conn)
        metrics, warnings = calculate_metrics(conn, shop_id=shop_id, date_from=date_from, date_to=date_to)
        analysis_run_id = create_analysis_run(
            conn,
            shop_id=shop_id,
            date_from=date_from,
            date_to=date_to,
            params={
                "shop_id": shop_id,
                "date_from": date_from,
                "date_to": date_to,
                "source_kind": SOURCE_KIND_API_PULL,
                "sync_run_id": sync_run_id,
            },
            metrics=metrics,
            warnings=warnings,
        )
        report = generate_report(conn, analysis_run_id=analysis_run_id, reports_dir=reports_dir)
    return {
        "analysis_run_id": analysis_run_id,
        "analysis_warning_count": len(warnings),
        "report": report,
    }


class WechatApiClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        http_post: HttpPost | None = None,
    ) -> None:
        self.base_url = str(base_url or DEFAULT_API_BASE_URL).rstrip("/")
        self.access_token = access_token
        self.http_post = http_post

    def post_json(self, endpoint: str, body: Mapping[str, Any], timeout: int) -> tuple[int, Mapping[str, Any]]:
        url = self._url(endpoint)
        if self.http_post is not None:
            return self.http_post(url, body, timeout)

        request_body = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        status = 0
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_body = exc.read().decode("utf-8", errors="replace")

        if not response_body.strip():
            return status, {}
        parsed = json.loads(response_body)
        if not isinstance(parsed, Mapping):
            raise ValueError(f"WeChat API returned non-object JSON for {endpoint}")
        return status, parsed

    def _url(self, endpoint: str) -> str:
        clean_endpoint = "/" + endpoint.strip("/")
        token = urllib.parse.quote(self.access_token, safe="")
        return f"{self.base_url}{clean_endpoint}?access_token={token}"


def _pull_endpoint_page(
    *,
    conn: Any,
    client: WechatApiClient,
    run_archive_dir: Path,
    shop_id: str,
    shop_name: str | None,
    sync_run_id: str,
    spec: EndpointSpec,
    date_from: str | None,
    date_to: str | None,
    page_size: int,
    page_number: int,
    cursor: str | None,
    timeout: int,
    endpoint_params: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    sync_item_id = _stable_id("sync_item", sync_run_id, shop_id, spec.endpoint, page_number)
    raw_response_id = _stable_id("raw_api", sync_run_id, shop_id, spec.endpoint, page_number)
    pulled_at = _utc_like_now()
    request_body = _request_body(
        spec=spec,
        date_from=date_from,
        date_to=date_to,
        page_size=page_size,
        page_number=page_number,
        cursor=cursor,
        endpoint_params=endpoint_params,
    )

    try:
        http_status, payload = client.post_json(spec.endpoint, request_body, timeout)
        _raise_api_error(payload)
        records, detail_payloads = _records_for_payload(client, spec, payload, timeout)
        records = [_with_request_context(spec, record, request_body) for record in records]
        records = [_normalize_record_for_table(spec.table_hint, record) for record in records]
        status = "completed"
        error_code = None
        error_message = None
    except Exception as exc:
        http_status = http_status if "http_status" in locals() else None
        payload = payload if "payload" in locals() else {}
        records = []
        detail_payloads = []
        status = "failed"
        error_code = _text((payload or {}).get("errcode")) if isinstance(payload, Mapping) else None
        error_message = str(exc)

    archive_payload = {
        "endpoint": spec.endpoint,
        "request_body": _redact_mapping(request_body),
        "response": payload,
        "detail_responses": detail_payloads,
        "records": records,
    }
    sanitized_payload = _redact_mapping(archive_payload)
    storage_path = run_archive_dir / f"{_filename_part(spec.endpoint)}_{page_number}.json"
    _write_json(storage_path, sanitized_payload)
    file_stats = _file_stats(storage_path)

    raw_index = {
        "raw_response_id": raw_response_id,
        "sync_run_id": sync_run_id,
        "sync_item_id": sync_item_id,
        "shop_id": shop_id,
        "source_kind": SOURCE_KIND_API_PULL,
        "endpoint": spec.endpoint,
        "request_id": _text(_first_value(payload, ("request_id",))),
        "rid": _text(_first_value(payload, ("rid",))),
        "status": status,
        "storage_path": str(storage_path),
        "sha256": file_stats["sha256"],
        "size_bytes": file_stats["size_bytes"],
        "record_count": len(records),
        "schema_version": SCHEMA_VERSION,
        "pulled_at": pulled_at,
        "metadata": {
            "redacted": True,
            "content_type": "application/json",
            "connector": CONNECTOR_NAME,
            "detail_response_count": len(detail_payloads),
        },
    }
    upsert_raw_api_response(conn, raw_index)
    if status == "completed":
        _upsert_records_for_table(
            conn,
            spec.table_hint,
            records,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=sync_run_id,
            source_file=str(storage_path),
            source_sheet=spec.endpoint,
        )

    next_cursor = _next_cursor(payload)
    item = {
        "sync_item_id": sync_item_id,
        "sync_run_id": sync_run_id,
        "shop_id": shop_id,
        "source_kind": SOURCE_KIND_API_PULL,
        "endpoint": spec.endpoint,
        "export_type": spec.export_type,
        "table_hint": spec.table_hint,
        "status": status,
        "cursor": cursor,
        "page_number": page_number,
        "request_id": raw_index["request_id"],
        "rid": raw_index["rid"],
        "http_status": http_status,
        "error_code": error_code,
        "error_message": error_message,
        "raw_response_id": raw_response_id,
        "row_count": len(records),
        "started_at": pulled_at,
        "finished_at": _utc_like_now(),
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
        "endpoint": spec.endpoint,
        "export_type": spec.export_type,
        "table_hint": spec.table_hint,
        "status": status,
        "row_count": len(records),
        "storage_path": str(storage_path),
        "has_more": bool(payload.get("has_more")) if isinstance(payload, Mapping) else False,
        "next_cursor": next_cursor,
        "error_code": error_code,
        "error_message": error_message,
        **file_stats,
    }


def _request_body(
    *,
    spec: EndpointSpec,
    date_from: str | None,
    date_to: str | None,
    page_size: int,
    page_number: int,
    cursor: str | None,
    endpoint_params: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    body: dict[str, Any] = {"page_size": int(page_size)}
    if spec.page_param:
        body[spec.page_param] = page_number
    if cursor:
        body["next_key"] = cursor

    start_ts = _unix_time(date_from, is_end=False)
    end_ts = _unix_time(date_to, is_end=True)
    if spec.date_mode == "order_create_range" and (start_ts or end_ts):
        body["create_time_range"] = _time_range(start_ts, end_ts)
    elif spec.date_mode == "aftersale_create_range" and (start_ts or end_ts):
        if start_ts:
            body["begin_create_time"] = start_ts
        if end_ts:
            body["end_create_time"] = end_ts
    elif spec.date_mode == "flat_time_range" and (start_ts or end_ts):
        if start_ts:
            body["start_time"] = start_ts
        if end_ts:
            body["end_time"] = end_ts
    elif spec.date_mode == "compass_ds":
        body = {"ds": _compact_date(date_from or date_to or "")}
        if spec.name == "compass_product":
            body.update({"limit": int(page_size), "offset": max(0, page_number - 1) * int(page_size)})
        if spec.name == "compass_audience":
            body["type"] = 3
    elif spec.date_mode == "date_strings":
        if date_from:
            body["start_date"] = date_from
        if date_to:
            body["end_date"] = date_to

    for key in (spec.name, spec.endpoint):
        extra = endpoint_params.get(key)
        if extra:
            body.update(dict(extra))
    return body


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def _date_windows(spec: EndpointSpec, date_from: str | None, date_to: str | None) -> list[tuple[str | None, str | None]]:
    if spec.date_mode not in {"aftersale_create_range", "order_create_range", "compass_ds"} or not date_from or not date_to:
        return [(date_from, date_to)]
    start = datetime.fromisoformat(date_from).date()
    end = datetime.fromisoformat(date_to).date()
    if end < start:
        return [(date_from, date_to)]
    window_days = 7 if spec.date_mode == "order_create_range" else 1
    windows = []
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=window_days - 1), end)
        windows.append((current.isoformat(), window_end.isoformat()))
        current = window_end + timedelta(days=1)
    return windows


def _records_for_payload(
    client: WechatApiClient,
    spec: EndpointSpec,
    payload: Mapping[str, Any],
    timeout: int,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    records = _extract_records(payload, spec.records_keys)
    if records or not spec.detail_endpoint or not spec.detail_request_key:
        return records, []

    detail_payloads: list[Mapping[str, Any]] = []
    detail_records: list[Mapping[str, Any]] = []
    for item_id in _id_values(payload, spec.id_keys):
        _, detail_payload = client.post_json(spec.detail_endpoint, {spec.detail_request_key: item_id}, timeout)
        _raise_api_error(detail_payload)
        detail_payloads.append(detail_payload)
        detail_record = _extract_detail_record(detail_payload, spec.detail_record_keys)
        if detail_record is not None:
            detail_records.append(detail_record)
    return detail_records, detail_payloads


def _extract_records(payload: Any, keys: Iterable[str]) -> list[Mapping[str, Any]]:
    if isinstance(payload, (list, tuple)):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            if _looks_like_record(value):
                return [value]
            nested = _extract_records(value, keys)
            if nested:
                return nested
    data = payload.get("data")
    if data is not None and data is not payload:
        return _extract_records(data, keys)
    return []


def _extract_detail_record(payload: Mapping[str, Any], keys: Iterable[str]) -> Mapping[str, Any] | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    data = payload.get("data")
    if isinstance(data, Mapping):
        return _extract_detail_record(data, keys) or data
    return payload if _looks_like_record(payload) else None


def _id_values(payload: Mapping[str, Any], keys: Iterable[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value if item not in (None, ""))
        elif value not in (None, ""):
            values.append(str(value))
    data = payload.get("data")
    if not values and isinstance(data, Mapping):
        values.extend(_id_values(data, keys))
    return values


def _normalize_record_for_table(table_hint: str, record: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(record)
    if table_hint == "orders":
        return _normalize_order_record(row)
    if table_hint == "refunds":
        return _normalize_refund_record(row)
    if table_hint == "fund_flows":
        return _normalize_fund_flow_record(row)
    return row


def _with_request_context(spec: EndpointSpec, record: Mapping[str, Any], request_body: Mapping[str, Any]) -> Mapping[str, Any]:
    if spec.date_mode != "compass_ds" or not isinstance(record, Mapping):
        return record
    row = dict(record)
    ds = _text(request_body.get("ds"))
    if ds and not row.get("stat_date"):
        row["stat_date"] = _date_from_compact(ds)
    return row


def _date_from_compact(value: str) -> str:
    text = _text(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _normalize_order_record(row: dict[str, Any]) -> dict[str, Any]:
    detail = row.get("order_detail")
    if isinstance(detail, Mapping):
        price_info = detail.get("price_info")
        if isinstance(price_info, Mapping):
            row.setdefault("payment_amount", _first_value(price_info, ("order_price", "pay_amount", "payment_amount", "total_amount")))
            row.setdefault("shipping_amount", _first_value(price_info, ("freight", "shipping_fee", "shipping_amount")))
            row.setdefault("discount_amount", _first_value(price_info, ("discounted_price", "discount_amount", "coupon_amount")))
        if "items" not in row:
            items = _first_list(detail, ("product_infos", "product_info", "sku_infos", "sku_info", "goods", "goods_list", "product_list"))
            if items:
                row["items"] = [_normalize_order_item(dict(item)) for item in items]
    row.setdefault("order_created_at", _first_value(row, ("create_time", "createTime")))
    row.setdefault("paid_at", _first_value(row, ("pay_time", "payTime")))
    return row


def _normalize_order_item(item: dict[str, Any]) -> dict[str, Any]:
    item.setdefault("product_name", _first_value(item, ("title", "product_name", "goods_name", "name")))
    item.setdefault("quantity", _first_value(item, ("sku_cnt", "count", "num", "quantity")))
    item.setdefault("item_amount", _first_value(item, ("sku_merchant_receive_amount", "sale_price", "pay_amount", "item_amount")))
    return item


def _normalize_refund_record(row: dict[str, Any]) -> dict[str, Any]:
    row.setdefault("refund_id", _first_value(row, ("aftersale_order_id", "after_sale_order_id", "aftersale_id")))
    row.setdefault("refund_created_at", _first_value(row, ("create_time", "createTime")))
    row.setdefault("refund_completed_at", _first_value(row, ("finish_time", "complete_time", "completeTime")))
    return row


def _normalize_fund_flow_record(row: dict[str, Any]) -> dict[str, Any]:
    row.setdefault("flow_date", _first_value(row, ("bookkeeping_time", "create_time", "transaction_time")))
    related = _first_list(row, ("related_info_list",))
    if related:
        first_related = related[0]
        if isinstance(first_related, Mapping):
            row.setdefault("order_id", first_related.get("order_id"))
            row.setdefault("biz_type", first_related.get("related_type"))
    if not row.get("direction") and str(row.get("flow_type") or "") in {"1", "2"}:
        row["direction"] = "in" if str(row.get("flow_type")) == "1" else "out"
    return row


def _upsert_records_for_table(
    conn: Any,
    table_hint: str,
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str,
    source_file: str,
    source_sheet: str,
) -> dict[str, int]:
    rows = [dict(record) for record in records]
    if table_hint == "products":
        return upsert_product_records(
            conn,
            rows,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
        )
    if table_hint in {"orders", "refunds", "fund_flows"}:
        return upsert_business_records(
            conn,
            table_hint,
            rows,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
        )
    if table_hint in GENERIC_STANDARD_TABLES:
        return _upsert_generic_standard_records(
            conn,
            table_hint,
            rows,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
        )
    return {}


def _upsert_generic_standard_records(
    conn: Any,
    table: str,
    records: list[dict[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str,
    source_file: str,
    source_sheet: str,
) -> dict[str, int]:
    if not records:
        return {table: 0}
    normalized = _map_generic_standard_records(
        records,
        table=table,
        shop_id=shop_id,
        shop_name=shop_name,
        task_id=task_id,
        source_file=source_file,
        source_sheet=source_sheet,
    )
    count = upsert_records(conn, table, normalized)
    conn.commit()
    return {table: count}


def _map_generic_standard_records(
    records: Iterable[Mapping[str, Any]],
    *,
    table: str,
    shop_id: str,
    shop_name: str | None,
    task_id: str,
    source_file: str,
    source_sheet: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    specs = specs_for_table(table)
    for row_number, record in enumerate(records, start=1):
        sanitized = _strip_sensitive(record)
        row: dict[str, Any] = {
            "shop_id": shop_id,
            "shop_name_snapshot": shop_name,
            "task_id": task_id,
            "source_file": source_file,
            "source_sheet": source_sheet,
            "source_row_number": row_number,
            "raw_json": stable_json(sanitized),
        }
        for spec in specs:
            value = _first_value(record, (spec.name, *spec.aliases))
            if value in (None, ""):
                continue
            if spec.kind == FIELD_KIND_AMOUNT:
                row[spec.name] = to_float(value)
            elif spec.kind == FIELD_KIND_DATETIME:
                row[spec.name] = to_iso_datetime(value)
            else:
                row[spec.name] = to_text(value)
        _fill_generic_defaults(table, row, sanitized, shop_id, source_file, source_sheet, row_number)
        row["row_fingerprint"] = _generic_row_fingerprint(table, row, sanitized)
        rows.append(row)
    return rows


def _fill_generic_defaults(
    table: str,
    row: dict[str, Any],
    raw_row: Mapping[str, Any],
    shop_id: str,
    source_file: str,
    source_sheet: str,
    row_number: int,
) -> None:
    if table == "reviews":
        row.setdefault("review_id", fingerprint(shop_id, row.get("order_id"), row.get("product_id"), row.get("buyer_id"), row.get("review_created_at"), raw_row))
        if not row.get("is_positive") and row.get("rating") is not None:
            row["is_positive"] = "1" if float(row["rating"]) >= 4 else "0"
    elif table == "shop_daily":
        row.setdefault("stat_date", _date_from_source(source_file, source_sheet))
        row.setdefault("visitor_count", _first_value(raw_row, ("visitor_count", "visitorCount", "pay_uv", "product_click_uv")))
        row.setdefault("click_user_count", _first_value(raw_row, ("click_user_count", "clickUserCount", "product_click_uv")))
        row.setdefault("order_count", _first_value(raw_row, ("order_count", "orderCount", "pay_order_cnt")))
        row.setdefault("buyer_count", _first_value(raw_row, ("buyer_count", "buyerCount", "pay_uv")))
        row.setdefault("payment_amount", _first_value(raw_row, ("payment_amount", "paymentAmount", "pay_gmv")))
        row.setdefault("refund_amount", _first_value(raw_row, ("refund_amount", "refundAmount", "pay_refund_gmv")))
    elif table == "product_daily":
        row.setdefault("stat_date", _date_from_source(source_file, source_sheet))
        if not row.get("product_id") and row.get("product_name"):
            row["product_id"] = fingerprint("product", shop_id, row.get("product_name"), row.get("sku_id"))
    elif table == "traffic_sources":
        row.setdefault("stat_date", _date_from_source(source_file, source_sheet))
        row.setdefault("source_name", row.get("source_type") or "unknown")
    elif table == "ad_spend":
        row.setdefault("stat_date", _date_from_source(source_file, source_sheet))
        row.setdefault("platform", "wechat")
    elif table == "audience_insights":
        if not row.get("dimension"):
            for field_name, dimension in (
                ("gender", "gender"),
                ("age_group", "age_group"),
                ("region", "region"),
                ("consumption_level", "consumption_level"),
                ("active_hour", "active_hour"),
            ):
                if row.get(field_name):
                    row["dimension"] = dimension
                    break
        if not row.get("segment_label"):
            for field_name in ("gender", "age_group", "region", "consumption_level", "active_hour", "category"):
                if row.get(field_name):
                    row["segment_label"] = row.get(field_name)
                    break
        row.setdefault("insight_id", fingerprint(shop_id, row.get("product_id"), row.get("product_name"), row.get("dimension"), row.get("segment_label"), source_file, source_sheet, row_number, raw_row))


def _generic_row_fingerprint(table: str, row: Mapping[str, Any], raw_row: Mapping[str, Any]) -> str:
    shop_id = row.get("shop_id")
    if table == "reviews" and shop_id and row.get("review_id"):
        return fingerprint(table, shop_id, row.get("review_id"))
    if table == "shop_daily" and shop_id and row.get("stat_date"):
        return fingerprint(table, shop_id, row.get("stat_date"))
    if table == "product_daily" and shop_id and row.get("stat_date") and row.get("product_id"):
        return fingerprint(table, shop_id, row.get("stat_date"), row.get("product_id"), row.get("sku_id"))
    if table == "traffic_sources" and shop_id and row.get("stat_date") and row.get("source_name"):
        return fingerprint(table, shop_id, row.get("stat_date"), row.get("source_name"), row.get("product_id"))
    if table == "ad_spend" and shop_id and row.get("stat_date") and (row.get("campaign_id") or row.get("campaign_name")):
        return fingerprint(table, shop_id, row.get("stat_date"), row.get("platform"), row.get("campaign_id"), row.get("campaign_name"), row.get("ad_group_id"), row.get("product_id"))
    if table == "audience_insights" and shop_id and row.get("insight_id"):
        return fingerprint(table, shop_id, row.get("insight_id"))
    return fingerprint(table, shop_id, row.get("source_file"), row.get("source_sheet"), row.get("source_row_number"), raw_row)


def _endpoint_spec(name: str) -> EndpointSpec:
    custom = _custom_generic_endpoint_spec(name)
    if custom is not None:
        return custom
    normalized = name.strip().casefold()
    normalized = ENDPOINT_ALIASES.get(normalized, normalized)
    if normalized in ENDPOINT_SPECS:
        return ENDPOINT_SPECS[normalized]
    if normalized.startswith("/"):
        return EndpointSpec(
            name=_filename_part(normalized),
            endpoint=normalized,
            export_type=_filename_part(normalized),
            table_hint="",
        )
    raise ValueError(f"unsupported WeChat API endpoint: {name}")


def _custom_generic_endpoint_spec(name: str) -> EndpointSpec | None:
    for separator in ("=", ":"):
        if separator not in name:
            continue
        table_name, endpoint = (part.strip() for part in name.split(separator, 1))
        table_name = table_name.casefold()
        if endpoint.startswith("/") and table_name in GENERIC_STANDARD_TABLES:
            return EndpointSpec(
                name=table_name,
                endpoint=endpoint,
                export_type=table_name,
                table_hint=table_name,
                date_mode="date_strings",
                records_keys=(table_name, "records", "data", "list", "items"),
            )
    return None


def _raise_api_error(payload: Mapping[str, Any]) -> None:
    errcode = payload.get("errcode")
    if errcode in (None, 0, "0"):
        return
    errmsg = payload.get("errmsg") or payload.get("message") or "WeChat API error"
    raise RuntimeError(f"WeChat API error {errcode}: {errmsg}")


def _next_cursor(payload: Mapping[str, Any]) -> str | None:
    return _text(_first_value(payload, ("next_key", "nextKey", "cursor", "next_cursor")))


def _time_range(start_ts: int | None, end_ts: int | None) -> dict[str, int]:
    value: dict[str, int] = {}
    if start_ts:
        value["start_time"] = start_ts
    if end_ts:
        value["end_time"] = end_ts
    return value


def _unix_time(value: str | None, *, is_end: bool) -> int | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if len(text) == 10:
        date_value = datetime.strptime(text, "%Y-%m-%d").date()
        dt = datetime.combine(date_value, time.max if is_end else time.min, tzinfo=CHINA_TZ)
        return int(dt.timestamp())
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return int(parsed.timestamp())


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


def _strip_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_sensitive(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_strip_sensitive(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _first_value(record: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_list(record: Mapping[str, Any], keys: Iterable[str]) -> list[Mapping[str, Any]]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (list, tuple)):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _looks_like_record(value: Mapping[str, Any]) -> bool:
    keys = {str(key) for key in value}
    return bool(keys & RECORD_HINT_KEYS) or any(key.endswith("_id") for key in keys)


def _date_from_source(source_file: str, source_sheet: str | None) -> str | None:
    text = f"{Path(source_file).stem} {source_sheet or ''}"
    digits = "".join(char if char.isdigit() else " " for char in text).split()
    for token in digits:
        if len(token) == 8:
            return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
    return None


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _file_stats(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:16]}"


def _filename_part(value: str) -> str:
    text = value.strip().strip("/") or "endpoint"
    return "".join(char if char.isalnum() else "_" for char in text).strip("_") or "endpoint"


def _utc_like_now() -> str:
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")
