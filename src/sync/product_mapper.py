from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from shared.ids import fingerprint, stable_json
from warehouse.repository import ensure_shop, upsert_records


PRODUCT_ID_KEYS = ("product_id", "productId", "spu_id", "spuId", "goods_id", "goodsId", "id")
PRODUCT_NAME_KEYS = ("product_name", "productName", "goods_name", "goodsName", "name", "title")
SKU_ID_KEYS = ("sku_id", "skuId", "id")
SKU_NAME_KEYS = ("sku_name", "skuName", "name", "spec", "specs", "title")
PRODUCT_ROW_SKU_ID_KEYS = ("sku_id", "skuId")
PRODUCT_ROW_SKU_NAME_KEYS = ("sku_name", "skuName", "spec")
CATEGORY_KEYS = ("category", "category_name", "categoryName")
PRICE_KEYS = ("price", "min_price", "minPrice", "sale_price", "salePrice")
STOCK_KEYS = ("stock", "stock_num", "stockNum", "stock_quantity", "stockQuantity")
SKU_CONTAINER_KEYS = ("sku_list", "skuList", "skus", "sku_info", "skuInfo", "list")
SKU_NESTED_LIST_KEYS = ("list", "items", "sku_list", "skuList", "skus")
BARCODE_KEYS = ("barcode", "bar_code", "barCode")
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
class ProductMapping:
    products: list[dict[str, Any]]
    product_skus: list[dict[str, Any]]


