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

from sync.business_mapper import (  # noqa: E402
    map_fund_flow_records,
    map_order_records,
    map_refund_records,
    upsert_business_records,
    upsert_fund_flow_records,
    upsert_order_records,
    upsert_refund_records,
)
from sync.mock_wechat import run_mock_sync  # noqa: E402
from warehouse.repository import initialize_database  # noqa: E402


SHOP_ID = "business-mapper-shop-001"
SHOP_NAME = "Business Mapper Test Shop"
TASK_ID = "business-mapper-sync-run-001"
SENSITIVE_TERMS = ("access_token", "authorization", "cookie", "secret", "session", "token")
SENSITIVE_SENTINELS = (
    "order-access-token-sentinel-must-not-persist-25a8",
    "order-authorization-sentinel-must-not-persist-55df",
    "item-cookie-sentinel-must-not-persist-14b2",
    "refund-secret-sentinel-must-not-persist-64bd",
    "fund-session-sentinel-must-not-persist-769e",
)


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_maps_api_business_records_into_standard_tables,
        test_upsert_business_records_routes_by_table_hint,
        test_mock_sync_writes_business_tables_after_raw_response,
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
        print(f"{failed} API business mapper regression test(s) failed.")
        return 1

    print(f"{len(tests)} API business mapper regression tests passed.")
    return 0


def test_maps_api_business_records_into_standard_tables() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_business_mapper_regression_") as temp_dir:
        db_path = Path(temp_dir) / "warehouse.sqlite"
        with connect(db_path) as conn:
            initialize_database(conn)

            order_counts = upsert_order_records(
                conn,
                order_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-orders.json",
                source_sheet="/mock/orders",
            )
            assert order_counts == {"orders": 2, "order_items": 3}

            refund_counts = upsert_refund_records(
                conn,
                refund_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-refunds.json",
                source_sheet="/mock/refunds",
            )
            assert refund_counts == {"refunds": 2}

            fund_counts = upsert_fund_flow_records(
                conn,
                fund_flow_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-funds.json",
                source_sheet="/mock/funds",
            )
            assert fund_counts == {"fund_flows": 2}

            assert_table_count(conn, "orders", 2)
            assert_table_count(conn, "order_items", 3)
            assert_table_count(conn, "refunds", 2)
            assert_table_count(conn, "fund_flows", 2)
            assert_order_fields(conn)
            assert_order_item_fields(conn)
            assert_refund_fields(conn)
            assert_fund_flow_fields(conn)
            assert_synthetic_ids_are_stable(conn)
            assert_no_sensitive_terms_in_raw_json(conn)
            first_snapshot = snapshot_business_keys(conn)

            assert upsert_order_records(
                conn,
                order_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-orders.json",
                source_sheet="/mock/orders",
            ) == {"orders": 2, "order_items": 3}
            assert upsert_refund_records(
                conn,
                refund_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-refunds.json",
                source_sheet="/mock/refunds",
            ) == {"refunds": 2}
            assert upsert_fund_flow_records(
                conn,
                fund_flow_records(),
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
                source_file="/tmp/mock-funds.json",
                source_sheet="/mock/funds",
            ) == {"fund_flows": 2}

            assert_table_count(conn, "orders", 2)
            assert_table_count(conn, "order_items", 3)
            assert_table_count(conn, "refunds", 2)
            assert_table_count(conn, "fund_flows", 2)
            assert snapshot_business_keys(conn) == first_snapshot
            assert_no_sensitive_terms_in_raw_json(conn)

    order_mapping = map_order_records(order_records(), shop_id=SHOP_ID, shop_name=SHOP_NAME, task_id=TASK_ID)
    assert len(order_mapping.orders) == 2
    assert len(order_mapping.order_items) == 3
    assert len(map_refund_records(refund_records(), shop_id=SHOP_ID, shop_name=SHOP_NAME, task_id=TASK_ID)) == 2
    assert len(map_fund_flow_records(fund_flow_records(), shop_id=SHOP_ID, shop_name=SHOP_NAME, task_id=TASK_ID)) == 2


