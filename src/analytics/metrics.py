from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from shared.parsing import safe_ratio, to_float


INVALID_STATUS_TOKENS = ("取消", "关闭", "退款成功", "交易关闭", "cancel", "closed")
PAID_STATUS_TOKENS = ("已付款", "已支付", "已完成", "已发货", "待发货", "paid", "complete", "finished", "shipped")


def calculate_metrics(
    conn: Any,
    *,
    shop_id: str,
    date_from: str | None,
    date_to: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    orders = _fetch_orders(conn, shop_id)
    order_items = _fetch_order_items(conn, shop_id)
    products = _fetch_products(conn, shop_id)
    refunds = _fetch_refunds(conn, shop_id)
    audience_insights = _fetch_audience_insights(conn, shop_id)
    shop_daily = _fetch_shop_daily(conn, shop_id)

    filtered_orders = [
        order
        for order in orders
        if _is_order_in_range(order, date_from, date_to, warnings)
    ]
    order_by_id = {
        str(order.get("order_id")): order
        for order in filtered_orders
        if order.get("order_id")
    }
    valid_orders = [
        order
        for order in filtered_orders
        if _is_effective_order(order, refunds, warnings)
    ]
    valid_order_ids = {
        str(order.get("order_id"))
        for order in valid_orders
        if order.get("order_id")
    }
    paid_orders = [
        order
        for order in filtered_orders
        if _is_paid_order(order)
    ]

    refunds_by_order = _refunds_by_order(refunds)
    refund_order_ids = {
        order_id
        for order_id, order in order_by_id.items()
        if _order_refund_amount(order, refunds_by_order) > 0
    }

    total_order_count = len(filtered_orders)
    paid_order_count = len(paid_orders)
    effective_order_count = len(valid_orders)
    refund_order_count = len(refund_order_ids)
    gmv = sum(_money(order.get("payment_amount")) for order in filtered_orders)
    refund_amount = sum(
        _order_refund_amount(order, refunds_by_order)
        for order in filtered_orders
    )
    net_sales_amount = max(gmv - refund_amount, 0.0)
    refund_order_rate = safe_ratio(refund_order_count, paid_order_count)
    average_order_value = safe_ratio(gmv, paid_order_count)

    filtered_shop_daily = [
        row
        for row in shop_daily
        if _is_shop_daily_in_range(row, date_from, date_to, warnings)
    ]
    shop_daily_metrics = _calculate_shop_daily_metrics(filtered_shop_daily)
    product_metrics = _calculate_product_metrics(
        order_items=order_items,
        products=products,
        refunds=refunds,
        valid_order_ids=valid_order_ids,
        store_net_sales=net_sales_amount,
        warnings=warnings,
    )
    audience_metrics = _calculate_audience_metrics(audience_insights)
    if not order_items:
        warnings.append(
            {
                "code": "missing_order_items",
                "message": "缺少订单明细数据，无法计算商品级退款率和贡献度",
            }
        )
    if filtered_orders and any(order.get("status") in (None, "") for order in filtered_orders):
        warnings.append(
            {
                "code": "missing_order_status",
                "message": "部分订单缺少状态，已按支付金额和退款金额估算有效成交订单",
            }
        )
    if filtered_orders and any(order.get("payment_amount") is None for order in filtered_orders):
        warnings.append(
            {
                "code": "missing_payment_amount",
                "message": "部分订单缺少支付金额，金额指标按 0 处理",
            }
        )
    data_coverage = _data_coverage_summary(
        filtered_orders=filtered_orders,
        order_items=order_items,
        refunds=refunds,
        shop_daily_metrics=shop_daily_metrics,
        warnings=warnings,
    )
    decision_metrics = _calculate_decision_metrics(
        order_metrics={
            "total_order_count": total_order_count,
            "paid_order_count": paid_order_count,
            "effective_order_count": effective_order_count,
            "refund_order_count": refund_order_count,
            "refund_order_rate": refund_order_rate,
            "gmv": round(gmv, 2),
            "refund_amount": round(refund_amount, 2),
            "net_sales_amount": round(net_sales_amount, 2),
            "average_order_value": round(average_order_value, 2),
        },
        shop_daily_metrics=shop_daily_metrics,
        product_metrics=product_metrics,
        audience_metrics=audience_metrics,
        data_coverage=data_coverage,
    )

    metrics = {
        "shop_id": shop_id,
        "date_from": date_from,
        "date_to": date_to,
        "order_metrics": {
            "total_order_count": total_order_count,
            "paid_order_count": paid_order_count,
            "effective_order_count": effective_order_count,
            "refund_order_count": refund_order_count,
            "refund_order_rate": refund_order_rate,
            "gmv": round(gmv, 2),
            "refund_amount": round(refund_amount, 2),
            "net_sales_amount": round(net_sales_amount, 2),
            "average_order_value": round(average_order_value, 2),
        },
        "shop_daily_metrics": shop_daily_metrics,
        "overview_daily_metrics": dict(shop_daily_metrics),
        "product_metrics": product_metrics,
        "audience_metrics": audience_metrics,
        "data_coverage": data_coverage,
        "decision_metrics": decision_metrics,
        "metadata": {
            "orders_scanned": len(orders),
            "orders_in_period": len(filtered_orders),
            "order_items_scanned": len(order_items),
            "products_scanned": len(products),
            "refunds_scanned": len(refunds),
            "audience_insights_scanned": len(audience_insights),
            "shop_daily_scanned": len(shop_daily),
            "shop_daily_in_period": len(filtered_shop_daily),
            "refund_attribution": "refunds are attributed to orders whose order_created_at falls in the analysis window",
        },
    }
    return metrics, warnings


def _data_coverage_summary(
    *,
    filtered_orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    refunds: list[dict[str, Any]],
    shop_daily_metrics: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    detail_order_count = len(filtered_orders)
    item_count = len(order_items)
    refund_count = len(refunds)
    overview_order_count = float(shop_daily_metrics.get("order_count") or 0)
    overview_refund_amount = float(shop_daily_metrics.get("refund_amount") or 0)
    detail_refund_amount = sum(_money(order.get("refund_amount")) for order in filtered_orders)
    coverage_status = "full_or_unknown"
    decision_level = "shop_and_product"
    reasons: list[str] = []

    if detail_order_count == 0 and overview_order_count > 0:
        coverage_status = "missing_order_detail"
        decision_level = "shop_summary_only"
        reasons.append("店铺汇总有成交订单，但订单明细未导入")
        warnings.append(
            {
                "code": "missing_order_detail_with_shop_daily",
                "message": "店铺日概览存在成交订单，但订单明细为空，订单核心指标不能按 0 解读",
            }
        )
    elif overview_order_count > 0 and detail_order_count < overview_order_count:
        coverage_status = "partial_order_detail"
        decision_level = "shop_summary_only" if item_count == 0 else "limited_product"
        reasons.append("订单明细数量少于店铺汇总成交订单数")
        warnings.append(
            {
                "code": "partial_order_detail_coverage",
                "detail_order_count": detail_order_count,
                "overview_order_count": overview_order_count,
                "message": "订单明细数量少于店铺汇总成交订单数，当前订单分析可能只是样本",
            }
        )
    elif detail_order_count > 0 and item_count == 0:
        coverage_status = "orders_without_items"
        decision_level = "order_only"
        reasons.append("已有订单表，但缺少订单商品明细")
    elif detail_order_count > 0:
        coverage_status = "order_detail_available"

    if overview_refund_amount > 0 and detail_refund_amount <= 0 and refund_count == 0:
        reasons.append("店铺汇总有退款金额，但退款表/订单退款字段未覆盖")
        warnings.append(
            {
                "code": "refund_detail_coverage_mismatch",
                "overview_refund_amount": round(overview_refund_amount, 2),
                "detail_refund_amount": round(detail_refund_amount, 2),
                "message": "店铺汇总存在退款金额，但退款明细未导入或订单退款字段为空，退款风险可能被低估",
            }
        )

    if not reasons and detail_order_count == 0 and overview_order_count == 0:
        coverage_status = "no_order_signal"
        decision_level = "insufficient"
        reasons.append("订单明细和店铺汇总都缺少订单信号")

    return {
        "status": coverage_status,
        "decision_level": decision_level,
        "detail_order_count": detail_order_count,
        "order_item_count": item_count,
        "refund_count": refund_count,
        "overview_order_count": overview_order_count,
        "overview_refund_amount": round(overview_refund_amount, 2),
        "detail_refund_amount": round(detail_refund_amount, 2),
        "is_full_order_detail_likely": coverage_status in {"full_or_unknown", "order_detail_available"} and item_count > 0,
        "reasons": reasons,
    }


def _calculate_decision_metrics(
    *,
    order_metrics: dict[str, Any],
    shop_daily_metrics: dict[str, Any],
    product_metrics: list[dict[str, Any]],
    audience_metrics: dict[str, Any],
    data_coverage: dict[str, Any],
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    decision_level = data_coverage.get("decision_level")
    has_product_decision_data = decision_level in {"shop_and_product", "limited_product"} and bool(product_metrics)

    if not has_product_decision_data:
        decisions.append(
            {
                "action": "补齐数据",
                "target": "订单全部导出、订单明细、退款/评价",
                "priority": "high",
                "confidence": "high",
                "basis": "当前数据覆盖不足，不能给具体商品加推/下架结论",
            }
        )

    growth_products = [
        row for row in product_metrics
        if float(row.get("contribution_rate") or 0) >= 0.15 and float(row.get("refund_rate") or 0) < 0.15
    ]
    risk_products = [
        row for row in product_metrics
        if int(row.get("order_count") or 0) >= 2 and float(row.get("refund_rate") or 0) >= 0.2
    ]
    low_net_products = [
        row for row in product_metrics
        if int(row.get("order_count") or 0) >= 2 and float(row.get("net_amount") or 0) <= 0
    ]

    for row in growth_products[:10]:
        decisions.append(
            {
                "action": "加推",
                "target": row.get("product_name") or row.get("product_id") or "未知商品",
                "priority": "medium",
                "confidence": "medium" if data_coverage.get("status") == "partial_order_detail" else "high",
                "basis": f"商品贡献 {_percent_text(row.get('contribution_rate'))}，退款率 {_percent_text(row.get('refund_rate'))}",
            }
        )

    for row in risk_products[:10]:
        decisions.append(
            {
                "action": "暂停放量/优化",
                "target": row.get("product_name") or row.get("product_id") or "未知商品",
                "priority": "high",
                "confidence": "medium",
                "basis": f"成交订单 {row.get('order_count', 0)}，退款率 {_percent_text(row.get('refund_rate'))}",
            }
        )

    for row in low_net_products[:10]:
        decisions.append(
            {
                "action": "复盘利润与售后",
                "target": row.get("product_name") or row.get("product_id") or "未知商品",
                "priority": "high",
                "confidence": "medium",
                "basis": f"商品净成交额 {_money_text(row.get('net_amount'))}",
            }
        )

    shop_refund_rate = float(shop_daily_metrics.get("refund_rate") or order_metrics.get("refund_order_rate") or 0)
    if shop_refund_rate >= 0.2:
        decisions.append(
            {
                "action": "先控退款再投放",
                "target": "全店",
                "priority": "high",
                "confidence": "medium",
                "basis": f"退款率/退款金额率 {_percent_text(shop_refund_rate)}",
            }
        )

    click_through_rate = float(shop_daily_metrics.get("click_through_rate") or 0)
    click_conversion_rate = float(shop_daily_metrics.get("click_conversion_rate") or 0)
    if shop_daily_metrics.get("row_count") and click_through_rate < 0.02:
        decisions.append(
            {
                "action": "优化主图/标题",
                "target": "店铺商品入口",
                "priority": "medium",
                "confidence": "medium",
                "basis": f"商品点击率 {_percent_text(click_through_rate)} 偏低",
            }
        )
    if shop_daily_metrics.get("row_count") and click_through_rate >= 0.02 and click_conversion_rate < 0.03:
        decisions.append(
            {
                "action": "优化详情页/价格/SKU",
                "target": "高点击低成交商品",
                "priority": "medium",
                "confidence": "low" if not product_metrics else "medium",
                "basis": f"点击率 {_percent_text(click_through_rate)}，点击成交率 {_percent_text(click_conversion_rate)}",
            }
        )

    primary_segments = audience_metrics.get("primary_segments", [])
    if primary_segments:
        labels = "、".join(_audience_decision_label(row) for row in primary_segments[:3])
        decisions.append(
            {
                "action": "人群定向测试",
                "target": labels,
                "priority": "medium",
                "confidence": "medium",
                "basis": "人群画像中成交/转化表现靠前",
            }
        )

    return {
        "data_coverage_status": data_coverage.get("status"),
        "decision_level": decision_level,
        "growth_products": growth_products[:10],
        "risk_products": risk_products[:10],
        "decisions": decisions,
    }


def _fetch_orders(conn: Any, shop_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE shop_id = ?",
        (shop_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_order_items(conn: Any, shop_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM order_items WHERE shop_id = ?",
        (shop_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_products(conn: Any, shop_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM products WHERE shop_id = ?",
        (shop_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_refunds(conn: Any, shop_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM refunds WHERE shop_id = ?",
        (shop_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_audience_insights(conn: Any, shop_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM audience_insights WHERE shop_id = ?",
        (shop_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_shop_daily(conn: Any, shop_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM shop_daily WHERE shop_id = ?",
        (shop_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _is_order_in_range(
    order: dict[str, Any],
    date_from: str | None,
    date_to: str | None,
    warnings: list[dict[str, Any]],
) -> bool:
    value = order.get("order_created_at")
    if not date_from and not date_to:
        return True
    if not value:
        warnings.append(
            {
                "code": "missing_order_created_at",
                "order_id": order.get("order_id"),
                "message": "订单缺少创建时间，无法纳入指定周期分析",
            }
        )
        return False
    day = _date_key(value)
    if day is None:
        warnings.append(
            {
                "code": "invalid_order_created_at",
                "order_id": order.get("order_id"),
                "value": value,
                "message": "订单创建时间无法解析，无法纳入指定周期分析",
            }
        )
        return False
    normalized_date_from = _date_key(date_from) if date_from else None
    normalized_date_to = _date_key(date_to) if date_to else None
    if normalized_date_from and day < normalized_date_from:
        return False
    if normalized_date_to and day > normalized_date_to:
        return False
    return True


def _is_shop_daily_in_range(
    row: dict[str, Any],
    date_from: str | None,
    date_to: str | None,
    warnings: list[dict[str, Any]],
) -> bool:
    value = row.get("stat_date")
    if not date_from and not date_to:
        return True
    if not value:
        warnings.append(
            {
                "code": "missing_shop_daily_stat_date",
                "source_row_number": row.get("source_row_number"),
                "message": "店铺日数据缺少统计日期，无法纳入指定周期分析",
            }
        )
        return False
    day = _date_key(value)
    if day is None:
        warnings.append(
            {
                "code": "invalid_shop_daily_stat_date",
                "source_row_number": row.get("source_row_number"),
                "value": value,
                "message": "店铺日数据统计日期无法解析，无法纳入指定周期分析",
            }
        )
        return False
    normalized_date_from = _date_key(date_from) if date_from else None
    normalized_date_to = _date_key(date_to) if date_to else None
    if normalized_date_from and day < normalized_date_from:
        return False
    if normalized_date_to and day > normalized_date_to:
        return False
    return True


def _is_paid_order(order: dict[str, Any]) -> bool:
    return _money(order.get("payment_amount")) > 0


def _is_effective_order(
    order: dict[str, Any],
    refunds: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> bool:
    status = str(order.get("status") or "").lower()
    if any(token.lower() in status for token in INVALID_STATUS_TOKENS):
        return False
    payment_amount = _money(order.get("payment_amount"))
    if payment_amount <= 0:
        return False
    refund_amount = _order_refund_amount(order, _refunds_by_order(refunds))
    if refund_amount >= payment_amount > 0:
        return False
    if not status:
        return True
    if any(token.lower() in status for token in PAID_STATUS_TOKENS):
        return True
    warnings.append(
        {
            "code": "unknown_order_status",
            "order_id": order.get("order_id"),
            "status": order.get("status"),
            "message": "订单状态未命中已知有效/无效状态，已按金额估算为有效订单",
        }
    )
    return True


def _calculate_shop_daily_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_dates = sorted(
        {
            date_key
            for row in rows
            if (date_key := _date_key(row.get("stat_date"))) is not None
        }
    )
    row_count = len(rows)
    date_count = len(valid_dates)
    average_divisor = date_count or row_count

    visitor_count = _sum_numeric(rows, "visitor_count")
    view_count = _sum_numeric(rows, "view_count")
    exposure_user_count = _sum_numeric(rows, "exposure_user_count")
    click_user_count = _sum_numeric(rows, "click_user_count")
    click_count = _sum_numeric(rows, "click_count")
    order_amount = _sum_numeric(rows, "order_amount")
    order_submit_count = _sum_numeric(rows, "order_submit_count")
    order_user_count = _sum_numeric(rows, "order_user_count")
    order_count = _sum_numeric(rows, "order_count")
    buyer_count = _sum_numeric(rows, "buyer_count")
    sold_quantity = _sum_numeric(rows, "sold_quantity")
    payment_amount = _sum_numeric(rows, "payment_amount")
    refund_amount = _sum_numeric(rows, "refund_amount")
    net_payment_amount = max(payment_amount - refund_amount, 0.0)

    imported_conversion_rates = _numeric_values(rows, "conversion_rate")
    imported_click_conversion_rates = _numeric_values(rows, "click_conversion_rate")
    imported_refund_rates = _numeric_values(rows, "refund_rate")
    avg_conversion_rate = _average(imported_conversion_rates)
    avg_click_conversion_rate = _average(imported_click_conversion_rates)
    avg_refund_rate = _average(imported_refund_rates)

    click_through_rate, click_through_rate_source = _shop_daily_click_through_rate(
        click_user_count=click_user_count,
        click_count=click_count,
        exposure_user_count=exposure_user_count,
        has_click_user_count=_has_numeric_value(rows, "click_user_count"),
        has_click_count=_has_numeric_value(rows, "click_count"),
    )
    click_conversion_rate, click_conversion_rate_source = _shop_daily_click_conversion_rate(
        click_count=click_count,
        order_count=order_count,
        has_order_count=_has_numeric_value(rows, "order_count"),
        avg_click_conversion_rate=avg_click_conversion_rate,
    )
    conversion_rate, conversion_rate_source = _shop_daily_conversion_rate(
        visitor_count=visitor_count,
        order_count=order_count,
        buyer_count=buyer_count,
        has_order_count=_has_numeric_value(rows, "order_count"),
        has_buyer_count=_has_numeric_value(rows, "buyer_count"),
        avg_conversion_rate=avg_conversion_rate,
    )
    refund_rate, refund_rate_source = _shop_daily_refund_rate(
        payment_amount=payment_amount,
        refund_amount=refund_amount,
        avg_refund_rate=avg_refund_rate,
        has_imported_refund_rate=bool(imported_refund_rates),
    )

    return {
        "row_count": row_count,
        "date_count": date_count,
        "date_from": valid_dates[0] if valid_dates else None,
        "date_to": valid_dates[-1] if valid_dates else None,
        "visitor_count": round(visitor_count, 2),
        "view_count": round(view_count, 2),
        "exposure_user_count": round(exposure_user_count, 2),
        "click_user_count": round(click_user_count, 2),
        "click_count": round(click_count, 2),
        "order_amount": round(order_amount, 2),
        "order_submit_count": round(order_submit_count, 2),
        "order_user_count": round(order_user_count, 2),
        "order_count": round(order_count, 2),
        "buyer_count": round(buyer_count, 2),
        "sold_quantity": round(sold_quantity, 2),
        "payment_amount": round(payment_amount, 2),
        "refund_amount": round(refund_amount, 2),
        "net_payment_amount": round(net_payment_amount, 2),
        "click_through_rate": round(click_through_rate, 6),
        "click_through_rate_source": click_through_rate_source,
        "click_conversion_rate": round(click_conversion_rate, 6),
        "click_conversion_rate_source": click_conversion_rate_source,
        "avg_click_conversion_rate": round(avg_click_conversion_rate, 6),
        "conversion_rate": round(conversion_rate, 6),
        "conversion_rate_source": conversion_rate_source,
        "avg_conversion_rate": round(avg_conversion_rate, 6),
        "refund_rate": round(refund_rate, 6),
        "refund_rate_source": refund_rate_source,
        "avg_refund_rate": round(avg_refund_rate, 6),
        "order_submit_rate": round(safe_ratio(order_submit_count, click_count), 6),
        "buyer_order_rate": round(safe_ratio(buyer_count, order_count), 6),
        "average_order_value": round(safe_ratio(payment_amount, order_count), 2),
        "average_buyer_value": round(safe_ratio(payment_amount, buyer_count), 2),
        "average_daily_visitor_count": round(safe_ratio(visitor_count, average_divisor), 2),
        "average_daily_view_count": round(safe_ratio(view_count, average_divisor), 2),
        "average_daily_exposure_user_count": round(safe_ratio(exposure_user_count, average_divisor), 2),
        "average_daily_click_user_count": round(safe_ratio(click_user_count, average_divisor), 2),
        "average_daily_click_count": round(safe_ratio(click_count, average_divisor), 2),
        "average_daily_order_amount": round(safe_ratio(order_amount, average_divisor), 2),
        "average_daily_order_submit_count": round(safe_ratio(order_submit_count, average_divisor), 2),
        "average_daily_order_user_count": round(safe_ratio(order_user_count, average_divisor), 2),
        "average_daily_order_count": round(safe_ratio(order_count, average_divisor), 2),
        "average_daily_buyer_count": round(safe_ratio(buyer_count, average_divisor), 2),
        "average_daily_sold_quantity": round(safe_ratio(sold_quantity, average_divisor), 2),
        "average_daily_payment_amount": round(safe_ratio(payment_amount, average_divisor), 2),
        "average_daily_refund_amount": round(safe_ratio(refund_amount, average_divisor), 2),
    }


def _shop_daily_click_through_rate(
    *,
    click_user_count: float,
    click_count: float,
    exposure_user_count: float,
    has_click_user_count: bool,
    has_click_count: bool,
) -> tuple[float, str]:
    if exposure_user_count <= 0:
        return 0.0, "unavailable"
    if has_click_user_count:
        return safe_ratio(click_user_count, exposure_user_count), "click_user_count / exposure_user_count"
    if has_click_count:
        return safe_ratio(click_count, exposure_user_count), "click_count / exposure_user_count"
    return 0.0, "unavailable"


def _shop_daily_click_conversion_rate(
    *,
    click_count: float,
    order_count: float,
    has_order_count: bool,
    avg_click_conversion_rate: float,
) -> tuple[float, str]:
    if click_count > 0 and has_order_count:
        return safe_ratio(order_count, click_count), "order_count / click_count"
    if avg_click_conversion_rate > 0:
        return avg_click_conversion_rate, "avg imported click_conversion_rate"
    return 0.0, "unavailable"


def _shop_daily_conversion_rate(
    *,
    visitor_count: float,
    order_count: float,
    buyer_count: float,
    has_order_count: bool,
    has_buyer_count: bool,
    avg_conversion_rate: float,
) -> tuple[float, str]:
    if visitor_count > 0 and has_buyer_count:
        return safe_ratio(buyer_count, visitor_count), "buyer_count / visitor_count"
    if visitor_count > 0 and has_order_count:
        return safe_ratio(order_count, visitor_count), "order_count / visitor_count"
    if avg_conversion_rate > 0:
        return avg_conversion_rate, "avg imported conversion_rate"
    return 0.0, "unavailable"


def _shop_daily_refund_rate(
    *,
    payment_amount: float,
    refund_amount: float,
    avg_refund_rate: float,
    has_imported_refund_rate: bool,
) -> tuple[float, str]:
    if payment_amount > 0:
        return safe_ratio(refund_amount, payment_amount), "refund_amount / payment_amount"
    if has_imported_refund_rate:
        return avg_refund_rate, "avg imported refund_rate"
    return 0.0, "unavailable"


def _sum_numeric(rows: list[dict[str, Any]], field: str) -> float:
    return sum((_money(row.get(field)) for row in rows), 0.0)


def _numeric_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = to_float(row.get(field))
        if value is not None:
            values.append(float(value))
    return values


def _has_numeric_value(rows: list[dict[str, Any]], field: str) -> bool:
    return any(to_float(row.get(field)) is not None for row in rows)


def _average(values: list[float]) -> float:
    return safe_ratio(sum(values), len(values))


def _calculate_product_metrics(
    *,
    order_items: list[dict[str, Any]],
    products: list[dict[str, Any]],
    refunds: list[dict[str, Any]],
    valid_order_ids: set[str],
    store_net_sales: float,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    valid_items = [
        item
        for item in order_items
        if item.get("order_id") and str(item.get("order_id")) in valid_order_ids
    ]
    refunds_by_order_product: dict[tuple[str, str], float] = defaultdict(float)
    refunds_without_product: dict[str, float] = defaultdict(float)
    for refund in refunds:
        order_id = str(refund.get("order_id") or "")
        if not order_id or order_id not in valid_order_ids:
            continue
        product_id = str(refund.get("product_id") or "")
        amount = _money(refund.get("refund_amount"))
        if product_id:
            refunds_by_order_product[(order_id, product_id)] += amount
        else:
            refunds_without_product[order_id] += amount

    items_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in valid_items:
        items_by_order[str(item.get("order_id"))].append(item)

    if refunds_without_product:
        warnings.append(
            {
                "code": "refund_without_product",
                "message": "部分退款记录缺少商品 ID，商品退款率按订单关联商品估算，商品净成交额按明细金额比例分摊退款",
                "affected_order_count": len(refunds_without_product),
            }
        )

    product_orders: dict[str, set[str]] = defaultdict(set)
    product_refund_orders: dict[str, set[str]] = defaultdict(set)
    product_gross: dict[str, float] = defaultdict(float)
    product_refunds: dict[str, float] = defaultdict(float)
    product_labels: dict[str, dict[str, Any]] = {}
    product_prices = _product_prices(products)
    estimated_item_amount_count = 0
    explicit_refund_preferred_count = 0

    for item in valid_items:
        order_id = str(item.get("order_id"))
        product_key = _product_key(item)
        product_labels.setdefault(
            product_key,
            {
                "product_id": item.get("product_id"),
                "product_name": item.get("product_name"),
                "sku_id": item.get("sku_id"),
                "sku_name": item.get("sku_name"),
            },
        )
        product_orders[product_key].add(order_id)
        item_amount = _money(item.get("item_amount"))
        if item_amount == 0:
            estimated_item_amount = _estimated_item_amount(item, product_prices)
            if estimated_item_amount > 0:
                item_amount = estimated_item_amount
                estimated_item_amount_count += 1
        product_gross[product_key] += item_amount

        explicit_item_refund = _money(item.get("refund_amount"))
        product_id = str(item.get("product_id") or "")
        refund_table_amount = refunds_by_order_product.get((order_id, product_id), 0.0)
        refund_amount = explicit_item_refund if explicit_item_refund > 0 else refund_table_amount
        if explicit_item_refund > 0 and refund_table_amount > 0:
            explicit_refund_preferred_count += 1

        unassigned_refund = refunds_without_product.get(order_id, 0.0)
        if unassigned_refund > 0:
            refund_amount += _allocated_refund(item, items_by_order[order_id], unassigned_refund)

        if refund_amount > 0:
            product_refund_orders[product_key].add(order_id)
            product_refunds[product_key] += refund_amount

    if estimated_item_amount_count:
        warnings.append(
            {
                "code": "estimated_item_amount",
                "message": "部分订单明细缺少商品实付金额，已按商品表价格和数量估算商品成交额",
                "affected_row_count": estimated_item_amount_count,
            }
        )
    if explicit_refund_preferred_count:
        warnings.append(
            {
                "code": "item_refund_amount_preferred",
                "message": "部分订单明细和退款表同时提供商品退款金额，商品级退款额以订单明细为准，避免重复计算",
                "affected_row_count": explicit_refund_preferred_count,
            }
        )

    rows: list[dict[str, Any]] = []
    for product_key in sorted(product_orders, key=lambda key: product_gross[key], reverse=True):
        gross = product_gross[product_key]
        refund_amount = product_refunds[product_key]
        net_amount = max(gross - refund_amount, 0.0)
        order_count = len(product_orders[product_key])
        refund_order_count = len(product_refund_orders[product_key])
        labels = product_labels[product_key]
        rows.append(
            {
                "product_id": labels.get("product_id"),
                "product_name": labels.get("product_name"),
                "sku_id": labels.get("sku_id"),
                "sku_name": labels.get("sku_name"),
                "order_count": order_count,
                "refund_order_count": refund_order_count,
                "refund_rate": round(safe_ratio(refund_order_count, order_count), 6),
                "gross_amount": round(gross, 2),
                "refund_amount": round(refund_amount, 2),
                "net_amount": round(net_amount, 2),
                "contribution_rate": round(safe_ratio(net_amount, store_net_sales), 6),
            }
        )
    return rows


def _product_prices(products: list[dict[str, Any]]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for product in products:
        price = _money(product.get("price"))
        if price <= 0:
            continue
        for key in (
            _product_price_key(product.get("product_id"), product.get("sku_id")),
            _product_price_key(product.get("product_id"), None),
            _product_price_key(product.get("product_name"), product.get("sku_id")),
            _product_price_key(product.get("product_name"), None),
        ):
            if key and key not in prices:
                prices[key] = price
    return prices


def _estimated_item_amount(item: dict[str, Any], product_prices: dict[str, float]) -> float:
    quantity = _money(item.get("quantity")) or 1.0
    for key in (
        _product_price_key(item.get("product_id"), item.get("sku_id")),
        _product_price_key(item.get("product_id"), None),
        _product_price_key(item.get("product_name"), item.get("sku_id")),
        _product_price_key(item.get("product_name"), None),
    ):
        price = product_prices.get(key)
        if price:
            return quantity * price
    return 0.0


def _product_price_key(product_value: Any, sku_value: Any) -> str | None:
    if not product_value:
        return None
    return f"{product_value}::{sku_value or ''}"


def _calculate_audience_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranked_segments = sorted(
        (_audience_metric_row(row) for row in rows),
        key=lambda row: (
            row["conversion_rate"],
            row["payment_amount"],
            row["order_count"],
            row["visitor_count"],
        ),
        reverse=True,
    )
    primary_segments = [
        row
        for row in ranked_segments
        if row["order_count"] > 0 or row["payment_amount"] > 0 or row["conversion_rate"] > 0
    ][:20]
    potential_segments = [
        row
        for row in ranked_segments
        if row["visitor_count"] > 0 and row["order_count"] == 0 and row["conversion_rate"] == 0
    ][:20]
    risk_segments = [
        row
        for row in ranked_segments
        if row["refund_rate"] >= 0.2 and (row["order_count"] >= 3 or row["payment_amount"] > 0)
    ][:20]

    return {
        "primary_segments": primary_segments,
        "potential_segments": potential_segments,
        "risk_segments": risk_segments,
        "segment_count": len(ranked_segments),
        "dimensions": sorted({row["dimension"] for row in ranked_segments if row["dimension"]}),
    }


def _audience_metric_row(row: dict[str, Any]) -> dict[str, Any]:
    visitor_count = _money(row.get("visitor_count"))
    order_count = _money(row.get("order_count"))
    conversion_rate = _money(row.get("conversion_rate"))
    if conversion_rate == 0 and visitor_count > 0 and order_count > 0:
        conversion_rate = safe_ratio(order_count, visitor_count)

    return {
        "product_id": row.get("product_id"),
        "product_name": row.get("product_name"),
        "category": row.get("category"),
        "dimension": row.get("dimension"),
        "segment_label": row.get("segment_label"),
        "visitor_count": round(visitor_count, 2),
        "add_to_cart_count": round(_money(row.get("add_to_cart_count")), 2),
        "order_count": round(order_count, 2),
        "payment_amount": round(_money(row.get("payment_amount")), 2),
        "conversion_rate": round(conversion_rate, 6),
        "refund_rate": round(_money(row.get("refund_rate")), 6),
        "region": row.get("region"),
        "gender": row.get("gender"),
        "age_group": row.get("age_group"),
        "consumption_level": row.get("consumption_level"),
        "active_hour": row.get("active_hour"),
    }


def _allocated_refund(
    item: dict[str, Any],
    order_items: list[dict[str, Any]],
    unassigned_refund: float,
) -> float:
    item_amount = _money(item.get("item_amount"))
    order_item_amount = sum(_money(row.get("item_amount")) for row in order_items)
    if order_item_amount <= 0:
        return safe_ratio(unassigned_refund, len(order_items))
    return unassigned_refund * safe_ratio(item_amount, order_item_amount)


def _refunds_by_order(refunds: list[dict[str, Any]]) -> dict[str, float]:
    grouped: dict[str, float] = defaultdict(float)
    for refund in refunds:
        order_id = str(refund.get("order_id") or "")
        if order_id:
            grouped[order_id] += _money(refund.get("refund_amount"))
    return grouped


def _order_refund_amount(order: dict[str, Any], refunds_by_order: dict[str, float]) -> float:
    order_id = str(order.get("order_id") or "")
    explicit_amount = _money(order.get("refund_amount"))
    return max(explicit_amount, refunds_by_order.get(order_id, 0.0))


def _money(value: Any) -> float:
    parsed = to_float(value)
    return float(parsed or 0.0)


def _percent_text(value: Any) -> str:
    return f"{_money(value) * 100:.2f}%"


def _money_text(value: Any) -> str:
    return f"{_money(value):.2f}"


def _audience_decision_label(row: dict[str, Any]) -> str:
    segment = row.get("segment_label") or row.get("region") or row.get("gender") or row.get("age_group") or "未知客群"
    dimension = row.get("dimension") or "人群"
    product = row.get("product_name") or row.get("category")
    return f"{product}-{dimension}:{segment}" if product else f"{dimension}:{segment}"


def _date_key(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    compact_candidate = text[:8]
    if len(compact_candidate) == 8 and compact_candidate.isdigit():
        try:
            return datetime.strptime(compact_candidate, "%Y%m%d").date().isoformat()
        except ValueError:
            pass
    normalized = text.replace("/", "-").replace(".", "-")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        pass
    if len(normalized) >= 10:
        candidate = normalized[:10]
        try:
            return datetime.fromisoformat(candidate).date().isoformat()
        except ValueError:
            return None
    return None


def _product_key(item: dict[str, Any]) -> str:
    for field in ("product_id", "product_name", "sku_id"):
        value = item.get(field)
        if value:
            return f"{field}:{value}"
    return f"row:{item.get('id')}"