def map_product_records(
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> ProductMapping:
    products: list[dict[str, Any]] = []
    product_skus: list[dict[str, Any]] = []

    for row_number, record in enumerate(records, start=1):
        product = _map_product_record(
            record,
            shop_id=shop_id,
            shop_name=shop_name,
            task_id=task_id,
            source_file=source_file,
            source_sheet=source_sheet,
            source_row_number=row_number,
        )
        products.append(product)

        for sku_index, sku in enumerate(_sku_records(record), start=1):
            sku_row = _map_sku_record(
                sku,
                product=product,
                product_record=record,
                sku_index=sku_index,
                source_row_number=row_number,
            )
            if sku_row is not None:
                product_skus.append(sku_row)

    return ProductMapping(products=products, product_skus=product_skus)


def upsert_product_records(
    conn: sqlite3.Connection,
    records: Iterable[Mapping[str, Any]],
    *,
    shop_id: str,
    shop_name: str | None = None,
    task_id: str | None = None,
    source_file: str | None = None,
    source_sheet: str | None = None,
) -> dict[str, int]:
    mapping = map_product_records(
        records,
        shop_id=shop_id,
        shop_name=shop_name,
        task_id=task_id,
        source_file=source_file,
        source_sheet=source_sheet,
    )
    return upsert_product_mapping(conn, mapping, shop_id=shop_id, shop_name=shop_name)


def upsert_product_mapping(
    conn: sqlite3.Connection,
    mapping: ProductMapping,
    *,
    shop_id: str,
    shop_name: str | None = None,
) -> dict[str, int]:
    ensure_shop(conn, shop_id, shop_name)
    counts = {
        "products": upsert_records(conn, "products", mapping.products),
        "product_skus": upsert_records(conn, "product_skus", mapping.product_skus),
    }
    conn.commit()
    return counts


def product_records_from_payload(payload: Any) -> list[Mapping[str, Any]]:
    """Extract product records from common API response envelope shapes."""
    if isinstance(payload, (list, tuple)):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []

    for key in ("records", "products", "product_list", "productList", "items", "list"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            return [item for item in value if isinstance(item, Mapping)]

    data = payload.get("data")
    if isinstance(data, Mapping):
        return product_records_from_payload(data)
    if isinstance(data, (list, tuple)):
        return [item for item in data if isinstance(item, Mapping)]

    return []


def _map_product_record(
    record: Mapping[str, Any],
    *,
    shop_id: str,
    shop_name: str | None,
    task_id: str | None,
    source_file: str | None,
    source_sheet: str | None,
    source_row_number: int,
) -> dict[str, Any]:
    product_id = _required_text(_first_value(record, PRODUCT_ID_KEYS), "product record missing product_id")
    product_name = _text(_first_value(record, PRODUCT_NAME_KEYS))
    sku_id = _text(_first_value(record, PRODUCT_ROW_SKU_ID_KEYS))
    sku_name = _text(_first_value(record, PRODUCT_ROW_SKU_NAME_KEYS))

    return {
        "shop_id": shop_id,
        "shop_name_snapshot": shop_name,
        "task_id": task_id,
        "product_id": product_id,
        "product_name": product_name,
        "sku_id": sku_id,
        "sku_name": sku_name,
        "category": _category_text(_first_value(record, CATEGORY_KEYS)),
        "status": _text(record.get("status")),
        "price": _number(_first_value(record, PRICE_KEYS)),
        "stock": _number(_first_value(record, STOCK_KEYS)),
        "source_file": source_file,
        "source_sheet": source_sheet,
        "source_row_number": source_row_number,
        "row_fingerprint": fingerprint("products", (shop_id, product_id, sku_id)),
        "raw_json": _safe_raw_json(record),
    }


def _map_sku_record(
    sku: Mapping[str, Any],
    *,
    product: Mapping[str, Any],
    product_record: Mapping[str, Any],
    sku_index: int,
    source_row_number: int,
) -> dict[str, Any] | None:
    sku_id = _text(_first_value(sku, SKU_ID_KEYS))
    if sku_id is None:
        return None

    shop_id = _required_text(product.get("shop_id"), "product row missing shop_id")
    product_id = _required_text(product.get("product_id"), "product row missing product_id")
    sku_name = _text(_first_value(sku, SKU_NAME_KEYS))
    sku_price = _number(_first_value(sku, PRICE_KEYS))
    if sku_price is None:
        sku_price = _number(_first_value(product_record, PRICE_KEYS))
    stock = _number(_first_value(sku, STOCK_KEYS))
    if stock is None:
        stock = _number(_first_value(product_record, STOCK_KEYS))

    return {
        "shop_id": shop_id,
        "shop_name_snapshot": product.get("shop_name_snapshot"),
        "task_id": product.get("task_id"),
        "product_id": product_id,
        "product_name": product.get("product_name"),
        "sku_id": sku_id,
        "sku_name": sku_name,
        "category": product.get("category"),
        "status": product.get("status"),
        "sku_price": sku_price,
        "stock": stock,
        "barcode": _text(_first_value(sku, BARCODE_KEYS)),
        "source_file": product.get("source_file"),
        "source_sheet": product.get("source_sheet"),
        "source_row_number": source_row_number,
        "row_fingerprint": fingerprint("product_skus", (shop_id, product_id, sku_id)),
        "raw_json": _safe_raw_json(sku, fallback={"sku_index": sku_index}),
    }


def _sku_records(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in SKU_CONTAINER_KEYS:
        value = record.get(key)
        items = _coerce_sku_records(value)
        if items:
            return items
    return []


def _coerce_sku_records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []

    for key in SKU_NESTED_LIST_KEYS:
        nested = value.get(key)
        if isinstance(nested, (list, tuple)):
            return [item for item in nested if isinstance(item, Mapping)]

    if _looks_like_sku(value):
        return [value]
    return []


def _looks_like_sku(value: Mapping[str, Any]) -> bool:
    keys = set(value)
    known_keys = set(SKU_ID_KEYS + SKU_NAME_KEYS + PRICE_KEYS + STOCK_KEYS + BARCODE_KEYS)
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
        for key in ("name", "category_name", "title", "value", "id"):
            nested = value.get(key)
            if _has_value(nested):
                return _text(nested)
        return stable_json(value)
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = str(value).strip()
    return text or None


def _category_text(value: Any) -> str | None:
    return _text(value)


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
