from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from shared.ids import fingerprint, stable_json
from warehouse.repository import ensure_shop, upsert_records


ORDER_ID_KEYS = ("order_id", "orderId", "id")
RELATED_ORDER_ID_KEYS = ("order_id", "orderId", "order_no", "orderNo")
BUYER_ID_KEYS = ("buyer_id", "buyerId", "openid", "buyerOpenid")
ORDER_STATUS_KEYS = ("status", "order_status", "orderStatus")
ORDER_CREATED_AT_KEYS = ("order_created_at", "orderCreatedAt", "create_time", "createTime", "order_time", "orderTime")
PAID_AT_KEYS = ("paid_at", "paidAt", "pay_time", "payTime")
PAYMENT_AMOUNT_KEYS = (
    "payment_amount",
    "paymentAmount",
    "pay_amount",
    "payAmount",
    "order_amount",
    "orderAmount",
    "total_amount",
    "totalAmount",
)
SHIPPING_AMOUNT_KEYS = ("shipping_amount", "shippingAmount", "freight", "shipping_fee", "shippingFee")
DISCOUNT_AMOUNT_KEYS = ("discount_amount", "discountAmount", "discount")
REFUND_AMOUNT_KEYS = ("refund_amount", "refundAmount")
CURRENCY_KEYS = ("currency", "currency_type", "currencyType")

ORDER_ITEM_CONTAINER_KEYS = (
    "items",
    "order_items",
    "orderItems",
    "product_list",
    "productList",
    "goods",
    "goods_list",
    "goodsList",
    "list",
)
ORDER_ITEM_NESTED_LIST_KEYS = ("items", "list", "goods", "goods_list", "goodsList", "product_list", "productList")
ORDER_ITEM_ID_KEYS = ("order_item_id", "orderItemId", "item_id", "itemId", "sub_order_id", "subOrderId", "id")
PRODUCT_ID_KEYS = ("product_id", "productId", "spu_id", "spuId", "goods_id", "goodsId")
PRODUCT_NAME_KEYS = ("product_name", "productName", "name", "title", "goods_name", "goodsName")
SKU_ID_KEYS = ("sku_id", "skuId")
SKU_NAME_KEYS = ("sku_name", "skuName", "spec", "sku_name", "specs", "sku")
QUANTITY_KEYS = ("quantity", "qty", "count", "num")
ITEM_AMOUNT_KEYS = ("item_amount", "itemAmount", "pay_amount", "payAmount", "amount", "price")

REFUND_ID_KEYS = ("refund_id", "refundId", "aftersale_id", "aftersaleId", "after_sale_id", "afterSaleId", "id")
REFUND_STATUS_KEYS = ("refund_status", "refundStatus", "status")
REFUND_CREATED_AT_KEYS = ("refund_created_at", "refundCreatedAt", "create_time", "createTime")
REFUND_COMPLETED_AT_KEYS = ("refund_completed_at", "refundCompletedAt", "complete_time", "completeTime")
REFUND_REASON_KEYS = ("reason", "refund_reason", "refundReason")

FLOW_ID_KEYS = ("flow_id", "flowId", "bill_id", "billId", "transaction_id", "transactionId", "id")
FLOW_DATE_KEYS = ("flow_date", "flowDate", "create_time", "createTime", "transaction_time", "transactionTime")
FLOW_TYPE_KEYS = ("flow_type", "flowType", "type")
BIZ_TYPE_KEYS = ("biz_type", "bizType")
AMOUNT_KEYS = ("amount", "payment_amount", "paymentAmount", "pay_amount", "payAmount")
DIRECTION_KEYS = ("direction",)
BALANCE_KEYS = ("balance",)
REMARK_KEYS = ("remark", "memo", "description")

SENSITIVE_KEY_PARTS = (
    "access_token",
    "authorization",
    "cookie",
    "credential",
    "secret",
    "session",
    "token",
)


@dataclass(frozen=True)
class OrderMapping:
    orders: list[dict[str, Any]]
    order_items: list[dict[str, Any]]