def test_upsert_business_records_routes_by_table_hint() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_business_router_regression_") as temp_dir:
        db_path = Path(temp_dir) / "warehouse.sqlite"
        with connect(db_path) as conn:
            initialize_database(conn)
            assert upsert_business_records(
                conn,
                "orders",
                order_records()[:1],
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
            ) == {"orders": 1, "order_items": 2}
            assert upsert_business_records(
                conn,
                "refunds",
                refund_records()[:1],
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
            ) == {"refunds": 1}
            assert upsert_business_records(
                conn,
                "fund_flows",
                fund_flow_records()[:1],
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
            ) == {"fund_flows": 1}
            assert upsert_business_records(
                conn,
                "products",
                [],
                shop_id=SHOP_ID,
                shop_name=SHOP_NAME,
                task_id=TASK_ID,
            ) == {}


def test_mock_sync_writes_business_tables_after_raw_response() -> None:
    with tempfile.TemporaryDirectory(prefix="wechat_business_mock_sync_regression_") as temp_dir:
        temp_path = Path(temp_dir)
        db_path = temp_path / "warehouse.sqlite"
        archive_dir = temp_path / "raw-api"

        result = run_mock_sync(
            db_path=db_path,
            archive_dir=archive_dir,
            shop_id=SHOP_ID,
            shop_name=SHOP_NAME,
            date_from="2026-06-01",
            date_to="2026-06-07",
            sync_run_id=TASK_ID,
            endpoints=("orders", "aftersale", "funds"),
        )
        assert result["status"] == "completed"

        with connect(db_path) as conn:
            assert_table_count(conn, "raw_api_responses", 3)
            assert_table_count(conn, "orders", 2)
            assert_table_count(conn, "order_items", 2)
            assert_table_count(conn, "refunds", 1)
            assert_table_count(conn, "fund_flows", 1)

            order = fetch_one(conn, "SELECT * FROM orders WHERE order_id = ?", ("mock-o001",))
            assert order["shop_id"] == SHOP_ID
            assert order["shop_name_snapshot"] == SHOP_NAME
            assert order["task_id"] == TASK_ID
            assert order["payment_amount"] == 199.0
            assert order["source_sheet"] == "/channels-shop-order/getorderlist"

            item = fetch_one(conn, "SELECT * FROM order_items WHERE order_item_id = ?", ("mock-oi001",))
            assert item["order_id"] == "mock-o001"
            assert item["product_id"] == "mock-p001"
            assert item["sku_id"] == "mock-sku-p001-red-m"
            assert item["item_amount"] == 199.0

            refund = fetch_one(conn, "SELECT * FROM refunds WHERE refund_id = ?", ("mock-r001",))
            assert refund["order_id"] == "mock-o001"
            assert refund["refund_amount"] == 20.0

            flow = fetch_one(conn, "SELECT * FROM fund_flows WHERE flow_id = ?", ("mock-f001",))
            assert flow["order_id"] == "mock-o001"
            assert flow["amount"] == 199.0


def order_records() -> list[dict[str, Any]]:
    return [
        {
            "orderId": "order-001",
            "openid": "buyer-openid-001",
            "order_status": "paid",
            "create_time": "2026-06-01 10:00:00",
            "pay_time": "2026-06-01 10:01:00",
            "pay_amount": "120.50",
            "freight": "6.00",
            "discount": "5.50",
            "refund_amount": "12.50",
            "currency": "CNY",
            "access_token": SENSITIVE_SENTINELS[0],
            "headers": {"Authorization": SENSITIVE_SENTINELS[1]},
            "items": [
                {
                    "orderItemId": "item-001-a",
                    "productId": "prod-001",
                    "title": "Alias Rain Jacket",
                    "skuId": "sku-001-red",
                    "spec": "Red / M",
                    "qty": "1",
                    "pay_amount": "80.50",
                    "refund_amount": "12.50",
                    "cookie": SENSITIVE_SENTINELS[2],
                },
                {
                    "sub_order_id": "item-001-b",
                    "spu_id": "prod-002",
                    "goods_name": "Alias Hat",
                    "sku_id": "sku-002-one",
                    "sku_name": "One Size",
                    "num": "2",
                    "amount": "40.00",
                },
            ],
        },
        {
            "id": "order-002",
            "buyerOpenid": "buyer-openid-002",
            "status": "finished",
            "order_time": "2026-06-02 11:00:00",
            "paid_at": "2026-06-02 11:02:00",
            "total_amount": 58,
            "shipping_amount": 0,
            "goods": {
                "list": [
                    {
                        "item_id": "item-002-a",
                        "goods_id": "prod-003",
                        "goods_name": "Nested Goods Bag",
                        "count": 1,
                        "price": 58,
                    }
                ]
            },
        },
    ]


