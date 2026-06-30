#!/usr/bin/env python3
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from analytics.metrics import calculate_metrics  # noqa: E402
from shared.ids import fingerprint  # noqa: E402
from warehouse.repository import connect, ensure_shop, initialize_database, upsert_records  # noqa: E402


SHOP_ID = "metrics-shop"


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_refund_rate_uses_paid_orders_not_net_effective_orders,
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
        print(f"{failed} analytics metrics regression test(s) failed.")
        return 1

    print(f"{len(tests)} analytics metrics regression tests passed.")
    return 0


def test_refund_rate_uses_paid_orders_not_net_effective_orders() -> None:
    with connect(":memory:") as conn:
        initialize_database(conn)
        ensure_shop(conn, SHOP_ID, "Metrics Shop")
        upsert_records(
            conn,
            "orders",
            [
                order("order-001", "已完成", "2026-06-01 10:00:00", 100.0, 0.0),
                order("order-002", "已完成", "2026-06-01 11:00:00", 50.0, 50.0),
                order("order-003", "已发货", "2026-06-02 12:00:00", 20.0, 10.0),
                order("order-004", "待发货", "2026-06-02 13:00:00", 30.0, 0.0),
            ],
        )
        upsert_records(
            conn,
            "order_items",
            [
                order_item("item-001", "order-001", "product-001", 100.0, 0.0),
                order_item("item-002", "order-002", "product-002", 50.0, 0.0),
                order_item("item-003", "order-003", "product-003", 20.0, 0.0),
                order_item("item-004", "order-004", "product-004", 30.0, 0.0),
            ],
        )
        upsert_records(
            conn,
            "refunds",
            [
                refund("refund-002", "order-002", "product-002", 50.0),
                refund("refund-003", "order-003", "product-003", 10.0),
            ],
        )

        metrics, warnings = calculate_metrics(
            conn,
            shop_id=SHOP_ID,
            date_from="2026-06-01",
            date_to="2026-06-03",
        )

    order_metrics = metrics["order_metrics"]
    assert warnings == []
    assert order_metrics["total_order_count"] == 4
    assert order_metrics["paid_order_count"] == 4
    assert order_metrics["effective_order_count"] == 3
    assert order_metrics["refund_order_count"] == 2
    assert order_metrics["refund_order_rate"] == 0.5
    assert order_metrics["gmv"] == 200.0
    assert order_metrics["refund_amount"] == 60.0
    assert order_metrics["net_sales_amount"] == 140.0
    assert order_metrics["average_order_value"] == 50.0


def order(
    order_id: str,
    status: str,
    order_created_at: str,
    payment_amount: float,
    refund_amount: float,
) -> dict[str, object]:
    return {
        "shop_id": SHOP_ID,
        "shop_name_snapshot": "Metrics Shop",
        "task_id": "metrics-regression",
        "order_id": order_id,
        "status": status,
        "order_created_at": order_created_at,
        "payment_amount": payment_amount,
        "refund_amount": refund_amount,
        "currency": "CNY",
        "row_fingerprint": fingerprint("orders", SHOP_ID, order_id),
    }


def order_item(
    order_item_id: str,
    order_id: str,
    product_id: str,
    item_amount: float,
    refund_amount: float,
) -> dict[str, object]:
    return {
        "shop_id": SHOP_ID,
        "shop_name_snapshot": "Metrics Shop",
        "task_id": "metrics-regression",
        "order_item_id": order_item_id,
        "order_id": order_id,
        "product_id": product_id,
        "product_name": product_id,
        "quantity": 1,
        "item_amount": item_amount,
        "refund_amount": refund_amount,
        "row_fingerprint": fingerprint("order_items", SHOP_ID, order_item_id),
    }


def refund(
    refund_id: str,
    order_id: str,
    product_id: str,
    refund_amount: float,
) -> dict[str, object]:
    return {
        "shop_id": SHOP_ID,
        "shop_name_snapshot": "Metrics Shop",
        "task_id": "metrics-regression",
        "refund_id": refund_id,
        "order_id": order_id,
        "product_id": product_id,
        "refund_status": "退款成功",
        "refund_amount": refund_amount,
        "row_fingerprint": fingerprint("refunds", SHOP_ID, refund_id),
    }


if __name__ == "__main__":
    raise SystemExit(main())
