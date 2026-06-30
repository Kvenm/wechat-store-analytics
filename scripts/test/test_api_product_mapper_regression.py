#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from sync.product_mapper import map_product_records, product_records_from_payload, upsert_product_records  # noqa: E402
from warehouse.repository import initialize_database  # noqa: E402


SHOP_ID = "mapper-shop-001"
SHOP_NAME = "Mapper Test Shop"
TASK_ID = "mapper-sync-run-001"
SENSITIVE_SENTINELS = (
    "product-token-sentinel-must-not-persist-7a0c",
    "sku-cookie-sentinel-must-not-persist-2f37",
    "nested-secret-sentinel-must-not-persist-5e19",
    "Bearer auth-sentinel-must-not-persist-83cf",
)


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_maps_api_product_records_into_business_tables,
        test_product_records_from_common_payload_envelopes,
    )

    failed = 0
    for test in tests:
        try:
            test()
        except Exception:
            failed += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")

    if failed:
        print(f"{failed} API product mapper regression test(s) failed.")
        return 1

    print(f"{len(tests)} API product mapper regression tests passed.")
    return 0


def test_maps_api_product_records_into_business_tables() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_product_mapper_regression_") as temp_dir:
        db_path = Path(temp_dir) / "warehouse.sqlite"
        with connect(db_path) as conn:
            initialize_database(conn)

            counts = upsert_product_records(
                conn,
                product_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-products.json",
            )
            assert counts == {"products": 2, "product_skus": 3}
            assert_table_count(conn, "products", 2)
            assert_table_count(conn, "product_skus", 3)
            assert_product_fields(conn)
            assert_sku_fields(conn)
            assert_no_sensitive_sentinels(conn)
            first_snapshot = snapshot_business_keys(conn)

            repeat_counts = upsert_product_records(
                conn,
                product_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-products.json",
            )
            assert repeat_counts == {"products": 2, "product_skus": 3}
            assert_table_count(conn, "products", 2)
            assert_table_count(conn, "product_skus", 3)
            assert snapshot_business_keys(conn) == first_snapshot
            assert_no_sensitive_sentinels(conn)

    mapping = map_product_records(product_records(), shop_id=SHOP_ID, shop_name=SHOP_NAME, task_id=TASK_ID)
    assert len(mapping.products) == 2
    assert len(mapping.product_skus) == 3


def test_product_records_from_common_payload_envelopes() -> None:
    records = [{"product_id": "p-from-records"}]
    assert product_records_from_payload({"records": records}) == records
    assert product_records_from_payload({"data": {"productList": records}}) == records
    assert product_records_from_payload({"data": records}) == records
    assert product_records_from_payload({"errcode": 0}) == []


def product_records() -> list[dict[str, Any]]:
    return [
        {
            "productId": "prod-001",
            "title": "Alias Rain Jacket",
            "status": "online",
            "category_name": "Outerwear",
            "min_price": "129.90",
            "stock_num": "42",
            "access_token": SENSITIVE_SENTINELS[0],
            "headers": {"Authorization": SENSITIVE_SENTINELS[3]},
            "sku_list": [
                {
                    "skuId": "sku-001-red",
                    "spec": "Red / M",
                    "sale_price": "129.90",
                    "stock_num": "12",
                    "barcode": "690000000001",
                    "cookie": SENSITIVE_SENTINELS[1],
                },
                {
                    "id": "sku-001-blue",
                    "name": "Blue / L",
                    "price": 139.0,
                    "stock": 8,
                    "barcode": "690000000002",
                    "meta": {"secret": SENSITIVE_SENTINELS[2]},
                },
            ],
        },
        {
            "spu_id": "prod-002",
            "name": "Nested SKU Hat",
            "status": "draft",
            "category": {"name": "Accessories"},
            "price": 59,
            "stock": 20,
            "sku_info": {
                "list": [
                    {
                        "sku_id": "sku-002-one",
                        "sku_name": "One Size",
                        "price": "59",
                        "stock_num": "20",
                        "barcode": "690000000003",
                    }
                ]
            },
        },
    ]