def refund_records() -> list[dict[str, Any]]:
    return [
        {
            "aftersale_id": "refund-001",
            "order_id": "order-001",
            "goods_id": "prod-001",
            "goods_name": "Alias Rain Jacket",
            "sku_id": "sku-001-red",
            "status": "completed",
            "amount": "12.50",
            "create_time": "2026-06-03 09:00:00",
            "complete_time": "2026-06-03 12:00:00",
            "reason": "尺码不合适",
            "secret": SENSITIVE_SENTINELS[3],
        },
        {
            "order_id": "order-002",
            "product_id": "prod-003",
            "product_name": "Nested Goods Bag",
            "refund_status": "processing",
            "refund_amount": 5,
            "refund_created_at": "2026-06-04 09:00:00",
        },
    ]


def fund_flow_records() -> list[dict[str, Any]]:
    return [
        {
            "flowId": "flow-001",
            "transaction_time": "2026-06-02 12:00:00",
            "type": "income",
            "biz_type": "order",
            "order_id": "order-001",
            "amount": "120.50",
            "currency": "CNY",
            "direction": "in",
            "balance": "1000.25",
            "remark": "订单入账",
            "session": SENSITIVE_SENTINELS[4],
        },
        {
            "create_time": "2026-06-03 12:30:00",
            "flow_type": "refund",
            "biz_type": "aftersale",
            "order_id": "order-001",
            "amount": "-12.50",
            "direction": "out",
            "balance": 987.75,
        },
    ]


def assert_order_fields(conn: sqlite3.Connection) -> None:
    order = fetch_one(conn, "SELECT * FROM orders WHERE order_id = ?", ("order-001",))
    assert order["shop_id"] == SHOP_ID
    assert order["shop_name_snapshot"] == SHOP_NAME
    assert order["task_id"] == TASK_ID
    assert order["buyer_id"] == "buyer-openid-001"
    assert order["status"] == "paid"
    assert order["order_created_at"] == "2026-06-01 10:00:00"
    assert order["paid_at"] == "2026-06-01 10:01:00"
    assert order["payment_amount"] == 120.5
    assert order["shipping_amount"] == 6.0
    assert order["discount_amount"] == 5.5
    assert order["refund_amount"] == 12.5
    assert order["currency"] == "CNY"
    assert order["source_file"] == "/tmp/mock-orders.json"
    assert order["source_sheet"] == "/mock/orders"

    raw_json = json.loads(order["raw_json"])
    assert raw_json["orderId"] == "order-001"
    assert "access_token" not in raw_json
    assert "Authorization" not in raw_json["headers"]

    second = fetch_one(conn, "SELECT * FROM orders WHERE order_id = ?", ("order-002",))
    assert second["buyer_id"] == "buyer-openid-002"
    assert second["payment_amount"] == 58.0
    assert second["shipping_amount"] == 0.0
    assert second["currency"] == "CNY"


def assert_order_item_fields(conn: sqlite3.Connection) -> None:
    item = fetch_one(conn, "SELECT * FROM order_items WHERE order_item_id = ?", ("item-001-a",))
    assert item["shop_id"] == SHOP_ID
    assert item["order_id"] == "order-001"
    assert item["product_id"] == "prod-001"
    assert item["product_name"] == "Alias Rain Jacket"
    assert item["sku_id"] == "sku-001-red"
    assert item["sku_name"] == "Red / M"
    assert item["quantity"] == 1.0
    assert item["item_amount"] == 80.5
    assert item["refund_amount"] == 12.5

    nested = fetch_one(conn, "SELECT * FROM order_items WHERE order_item_id = ?", ("item-002-a",))
    assert nested["order_id"] == "order-002"
    assert nested["product_id"] == "prod-003"
    assert nested["product_name"] == "Nested Goods Bag"
    assert nested["quantity"] == 1.0
    assert nested["item_amount"] == 58.0

    raw_json = json.loads(item["raw_json"])
    assert raw_json["orderItemId"] == "item-001-a"
    assert "cookie" not in raw_json


