#!/usr/bin/env python3
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from ai_analysis.payloads import build_multi_shop_payload, build_shop_payload  # noqa: E402


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_ai_payload_keeps_paid_order_count_and_paid_order_aov,
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
        print(f"{failed} AI payload metrics regression test(s) failed.")
        return 1

    print(f"{len(tests)} AI payload metrics regression tests passed.")
    return 0


def test_ai_payload_keeps_paid_order_count_and_paid_order_aov() -> None:
    metrics = shop_metrics("shop-001", paid_order_count=4, effective_order_count=1, gmv=156.6, net_sales_amount=89.8)

    shop_payload = build_shop_payload(metrics, analysis_run_id="ar_payload_regression")
    multi_payload = build_multi_shop_payload([metrics], analysis_run_ids=["ar_payload_regression"])

    assert shop_payload["metrics"]["orders"]["paid_order_count"] == 4
    assert shop_payload["metrics"]["orders"]["average_order_value"] == 39.15
    assert multi_payload["comparison"]["totals"]["paid_order_count"] == 4
    assert multi_payload["comparison"]["totals"]["effective_order_count"] == 1
    assert multi_payload["comparison"]["totals"]["average_order_value"] == 39.15
    assert multi_payload["comparison"]["rankings"]["by_average_order_value"][0]["paid_order_count"] == 4


def shop_metrics(
    shop_id: str,
    *,
    paid_order_count: int,
    effective_order_count: int,
    gmv: float,
    net_sales_amount: float,
) -> dict[str, object]:
    return {
        "shop_id": shop_id,
        "date_from": "2026-06-01",
        "date_to": "2026-06-03",
        "order_metrics": {
            "total_order_count": paid_order_count,
            "paid_order_count": paid_order_count,
            "effective_order_count": effective_order_count,
            "refund_order_count": 2,
            "refund_order_rate": 0.5,
            "gmv": gmv,
            "refund_amount": round(gmv - net_sales_amount, 2),
            "net_sales_amount": net_sales_amount,
            "average_order_value": round(gmv / paid_order_count, 2),
        },
        "shop_daily_metrics": {},
        "product_metrics": [],
        "audience_metrics": {},
        "metadata": {},
    }


if __name__ == "__main__":
    raise SystemExit(main())