def assert_product_fields(conn: sqlite3.Connection) -> None:
    product = fetch_one(conn, "SELECT * FROM products WHERE product_id = ?", ("prod-001",))
    assert product["shop_id"] == SHOP_ID
    assert product["shop_name_snapshot"] == SHOP_NAME
    assert product["task_id"] == TASK_ID
    assert product["product_name"] == "Alias Rain Jacket"
    assert product["category"] == "Outerwear"
    assert product["status"] == "online"
    assert product["price"] == 129.9
    assert product["stock"] == 42.0
    assert product["sku_id"] is None
    assert product["sku_name"] is None
    assert product["source_file"] == "/tmp/mock-products.json"

    nested = fetch_one(conn, "SELECT * FROM products WHERE product_id = ?", ("prod-002",))
    assert nested["product_name"] == "Nested SKU Hat"
    assert nested["category"] == "Accessories"
    assert nested["price"] == 59.0
    assert nested["stock"] == 20.0

    raw_json = json.loads(product["raw_json"])
    assert raw_json["productId"] == "prod-001"
    assert "access_token" not in raw_json
    assert "Authorization" not in raw_json["headers"]


def assert_sku_fields(conn: sqlite3.Connection) -> None:
    red = fetch_one(conn, "SELECT * FROM product_skus WHERE sku_id = ?", ("sku-001-red",))
    assert red["shop_id"] == SHOP_ID
    assert red["product_id"] == "prod-001"
    assert red["product_name"] == "Alias Rain Jacket"
    assert red["sku_name"] == "Red / M"
    assert red["category"] == "Outerwear"
    assert red["status"] == "online"
    assert red["sku_price"] == 129.9
    assert red["stock"] == 12.0
    assert red["barcode"] == "690000000001"

    blue = fetch_one(conn, "SELECT * FROM product_skus WHERE sku_id = ?", ("sku-001-blue",))
    assert blue["sku_name"] == "Blue / L"
    assert blue["sku_price"] == 139.0
    assert blue["stock"] == 8.0

    nested = fetch_one(conn, "SELECT * FROM product_skus WHERE sku_id = ?", ("sku-002-one",))
    assert nested["product_id"] == "prod-002"
    assert nested["sku_name"] == "One Size"
    assert nested["sku_price"] == 59.0
    assert nested["stock"] == 20.0

    raw_json = json.loads(red["raw_json"])
    assert raw_json["skuId"] == "sku-001-red"
    assert "cookie" not in raw_json


def assert_no_sensitive_sentinels(conn: sqlite3.Connection) -> None:
    for table in ("products", "product_skus"):
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
        payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
        lowered_payload = payload.lower()
        for sentinel in SENSITIVE_SENTINELS:
            assert sentinel not in payload, f"sensitive sentinel persisted in {table}: {sentinel}"
        for term in ("access_token", "authorization", "cookie", "secret", "token"):
            assert term not in lowered_payload, f"sensitive key persisted in {table}: {term}"


def snapshot_business_keys(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        "products": [
            dict(row)
            for row in conn.execute(
                """
                SELECT shop_id, product_id, product_name, row_fingerprint
                FROM products
                ORDER BY row_fingerprint
                """
            ).fetchall()
        ],
        "product_skus": [
            dict(row)
            for row in conn.execute(
                """
                SELECT shop_id, product_id, sku_id, sku_name, row_fingerprint
                FROM product_skus
                ORDER BY row_fingerprint
                """
            ).fetchall()
        ],
    }


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def assert_table_count(conn: sqlite3.Connection, table: str, expected: int) -> None:
    actual = int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
    assert actual == expected, f"expected {expected} {table} rows, got {actual}"


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> Mapping[str, Any]:
    row = conn.execute(sql, params).fetchone()
    assert row is not None, f"query returned no rows: {sql} {params}"
    return row


if __name__ == "__main__":
    raise SystemExit(main())
