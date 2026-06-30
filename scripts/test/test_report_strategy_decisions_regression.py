#!/usr/bin/env python3
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable, Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from reporting.render import _render_markdown  # noqa: E402


SENSITIVE_SENTINELS = (
    "ORDER202606280001",
    "buyer-openid-sensitive",
    "13800138000",
)


def main() -> int:
    tests: tuple[Callable[[], None], ...] = (
        test_structured_decisions_include_actions_and_redact_warning_details,
        test_insufficient_data_says_no_decision,
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
        print(f"{failed} report strategy decision regression test(s) failed.")
        return 1

    print(f"{len(tests)} report strategy decision regression tests passed.")
    return 0


def test_structured_decisions_include_actions_and_redact_warning_details() -> None:
    report = render(marketing_metrics(), warnings_with_sensitive_fields())

    assert "## 初步投放建议" in report
    assert "## 结构化经营决策" in report
    assert "### 决策边界" in report
    assert "### 加推商品" in report
    assert "### 暂停/优化商品" in report
    assert "### 退款/售后风险" in report
    assert "### 价格/SKU/详情页/投放建议" in report
    assert "爆款杯 / 红色套装" in report
    assert "建议加推" in report
    assert "问题包 / 标准款" in report
    assert "暂停放量并先优化" in report
    assert "退款/售后风险" in report
    assert "价格：当前客单价为 80.00" in report
    assert "SKU：优先核查" in report
    assert "详情页/素材：商品点击率为 1.50%" in report
    assert "投放：预算优先给低退款高贡献商品" in report
    assert "数据质量 warning：`missing_order_created_at`、`unknown_order_status`、`invalid_order_created_at`" in report
    assert "`missing_order_created_at`：详见数据质量记录" in report
    assert "`invalid_order_created_at`：order_id=[REDACTED] mobile=[REDACTED]" in report

    for sentinel in SENSITIVE_SENTINELS:
        assert sentinel not in report, f"sensitive sentinel leaked in report: {sentinel}"


def test_insufficient_data_says_no_decision() -> None:
    report = render(insufficient_metrics(), [])

    assert "## 结构化经营决策" in report
    assert "数据不足：缺少商品明细，不能判断哪些商品适合加推，不能下加推决策。" in report
    assert "数据不足：缺少商品明细，不能判断哪些商品需要暂停或优化，不能下暂停/优化决策。" in report
    assert "数据不足：订单、店铺日退款指标和商品明细都不足，不能判断退款/售后风险。" in report
    assert "价格：缺少有效客单价和价格/毛利指标，不能下价格调整决策。" in report
    assert "SKU：缺少商品明细，不能做 SKU 级决策。" in report
    assert "详情页：缺少店铺日曝光、点击和成交指标，不能判断详情页或素材承接问题。" in report
    assert "投放：缺少商品明细，不能指定投放商品。" in report
    assert "人群：缺少可用主力或潜力客群，不能下人群定向决策。" in report


def render(metrics: dict[str, Any], warnings: list[dict[str, Any]]) -> str:
    return _render_markdown(
        {
            "shop_id": "decision-shop",
            "id": "ar_decision_regression",
            "created_at": "2026-06-28T10:00:00+08:00",
        },
        metrics,
        warnings,
    )


def marketing_metrics() -> dict[str, Any]:
    return {
        "date_from": "2026-06-01",
        "date_to": "2026-06-07",
        "order_metrics": {
            "total_order_count": 18,
            "effective_order_count": 10,
            "refund_order_count": 2,
            "refund_order_rate": 0.2,
            "gmv": 900.0,
            "refund_amount": 100.0,
            "net_sales_amount": 800.0,
            "average_order_value": 80.0,
        },
        "shop_daily_metrics": {
            "row_count": 7,
            "date_count": 7,
            "date_from": "2026-06-01",
            "date_to": "2026-06-07",
            "exposure_user_count": 2000,
            "click_user_count": 30,
            "click_count": 40,
            "order_amount": 900.0,
            "order_submit_count": 12,
            "order_user_count": 10,
            "order_count": 10,
            "buyer_count": 10,
            "sold_quantity": 14,
            "payment_amount": 900.0,
            "refund_amount": 100.0,
            "net_payment_amount": 800.0,
            "click_through_rate": 0.015,
            "click_conversion_rate": 0.25,
            "refund_rate": 0.11,
            "average_daily_payment_amount": 128.57,
        },
        "product_metrics": [
            {
                "product_id": "prod-add",
                "product_name": "爆款杯",
                "sku_id": "sku-red",
                "sku_name": "红色套装",
                "order_count": 6,
                "refund_order_count": 0,
                "refund_rate": 0,
                "gross_amount": 520.0,
                "refund_amount": 0.0,
                "net_amount": 520.0,
                "contribution_rate": 0.65,
            },
            {
                "product_id": "prod-risk",
                "product_name": "问题包",
                "sku_id": "sku-basic",
                "sku_name": "标准款",
                "order_count": 4,
                "refund_order_count": 2,
                "refund_rate": 0.5,
                "gross_amount": 300.0,
                "refund_amount": 100.0,
                "net_amount": 200.0,
                "contribution_rate": 0.25,
            },
        ],
        "audience_metrics": {
            "segment_count": 2,
            "primary_segments": [
                {
                    "product_name": "爆款杯",
                    "dimension": "region",
                    "segment_label": "华东",
                    "visitor_count": 500,
                    "order_count": 8,
                    "payment_amount": 520.0,
                    "conversion_rate": 0.08,
                    "refund_rate": 0.0,
                }
            ],
            "potential_segments": [],
            "risk_segments": [
                {
                    "product_name": "问题包",
                    "dimension": "age",
                    "segment_label": "18-24",
                    "visitor_count": 100,
                    "order_count": 3,
                    "payment_amount": 200.0,
                    "conversion_rate": 0.03,
                    "refund_rate": 0.25,
                }
            ],
            "dimensions": ["age", "region"],
        },
    }


def insufficient_metrics() -> dict[str, Any]:
    return {
        "date_from": "2026-06-01",
        "date_to": "2026-06-07",
        "order_metrics": {
            "total_order_count": 0,
            "effective_order_count": 0,
            "refund_order_count": 0,
            "refund_order_rate": 0,
            "gmv": 0,
            "refund_amount": 0,
            "net_sales_amount": 0,
            "average_order_value": 0,
        },
        "shop_daily_metrics": {"row_count": 0, "date_count": 0},
        "product_metrics": [],
        "audience_metrics": {
            "segment_count": 0,
            "primary_segments": [],
            "potential_segments": [],
            "risk_segments": [],
            "dimensions": [],
        },
    }


def warnings_with_sensitive_fields() -> list[dict[str, Any]]:
    return [
        {
            "code": "missing_order_created_at",
            "order_id": SENSITIVE_SENTINELS[0],
        },
        {
            "code": "unknown_order_status",
            "buyer_id": SENSITIVE_SENTINELS[1],
            "phone": SENSITIVE_SENTINELS[2],
        },
        {
            "code": "invalid_order_created_at",
            "message": f"order_id={SENSITIVE_SENTINELS[0]} mobile={SENSITIVE_SENTINELS[2]}",
        },
    ]


if __name__ == "__main__":
    raise SystemExit(main())