def assert_refund_fields(conn: sqlite3.Connection) -> None:
    refund = fetch_one(conn, "SELECT * FROM refunds WHERE refund_id = ?", ("refund-001",))
    assert refund["shop_id"] == SHOP_ID
    assert refund["order_id"] == "order-001"
    assert refund["product_id"] == "prod-001"
    assert refund["product_name"] == "Alias Rain Jacket"
    assert refund["sku_id"] == "sku-001-red"
    assert refund["refund_status"] == "completed"
    assert refund["refund_amount"] == 12.5
    assert refund["refund_created_at"] == "2026-06-03 09:00:00"
    assert refund["refund_completed_at"] == "2026-06-03 12:00:00"
    assert refund["reason"] == "尺码不合适"

    raw_json = json.loads(refund["raw_json"])
    assert raw_json["aftersale_id"] == "refund-001"
    assert "secret" not in raw_json

    second = fetch_one(conn, "SELECT * FROM refunds WHERE order_id = ? AND product_id = ?", ("order-002", "prod-003"))
    assert second["order_id"] == "order-002"
    assert second["refund_status"] == "processing"
    assert second["refund_amount"] == 5.0
    assert second["refund_id"]
    assert second["refund_id"] != "refund-002"


def assert_fund_flow_fields(conn: sqlite3.Connection) -> None:
    flow = fetch_one(conn, "SELECT * FROM fund_flows WHERE flow_id = ?", ("flow-001",))
    assert flow["shop_id"] == SHOP_ID
    assert flow["flow_date"] == "2026-06-02 12:00:00"
    assert flow["flow_type"] == "income"
    assert flow["biz_type"] == "order"
    assert flow["order_id"] == "order-001"
    assert flow["amount"] == 120.5
    assert flow["currency"] == "CNY"
    assert flow["direction"] == "in"
    assert flow["balance"] == 1000.25
    assert flow["remark"] == "订单入账"

    raw_json = json.loads(flow["raw_json"])
    assert raw_json["flowId"] == "flow-001"
    assert "session" not in raw_json

    second = fetch_one(conn, "SELECT * FROM fund_flows WHERE order_id = ? AND flow_type = ?", ("order-001", "refund"))
    assert second["flow_type"] == "refund"
    assert second["amount"] == 12.5
    assert second["currency"] == "CNY"
    assert second["direction"] == "out"
    assert second["flow_id"]
    assert second["flow_id"] != "flow-002"


def assert_synthetic_ids_are_stable(conn: sqlite3.Connection) -> None:
    refund = fetch_one(conn, "SELECT refund_id, row_fingerprint FROM refunds WHERE order_id = ? AND product_id = ?", ("order-002", "prod-003"))
    fund_flow = fetch_one(conn, "SELECT flow_id, row_fingerprint FROM fund_flows WHERE order_id = ? AND flow_type = ?", ("order-001", "refund"))
    assert refund["refund_id"]
    assert refund["row_fingerprint"]
    assert fund_flow["flow_id"]
    assert fund_flow["row_fingerprint"]


def assert_no_sensitive_terms_in_raw_json(conn: sqlite3.Connection) -> None:
    for table in ("orders", "order_items", "refunds", "fund_flows"):
        rows = conn.execute(f"SELECT raw_json FROM {table}").fetchall()
        for row in rows:
            raw_json = str(row["raw_json"] or "")
            lowered = raw_json.lower()
            for sentinel in SENSITIVE_SENTINELS:
                assert sentinel not in raw_json, f"sensitive sentinel persisted in {table}: {sentinel}"
            for term in SENSITIVE_TERMS:
                assert term not in lowered, f"sensitive key persisted in {table} raw_json: {term}"


def snapshot_business_keys(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        "orders": select_identity(conn, "orders", "shop_id, order_id, row_fingerprint"),
        "order_items": select_identity(conn, "order_items", "shop_id, order_item_id, order_id, row_fingerprint"),
        "refunds": select_identity(conn, "refunds", "shop_id, refund_id, order_id, row_fingerprint"),
        "fund_flows": select_identity(conn, "fund_flows", "shop_id, flow_id, row_fingerprint"),
    }


def select_identity(conn: sqlite3.Connection, table: str, columns: str) -> list[dict[str, Any]]:
    rows = [dict(row) for row in conn.execute(f"SELECT {columns} FROM {table} ORDER BY row_fingerprint").fetchall()]
    fingerprints = [row["row_fingerprint"] for row in rows]
    assert len(fingerprints) == len(set(fingerprints)), f"duplicate row_fingerprint in {table}"
    return rows


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