@dataclass(frozen=True)
class BusinessMapping:
    orders: list[dict[str, Any]]
    order_items: list[dict[str, Any]]
    refunds: list[dict[str, Any]]
    fund_flows: list[dict[str, Any]]


def map_order_records(
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> OrderMapping:
    orders: list[dict[str, Any]] = []
    order_items: list[dict[str, Any]] = []

    for row_number, record in enumerate(records, start=1):
        order = _map_order_record(
            record,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
            source_row_number=row_number,
        )
        orders.append(order)

        for item_index, item in enumerate(_order_item_records(record), start=1):
            order_item = _map_order_item_record(
                item,
                order=order,
                item_index=item_index,
                source_row_number=row_number,
            )
            order_items.append(order_item)

    return OrderMapping(orders=orders, order_items=order_items)


def upsert_order_records(
    conn: sqlite3.Connection,
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> dict[str, int]:
    mapping = map_order_records(
        records,
        shop_id=shop_id,
        shop_name=shop_name,
        task_id=task_id,
        source_file=source_file,
        source_sheet=source_sheet,
    )
    ensure_shop(conn, shop_id, shop_name)
    counts = {
        "orders": upsert_records(conn, "orders", mapping.orders),
        "order_items": upsert_records(conn, "order_items", mapping.order_items),
    }
    conn.commit()
    return counts


def map_refund_records(
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> list[dict[str, Any]]:
    return [
        _map_refund_record(
            record,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
            source_row_number=row_number,
        )
        for row_number, record in enumerate(records, start=1)
    ]


def upsert_refund_records(
    conn: sqlite3.Connection,
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> dict[str, int]:
    rows = map_refund_records(
        records,
        shop_id=shop_id,
        shop_name=shop_name,
        task_id=task_id,
        source_file=source_file,
        source_sheet=source_sheet,
    )
    ensure_shop(conn, shop_id, shop_name)
    counts = {"refunds": upsert_records(conn, "refunds", rows)}
    conn.commit()
    return counts


def map_fund_flow_records(
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> list[dict[str, Any]]:
    return [
        _map_fund_flow_record(
            record,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
            source_row_number=row_number,
        )
        for row_number, record in enumerate(records, start=1)
    ]


def upsert_fund_flow_records(
    conn: sqlite3.Connection,
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> dict[str, int]:
    rows = map_fund_flow_records(
        records,
        shop_id=shop_id,
        shop_name=shop_name,
        task_id=task_id,
        source_file=source_file,
        source_sheet=source_sheet,
    )
    ensure_shop(conn, shop_id, shop_name)
    counts = {"fund_flows": upsert_records(conn, "fund_flows", rows)}
    conn.commit()
    return counts


def upsert_business_records(
    conn: sqlite3.Connection,
    table_hint: str,
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> dict[str, int]:
    normalized = table_hint.strip().casefold()
    if normalized in {"orders", "order", "transactions"}:
        return upsert_order_records(
            conn,
            records,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
        )
    if normalized in {"refunds", "refund", "aftersale", "after_sale"}:
        return upsert_refund_records(
            conn,
            records,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
        )
    if normalized in {"fund_flows", "fund_flow", "funds", "fund"}:
        return upsert_fund_flow_records(
            conn,
            records,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
        )
    return {}


def _map_order_record(
    record: Mapping[str, Any],
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str | None,
    source_file: str | None,
    source_sheet: str | None,
    source_row_number: int,
) -> dict[str, Any]:
    order_id = _required_text(_first_value(record, ORDER_ID_KEYS), "order record missing order_id")

    return {
        "shop_id": shop_id,
        "shop_name_snapshot": shop_name,
        "task_id": task_id,
        "order_id": order_id,
        "buyer_id": _text(_first_value(record, BUYER_ID_KEYS)),
        "status": _text(_first_value(record, ORDER_STATUS_KEYS)),
        "order_created_at": _text(_first_value(record, ORDER_CREATED_AT_KEYS)),
        "paid_at": _text(_first_value(record, PAID_AT_KEYS)),
        "payment_amount": _number(_first_value(record, PAYMENT_AMOUNT_KEYS)),
        "shipping_amount": _number(_first_value(record, SHIPPING_AMOUNT_KEYS)),
        "discount_amount": _number(_first_value(record, DISCOUNT_AMOUNT_KEYS)),
        "refund_amount": _number(_first_value(record, REFUND_AMOUNT_KEYS)),
        "currency": _text(_first_value(record, CURRENCY_KEYS)) or "CNY",
        "source_file": source_file,
        "source_sheet": source_sheet,
        "source_row_number": source_row_number,
        "row_fingerprint": fingerprint("orders", (shop_id, order_id)),
        "raw_json": _safe_raw_json(record),
    }


def _map_order_item_record(
    item: Mapping[str, Any],
    *,
    order: Mapping[str, Any],
    item_index: int,
    source_row_number: int,
) -> dict[str, Any]:
    shop_id = _required_text(order.get("shop_id"), "order row missing shop_id")
    order_id = _required_text(order.get("order_id"), "order row missing order_id")
    product_id = _text(_first_value(item, PRODUCT_ID_KEYS))
    sku_id = _text(_first_value(item, SKU_ID_KEYS))
    order_item_id = _text(_first_value(item, ORDER_ITEM_ID_KEYS))
    if order_item_id is None:
        order_item_id = fingerprint("order_item_id", (shop_id, order_id, product_id, sku_id, item_index))

    return {
        "shop_id": shop_id,
        "shop_name_snapshot": order.get("shop_name_snapshot"),
        "task_id": order.get("task_id"),
        "order_item_id": order_item_id,
        "order_id": order_id,
        "product_id": product_id,
        "product_name": _text(_first_value(item, PRODUCT_NAME_KEYS)),
        "sku_id": sku_id,
        "sku_name": _text(_first_value(item, SKU_NAME_KEYS)),
        "quantity": _number(_first_value(item, QUANTITY_KEYS)) or 1.0,
        "item_amount": _number(_first_value(item, ITEM_AMOUNT_KEYS)),
        "refund_amount": _number(_first_value(item, REFUND_AMOUNT_KEYS)),
        "source_file": order.get("source_file"),
        "source_sheet": order.get("source_sheet"),
        "source_row_number": source_row_number,
        "row_fingerprint": fingerprint("order_items", (shop_id, order_item_id, order_id)),
        "raw_json": _safe_raw_json(item, fallback={"item_index": item_index, "order_id": order_id}),
    }


def _map_refund_record(
    record: Mapping[str, Any],
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str | None,
    source_file: str | None,
    source_sheet: str | None,
    source_row_number: int,
) -> dict[str, Any]:
    sanitized = _strip_sensitive(record)
    order_id = _text(_first_value(record, RELATED_ORDER_ID_KEYS))
    product_id = _text(_first_value(record, PRODUCT_ID_KEYS))
    sku_id = _text(_first_value(record, SKU_ID_KEYS))
    refund_amount = _number(_first_value(record, REFUND_AMOUNT_KEYS + AMOUNT_KEYS))
    refund_created_at = _text(_first_value(record, REFUND_CREATED_AT_KEYS))
    refund_id = _text(_first_value(record, REFUND_ID_KEYS))
    if refund_id is None:
        refund_id = fingerprint(
            "refund_id",
            (shop_id, order_id, product_id, sku_id, refund_amount, refund_created_at, sanitized),
        )

    return {
        "shop_id": shop_id,
        "shop_name_snapshot": shop_name,
        "task_id": task_id,
        "refund_id": refund_id,
        "order_id": order_id,
        "product_id": product_id,
        "product_name": _text(_first_value(record, PRODUCT_NAME_KEYS)),
        "sku_id": sku_id,
        "refund_status": _text(_first_value(record, REFUND_STATUS_KEYS)),
        "refund_amount": refund_amount,
        "refund_created_at": refund_created_at,
        "refund_completed_at": _text(_first_value(record, REFUND_COMPLETED_AT_KEYS)),
        "reason": _text(_first_value(record, REFUND_REASON_KEYS)),
        "source_file": source_file,
        "source_sheet": source_sheet,
        "source_row_number": source_row_number,
        "row_fingerprint": fingerprint("refunds", (shop_id, refund_id, order_id)),
        "raw_json": _safe_raw_json(record),
    }


def _map_fund_flow_record(
    record: Mapping[str, Any],
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str | None,
    source_file: str | None,
    source_sheet: str | None,
    source_row_number: int,
) -> dict[str, Any]:
    sanitized = _strip_sensitive(record)
    flow_date = _text(_first_value(record, FLOW_DATE_KEYS))
    flow_type = _text(_first_value(record, FLOW_TYPE_KEYS))
    order_id = _text(_first_value(record, RELATED_ORDER_ID_KEYS))
    signed_amount = _number(_first_value(record, AMOUNT_KEYS))
    direction = _text(_first_value(record, DIRECTION_KEYS))
    if signed_amount is not None and not direction:
        direction = "out" if signed_amount < 0 else "in"
    amount = abs(signed_amount) if signed_amount is not None else None
    flow_id = _text(_first_value(record, FLOW_ID_KEYS))
    if flow_id is None:
        flow_id = fingerprint(
            "fund_flow_id",
            (shop_id, flow_date, flow_type, order_id, signed_amount, sanitized),
        )

    return {
        "shop_id": shop_id,
        "shop_name_snapshot": shop_name,
        "task_id": task_id,
        "flow_id": flow_id,
        "flow_date": flow_date,
        "flow_type": flow_type,
        "biz_type": _text(_first_value(record, BIZ_TYPE_KEYS)),
        "order_id": order_id,
        "amount": amount,
        "currency": _text(_first_value(record, CURRENCY_KEYS)) or "CNY",
        "direction": direction,
        "balance": _number(_first_value(record, BALANCE_KEYS)),
        "remark": _text(_first_value(record, REMARK_KEYS)),
        "source_file": source_file,
        "source_sheet": source_sheet,
        "source_row_number": source_row_number,
        "row_fingerprint": fingerprint("fund_flows", (shop_id, flow_id)),
        "raw_json": _safe_raw_json(record),
    }


def _order_item_records(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ORDER_ITEM_CONTAINER_KEYS:
        value = record.get(key)
        items = _coerce_item_records(value)
        if items:
            return items
    return []


def _coerce_item_records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []

    for key in ORDER_ITEM_NESTED_LIST_KEYS:
        nested = value.get(key)
        if isinstance(nested, (list, tuple)):
            return [item for item in nested if isinstance(item, Mapping)]

    if _looks_like_item(value):
        return [value]
    return []


def _looks_like_item(value: Mapping[str, Any]) -> bool:
    keys = set(value)
    known_keys = set(
        ORDER_ITEM_ID_KEYS
        + PRODUCT_ID_KEYS
        + PRODUCT_NAME_KEYS
        + SKU_ID_KEYS
        + SKU_NAME_KEYS
        + QUANTITY_KEYS
        + ITEM_AMOUNT_KEYS
    )
    return bool(keys & known_keys)


def _first_value(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        if _has_value(value):
            return value
    return None


def _required_text(value: Any, message: str) -> str:
    text = _text(value)
    if text is None:
        raise ValueError(message)
    return text


def _text(value: Any) -> str | None:
    if not _has_value(value):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        for key in ("name", "title", "value", "id"):
            nested = value.get(key)
            if _has_value(nested):
                return _text(nested)
        return stable_json(value)
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _safe_raw_json(record: Mapping[str, Any], *, fallback: Mapping[str, Any] | None = None) -> str:
    sanitized = _strip_sensitive(record)
    if not sanitized and fallback:
        sanitized = dict(fallback)
    return stable_json(sanitized)


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
