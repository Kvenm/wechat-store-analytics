from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from shared.paths import ensure_dir
from warehouse.repository import create_strategy_report, get_analysis_run


MIN_PRODUCT_ORDER_COUNT_FOR_DECISION = 3
ADD_PRODUCT_CONTRIBUTION_RATE = 0.2
LOW_PRODUCT_REFUND_RATE = 0.15
HIGH_PRODUCT_REFUND_RATE = 0.2
LOW_PRODUCT_CONTRIBUTION_RATE = 0.05
LOW_CLICK_THROUGH_RATE = 0.02
LOW_CLICK_CONVERSION_RATE = 0.02
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
ORDER_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:\d{12,}|[A-Za-z]{1,8}\d{8,}[A-Za-z0-9]*)(?![A-Za-z0-9])")
SENSITIVE_MESSAGE_FIELD_PATTERN = re.compile(
    r"(?i)\b(?:order_id|order_no|order_number|transaction_id|buyer_id|buyer|buyer_name|openid|"
    r"phone|mobile|tel|address)[=:：]\s*[^,，;；\s]+"
    r"|(?:订单号|买家|收件人|手机号|电话|地址)[=:：]\s*[^,，;；\s]+"
)


def generate_report(
    conn: Any,
    *,
    analysis_run_id: str,
    reports_dir: str | Path,
) -> dict[str, Any]:
    run = get_analysis_run(conn, analysis_run_id)
    metrics = json.loads(run["metrics_json"])
    warnings = json.loads(run["warnings_json"] or "[]")
    shop_id = run["shop_id"]
    output_dir = ensure_dir(Path(reports_dir))
    basename = f"{shop_id}_{analysis_run_id}"
    markdown_path = output_dir / f"{basename}.md"
    summary_csv_path = output_dir / f"{basename}_summary.csv"
    product_csv_path = output_dir / f"{basename}_products.csv"
    audience_csv_path = output_dir / f"{basename}_audience.csv"
    decisions_csv_path = output_dir / f"{basename}_decisions.csv"

    markdown_path.write_text(_render_markdown(run, metrics, warnings), encoding="utf-8")
    _write_summary_csv(summary_csv_path, metrics)
    _write_product_csv(product_csv_path, metrics)
    _write_audience_csv(audience_csv_path, metrics)
    _write_decisions_csv(decisions_csv_path, metrics, warnings)

    report_id = create_strategy_report(
        conn,
        analysis_run_id=analysis_run_id,
        shop_id=shop_id,
        markdown_path=str(markdown_path),
        summary_csv_path=str(summary_csv_path),
        product_csv_path=str(product_csv_path),
        audience_csv_path=str(audience_csv_path),
        warnings=warnings,
    )
    return {
        "report_id": report_id,
        "analysis_run_id": analysis_run_id,
        "markdown_path": str(markdown_path),
        "summary_csv_path": str(summary_csv_path),
        "product_csv_path": str(product_csv_path),
        "audience_csv_path": str(audience_csv_path),
        "decisions_csv_path": str(decisions_csv_path),
        "warnings": warnings,
    }


def _render_markdown(run: dict[str, Any], metrics: dict[str, Any], warnings: list[dict[str, Any]]) -> str:
    order_metrics = metrics.get("order_metrics", {})
    shop_daily_metrics = metrics.get("shop_daily_metrics", {})
    product_metrics = metrics.get("product_metrics", [])
    audience_metrics = metrics.get("audience_metrics", {})
    data_coverage = metrics.get("data_coverage", {})
    date_from = metrics.get("date_from") or "未限定"
    date_to = metrics.get("date_to") or "未限定"

    lines = [
        f"# 微信小店运营分析报告",
        "",
        f"- 店铺 ID：`{run['shop_id']}`",
        f"- 分析 Run：`{run['id']}`",
        f"- 分析周期：`{date_from}` 至 `{date_to}`",
        f"- 生成时间：`{run['created_at']}`",
        f"- 数据覆盖：`{_coverage_label(data_coverage)}`",
        f"- 指标口径：{_metric_scope_text(data_coverage)}",
        "",
        "## 核心指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 总订单量 | {order_metrics.get('total_order_count', 0)} |",
        f"| 支付订单数 | {order_metrics.get('paid_order_count', order_metrics.get('total_order_count', 0))} |",
        f"| 有效成交订单量 | {order_metrics.get('effective_order_count', 0)} |",
        f"| 退款订单量 | {order_metrics.get('refund_order_count', 0)} |",
        f"| 退款占比 | {_format_percent(order_metrics.get('refund_order_rate', 0))} |",
        f"| GMV | {_format_money(order_metrics.get('gmv', 0))} |",
        f"| 退款金额 | {_format_money(order_metrics.get('refund_amount', 0))} |",
        f"| 净成交额 | {_format_money(order_metrics.get('net_sales_amount', 0))} |",
        f"| 客单价 | {_format_money(order_metrics.get('average_order_value', 0))} |",
        "",
        "## 数据覆盖",
        "",
    ]
    lines.extend(_data_coverage_lines(data_coverage))
    lines.extend(["", "## 数据缺口与决策限制", ""])
    lines.extend(_decision_limitation_lines(metrics, warnings))
    lines.extend([
        "",
        "## 店铺日概览",
        "",
    ])

    if shop_daily_metrics.get("row_count"):
        lines.extend(
            [
                f"- 日概览周期：`{shop_daily_metrics.get('date_from') or '未知'}` 至 `{shop_daily_metrics.get('date_to') or '未知'}`",
                "",
                "| 指标 | 数值 |",
                "|---|---:|",
                f"| 统计天数 | {shop_daily_metrics.get('date_count', 0)} |",
                f"| 商品曝光人数 | {_format_number(shop_daily_metrics.get('exposure_user_count', 0))} |",
                f"| 商品点击人数 | {_format_number(shop_daily_metrics.get('click_user_count', 0))} |",
                f"| 商品点击次数 | {_format_number(shop_daily_metrics.get('click_count', 0))} |",
                f"| 下单金额 | {_format_money(shop_daily_metrics.get('order_amount', 0))} |",
                f"| 下单订单数 | {_format_number(shop_daily_metrics.get('order_submit_count', 0))} |",
                f"| 下单人数 | {_format_number(shop_daily_metrics.get('order_user_count', 0))} |",
                f"| 成交订单数 | {_format_number(shop_daily_metrics.get('order_count', 0))} |",
                f"| 成交人数 | {_format_number(shop_daily_metrics.get('buyer_count', 0))} |",
                f"| 成交件数 | {_format_number(shop_daily_metrics.get('sold_quantity', 0))} |",
                f"| 成交金额 | {_format_money(shop_daily_metrics.get('payment_amount', 0))} |",
                f"| 退款金额 | {_format_money(shop_daily_metrics.get('refund_amount', 0))} |",
                f"| 净成交额 | {_format_money(shop_daily_metrics.get('net_payment_amount', 0))} |",
                f"| 商品点击率 | {_format_percent(shop_daily_metrics.get('click_through_rate', 0))} |",
                f"| 点击成交率 | {_format_percent(shop_daily_metrics.get('click_conversion_rate', 0))} |",
                f"| 退款金额率 | {_format_percent(shop_daily_metrics.get('refund_rate', 0))} |",
                f"| 日均成交金额 | {_format_money(shop_daily_metrics.get('average_daily_payment_amount', 0))} |",
                "",
            ]
        )
    else:
        lines.extend(["暂无可计算的店铺日概览。", ""])

    lines.extend([
        "## 商品表现",
        "",
    ])

    if product_metrics:
        lines.extend(
            [
                "| 商品 | 成交订单数 | 退款订单数 | 商品退款率 | 净成交额 | 商品贡献 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in product_metrics[:30]:
            product_label = row.get("product_name") or row.get("product_id") or "未知商品"
            lines.append(
                "| {product} | {orders} | {refund_orders} | {refund_rate} | {net} | {contribution} |".format(
                    product=str(product_label).replace("|", "\\|"),
                    orders=row.get("order_count", 0),
                    refund_orders=row.get("refund_order_count", 0),
                    refund_rate=_format_percent(row.get("refund_rate", 0)),
                    net=_format_money(row.get("net_amount", 0)),
                    contribution=_format_percent(row.get("contribution_rate", 0)),
                )
            )
    else:
        lines.append("暂无可计算的商品明细。")

    lines.extend(["", "## 人群适配", ""])
    lines.extend(_audience_section_lines(audience_metrics))
    lines.extend(["", "## 初步投放建议", ""])
    lines.extend(_strategy_lines(metrics, warnings))
    lines.extend(["", "## 结构化经营决策", ""])
    lines.extend(_business_decision_lines(metrics, warnings))
    lines.extend(["", "## 数据质量 Warnings", ""])
    if warnings:
        for warning in warnings[:100]:
            message = _safe_warning_message(warning)
            code = warning.get("code", "warning")
            lines.append(f"- `{code}`：{message}")
    else:
        lines.append("暂无 warning。")

    lines.extend(
        [
            "",
            "## 口径说明",
            "",
            "- 分析周期按订单创建时间筛选。",
            "- 退款金额优先使用订单表退款金额与退款表聚合金额中的较大值。",
            "- 商品退款率按包含该商品且发生退款的订单数 / 包含该商品的有效成交订单数计算。",
            "- 商品贡献按商品净成交额 / 全店净成交额计算。",
            "- 人群适配来自罗盘/流量/商品分析导出文件，采集类型 `compass` 导入后映射为 `audience_insights`。",
            "- 当前人群洞察按店铺全量已导入数据计算，尚未按订单分析周期过滤。",
            "- 结构化经营决策只使用聚合指标，不展示订单号、买家信息等原始记录字段。",
        ]
    )
    return "\n".join(lines) + "\n"


def _audience_section_lines(audience_metrics: dict[str, Any]) -> list[str]:
    primary_segments = audience_metrics.get("primary_segments", [])
    potential_segments = audience_metrics.get("potential_segments", [])
    risk_segments = audience_metrics.get("risk_segments", [])
    lines: list[str] = []

    if not audience_metrics.get("segment_count"):
        return ["暂无可计算的人群/罗盘明细。"]

    lines.extend(["### 主力客群", ""])
    lines.extend(_audience_table_lines(primary_segments[:15]))
    lines.extend(["", "### 潜力客群", ""])
    lines.extend(_audience_table_lines(potential_segments[:10]) if potential_segments else ["暂无明显潜力客群。"])
    lines.extend(["", "### 风险客群", ""])
    lines.extend(_audience_table_lines(risk_segments[:10]) if risk_segments else ["暂无明显高退款客群。"])
    return lines


def _audience_table_lines(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["暂无数据。"]
    lines = [
        "| 商品/品类 | 维度 | 人群标签 | 访客 | 成交 | 转化率 | 退款率 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        product_label = row.get("product_name") or row.get("category") or row.get("product_id") or "全店"
        segment = row.get("segment_label") or row.get("region") or row.get("gender") or row.get("age_group") or "未知"
        lines.append(
            "| {product} | {dimension} | {segment} | {visitors} | {orders} | {conversion} | {refund} |".format(
                product=_escape_table_cell(product_label),
                dimension=_escape_table_cell(row.get("dimension") or "未标注"),
                segment=_escape_table_cell(segment),
                visitors=row.get("visitor_count", 0),
                orders=row.get("order_count", 0),
                conversion=_format_percent(row.get("conversion_rate", 0)),
                refund=_format_percent(row.get("refund_rate", 0)),
            )
        )
    return lines


def _strategy_lines(metrics: dict[str, Any], warnings: list[dict[str, Any]]) -> list[str]:
    order_metrics = metrics.get("order_metrics", {})
    shop_daily_metrics = metrics.get("shop_daily_metrics", {})
    product_metrics = metrics.get("product_metrics", [])
    audience_metrics = metrics.get("audience_metrics", {})
    lines: list[str] = []

    if not product_metrics:
        lines.append("- 当前缺少商品明细，先补齐订单明细后再生成商品投放建议。")

    high_contribution = [
        row
        for row in product_metrics
        if float(row.get("contribution_rate") or 0) >= 0.2 and float(row.get("refund_rate") or 0) < 0.15
    ]
    high_refund = [
        row
        for row in product_metrics
        if int(row.get("order_count") or 0) >= 3 and float(row.get("refund_rate") or 0) >= 0.2
    ]
    if high_contribution:
        labels = "、".join(str(row.get("product_name") or row.get("product_id")) for row in high_contribution[:5])
        lines.append(f"- 优先放大低退款且贡献高的商品：{labels}。")
    if high_refund:
        labels = "、".join(str(row.get("product_name") or row.get("product_id")) for row in high_refund[:5])
        lines.append(f"- 暂缓放量或复盘高退款商品：{labels}。")

    primary_segments = audience_metrics.get("primary_segments", [])
    potential_segments = audience_metrics.get("potential_segments", [])
    risk_segments = audience_metrics.get("risk_segments", [])
    if primary_segments:
        labels = "、".join(_segment_label(row) for row in primary_segments[:5])
        lines.append(f"- 人群定向优先测试高转化客群：{labels}。")
    if potential_segments:
        labels = "、".join(_segment_label(row) for row in potential_segments[:5])
        lines.append(f"- 潜力客群建议小预算测素材和承接页：{labels}。")
    if risk_segments:
        labels = "、".join(_segment_label(row) for row in risk_segments[:5])
        lines.append(f"- 高退款客群先收缩投放或单独复盘：{labels}。")

    refund_rate = float(order_metrics.get("refund_order_rate") or 0)
    if refund_rate >= 0.2:
        lines.append("- 店铺退款占比较高，建议先检查商品描述、尺码/规格表达、履约时效和售后原因，再扩大投放。")
    elif refund_rate == 0 and order_metrics.get("effective_order_count", 0):
        lines.append("- 当前周期未识别到退款订单，请确认退款表是否已导入，避免低估退款风险。")

    shop_daily_refund_rate = float(shop_daily_metrics.get("refund_rate") or 0)
    if shop_daily_metrics.get("row_count") and shop_daily_refund_rate >= 0.2:
        lines.append("- 店铺日概览显示退款金额率偏高，当前应先补订单、评价和退款原因数据，再判断哪些商品需要暂停投放。")
    if shop_daily_metrics.get("row_count") and not product_metrics:
        lines.append("- 已拿到店铺级曝光、点击、成交和退款趋势；下一步需要采商品明细，才能输出具体商品加推或下架建议。")

    if warnings:
        lines.append("- 报告存在数据质量 warning，投放动作建议以补齐字段后的复算结果为准。")
    if not lines:
        lines.append("- 当前指标未触发明显风险，可继续观察商品贡献和退款率的稳定性。")
    return lines


def _business_decision_lines(metrics: dict[str, Any], warnings: list[dict[str, Any]]) -> list[str]:
    order_metrics = metrics.get("order_metrics", {})
    shop_daily_metrics = metrics.get("shop_daily_metrics", {})
    product_metrics = metrics.get("product_metrics", [])
    audience_metrics = metrics.get("audience_metrics", {})
    data_coverage = metrics.get("data_coverage", {})
    lines: list[str] = []

    lines.extend(["### 决策边界", ""])
    lines.extend(_decision_boundary_lines(order_metrics, shop_daily_metrics, product_metrics, audience_metrics, data_coverage, warnings))
    lines.extend(["", "### 加推商品", ""])
    lines.extend(_add_product_decision_lines(product_metrics))
    lines.extend(["", "### 暂停/优化商品", ""])
    lines.extend(_pause_or_optimize_product_decision_lines(product_metrics))
    lines.extend(["", "### 退款/售后风险", ""])
    lines.extend(_refund_after_sales_decision_lines(order_metrics, shop_daily_metrics, product_metrics, audience_metrics))
    lines.extend(["", "### 价格/SKU/详情页/投放建议", ""])
    lines.extend(
        _pricing_sku_detail_ads_decision_lines(
            order_metrics,
            shop_daily_metrics,
            product_metrics,
            audience_metrics,
        )
    )
    return lines


def _decision_limitation_lines(metrics: dict[str, Any], warnings: list[dict[str, Any]]) -> list[str]:
    limitations = _decision_limitations(metrics, warnings)
    if not limitations:
        return ["- 当前未识别到阻断性数据缺口；涉及价格、毛利、库存和履约的动作仍需人工复核。"]
    lines = [
        "| 决策项 | 当前状态 | 还能做什么 | 不能直接做什么 | 需要补充的数据 |",
        "|---|---|---|---|---|",
    ]
    for item in limitations:
        lines.append(
            "| {decision} | {status} | {allowed} | {blocked} | {needed} |".format(
                decision=_escape_table_cell(item["decision"]),
                status=_escape_table_cell(item["status"]),
                allowed=_escape_table_cell(item["allowed"]),
                blocked=_escape_table_cell(item["blocked"]),
                needed=_escape_table_cell(item["needed"]),
            )
        )
    return lines


def _decision_limitations(metrics: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, str]]:
    order_metrics = metrics.get("order_metrics", {})
    shop_daily_metrics = metrics.get("shop_daily_metrics", {})
    product_metrics = metrics.get("product_metrics", [])
    audience_metrics = metrics.get("audience_metrics", {})
    data_coverage = metrics.get("data_coverage", {})
    warning_codes = {str(warning.get("code") or "") for warning in warnings}

    limitations: list[dict[str, str]] = []
    decision_level = str(data_coverage.get("decision_level") or "")
    coverage_status = _coverage_label(data_coverage)
    effective_orders = _as_float(order_metrics.get("effective_order_count"))
    order_items = _as_float(data_coverage.get("order_item_count"))
    refunds = _as_float(data_coverage.get("refund_count"))
    has_shop_daily = bool(shop_daily_metrics.get("row_count"))
    has_product_sample = _has_product_decision_sample(product_metrics)
    has_audience = bool(audience_metrics.get("segment_count"))
    has_warnings = bool(warning_codes)

    if decision_level in {"insufficient", "shop_summary_only", "order_only"} or not product_metrics:
        limitations.append(
            {
                "decision": "具体商品加推/暂停",
                "status": f"受限：{coverage_status}",
                "allowed": "可做店铺级订单、退款、GMV 复盘" if effective_orders or has_shop_daily else "只能确认当前数据不足",
                "blocked": "不能把某个商品直接定为加推、下架或暂停放量对象",
                "needed": "订单全部导出、订单商品明细、退款/售后明细、商品列表",
            }
        )
    elif not has_product_sample:
        limitations.append(
            {
                "decision": "具体商品加推/暂停",
                "status": f"受限：商品样本低于 {MIN_PRODUCT_ORDER_COUNT_FOR_DECISION} 单阈值",
                "allowed": "可做小预算验证和自然流量观察",
                "blocked": "不能直接大额放量或下架",
                "needed": "更长周期订单、商品明细、退款明细",
            }
        )

    if refunds == 0 and (
        _as_float(order_metrics.get("refund_order_count")) == 0
        or "refund_detail_coverage_mismatch" in warning_codes
    ):
        limitations.append(
            {
                "decision": "退款/售后归因",
                "status": "受限：缺少独立退款/售后明细",
                "allowed": "可用订单聚合退款金额做风险提醒",
                "blocked": "不能判断退款原因、责任环节或售后优化优先级",
                "needed": "退款/退货导出、售后原因、评价差评内容",
            }
        )

    if not has_audience:
        limitations.append(
            {
                "decision": "人群定向",
                "status": "受限：缺少人群/罗盘明细",
                "allowed": "可按已有商品与订单表现做粗略假设",
                "blocked": "不能直接锁定年龄、性别、地域、消费层级等投放定向",
                "needed": "人群数据、流量来源、商品访客和成交人群",
            }
        )

    if not has_shop_daily:
        limitations.append(
            {
                "decision": "详情页/素材承接",
                "status": "受限：缺少曝光、点击、成交趋势",
                "allowed": "可从订单和退款侧提出需要复核的方向",
                "blocked": "不能判断是主图标题问题、详情页转化问题还是流量质量问题",
                "needed": "店铺日概览、商品曝光、点击、转化、流量来源",
            }
        )

    limitations.append(
        {
            "decision": "价格/利润/库存",
            "status": "受限：系统未采集毛利、成本、库存周转和竞品价格",
            "allowed": "可根据客单价和退款风险提示价格需复核",
            "blocked": "不能直接给出涨价、降价、清仓或补货结论",
            "needed": "商品成本、毛利、库存、活动价、竞品价格、库存可售天数",
        }
    )

    if has_warnings:
        limitations.append(
            {
                "decision": "所有经营动作",
                "status": f"受限：存在 {_warning_codes(warnings)}",
                "allowed": "可按当前聚合结果做初步排查",
                "blocked": "不能把本次结论视为最终投放或下架依据",
                "needed": "修正 warning 对应字段后重新导入和分析",
            }
        )

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in limitations:
        key = (item["decision"], item["status"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _metric_scope_text(data_coverage: dict[str, Any]) -> str:
    if not data_coverage:
        return "核心指标仅基于已导入数据；缺失订单/退款明细时，0 不代表真实为 0。"
    decision_level = str(data_coverage.get("decision_level") or "")
    status = str(data_coverage.get("status") or "")
    if decision_level in {"insufficient", "shop_summary_only"} or status in {"missing_order_detail", "no_order_signal"}:
        return "核心指标仅基于已导入数据；缺失订单/退款明细时，0 不代表真实为 0。"
    if status in {"partial_order_detail", "orders_without_items"}:
        return "核心指标仅基于已导入订单样本；商品、退款和投放结论需要补齐后复算。"
    return "核心指标基于当前已导入订单、退款、商品和店铺日数据；仍需结合库存、毛利、履约人工复核。"


def _decision_boundary_lines(
    order_metrics: dict[str, Any],
    shop_daily_metrics: dict[str, Any],
    product_metrics: list[dict[str, Any]],
    audience_metrics: dict[str, Any],
    data_coverage: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> list[str]:
    lines = [
        (
            "- 可用聚合数据：有效成交订单 {orders} 单、商品明细 {products} 个、"
            "店铺日概览 {days} 天、人群洞察 {segments} 个。"
        ).format(
            orders=_format_number(order_metrics.get("effective_order_count", 0)),
            products=_format_number(len(product_metrics)),
            days=_format_number(shop_daily_metrics.get("date_count") or shop_daily_metrics.get("row_count") or 0),
            segments=_format_number(audience_metrics.get("segment_count", 0)),
        )
    ]
    if data_coverage:
        lines.append(
            "- 数据覆盖状态：{status}；决策等级：{level}。".format(
                status=_coverage_label(data_coverage),
                level=data_coverage.get("decision_level") or "未知",
            )
        )
    warning_codes = _warning_codes(warnings)
    if warning_codes:
        lines.append(
            f"- 数据质量 warning：{warning_codes}。以下决策只引用聚合指标；不展示订单号、买家信息等敏感字段。"
        )
    else:
        lines.append("- 当前未记录数据质量 warning；以下决策仍需结合库存、毛利、活动档期和履约能力复核。")
    lines.append("- 当前指标缺少毛利、库存、竞品价格和售后原因，价格和售后归因不能直接下最终决策。")
    return lines


def _add_product_decision_lines(product_metrics: list[dict[str, Any]]) -> list[str]:
    if not product_metrics:
        return ["- 数据不足：缺少商品明细，不能判断哪些商品适合加推，不能下加推决策。"]
    if not _has_product_decision_sample(product_metrics):
        return [
            (
                "- 数据不足：商品成交样本均低于 {orders} 单，不能下加推决策；"
                "可先保留自然流量或小预算验证。"
            ).format(orders=MIN_PRODUCT_ORDER_COUNT_FOR_DECISION)
        ]

    candidates = [
        row
        for row in product_metrics
        if _product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION
        and _as_float(row.get("net_amount")) > 0
        and _as_float(row.get("refund_rate")) < LOW_PRODUCT_REFUND_RATE
        and _as_float(row.get("contribution_rate")) >= ADD_PRODUCT_CONTRIBUTION_RATE
    ]
    candidates = sorted(
        candidates,
        key=lambda row: (
            _as_float(row.get("contribution_rate")),
            _as_float(row.get("net_amount")),
            _product_order_count(row),
        ),
        reverse=True,
    )
    if not candidates:
        return [
            (
                "- 暂不建议加推：当前有商品样本，但没有商品同时满足成交样本充足、"
                "贡献不低于 {contribution} 且退款率低于 {refund}；不能下明确加推决策。"
            ).format(
                contribution=_format_percent(ADD_PRODUCT_CONTRIBUTION_RATE),
                refund=_format_percent(LOW_PRODUCT_REFUND_RATE),
            )
        ]

    lines = [
        "| 商品/SKU | 决策 | 依据 |",
        "|---|---|---|",
    ]
    for row in candidates[:5]:
        lines.append(
            "| {product} | 建议加推 | {evidence}。优先扩大素材测试和主力客群预算。 |".format(
                product=_escape_table_cell(_product_label(row)),
                evidence=_escape_table_cell(_product_evidence(row)),
            )
        )
    return lines


def _pause_or_optimize_product_decision_lines(product_metrics: list[dict[str, Any]]) -> list[str]:
    if not product_metrics:
        return ["- 数据不足：缺少商品明细，不能判断哪些商品需要暂停或优化，不能下暂停/优化决策。"]
    if not _has_product_decision_sample(product_metrics):
        return [
            (
                "- 数据不足：商品成交样本均低于 {orders} 单，不能下暂停/优化决策；"
                "先补齐更多成交和退款样本。"
            ).format(orders=MIN_PRODUCT_ORDER_COUNT_FOR_DECISION)
        ]

    pause_candidates = [
        row
        for row in product_metrics
        if _product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION
        and _as_float(row.get("refund_rate")) >= HIGH_PRODUCT_REFUND_RATE
    ]
    optimize_candidates = [
        row
        for row in product_metrics
        if row not in pause_candidates
        and _product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION
        and (
            _as_float(row.get("refund_rate")) >= LOW_PRODUCT_REFUND_RATE
            or _as_float(row.get("contribution_rate")) <= LOW_PRODUCT_CONTRIBUTION_RATE
        )
    ]
    rows = sorted(
        pause_candidates,
        key=lambda row: (_as_float(row.get("refund_rate")), _product_order_count(row)),
        reverse=True,
    )[:5]
    rows.extend(
        sorted(
            optimize_candidates,
            key=lambda row: (
                _as_float(row.get("refund_rate")),
                -_as_float(row.get("contribution_rate")),
                _product_order_count(row),
            ),
            reverse=True,
        )[: max(0, 5 - len(rows))]
    )
    if not rows:
        return ["- 暂无需要暂停或重点优化的商品：当前有样本商品未触发高退款或低贡献阈值。"]

    lines = [
        "| 商品/SKU | 决策 | 依据 |",
        "|---|---|---|",
    ]
    for row in rows:
        is_pause = row in pause_candidates
        decision = "暂停放量并先优化" if is_pause else "保留观察并优化承接"
        next_step = "先复盘规格描述、详情页承诺、履约时效和售后原因" if is_pause else "先优化标题卖点、SKU 命名和详情页转化"
        lines.append(
            "| {product} | {decision} | {evidence}。{next_step}。 |".format(
                product=_escape_table_cell(_product_label(row)),
                decision=decision,
                evidence=_escape_table_cell(_product_evidence(row)),
                next_step=next_step,
            )
        )
    return lines


def _refund_after_sales_decision_lines(
    order_metrics: dict[str, Any],
    shop_daily_metrics: dict[str, Any],
    product_metrics: list[dict[str, Any]],
    audience_metrics: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    effective_orders = _as_float(order_metrics.get("effective_order_count"))
    order_refund_rate = _as_float(order_metrics.get("refund_order_rate"))
    shop_daily_refund_rate = _as_float(shop_daily_metrics.get("refund_rate"))
    has_order_refund_basis = effective_orders > 0
    has_shop_daily_refund_basis = bool(shop_daily_metrics.get("row_count"))
    risk_products = [
        row
        for row in product_metrics
        if _product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION
        and _as_float(row.get("refund_rate")) >= HIGH_PRODUCT_REFUND_RATE
    ]
    risk_segments = audience_metrics.get("risk_segments", [])

    if not has_order_refund_basis and not has_shop_daily_refund_basis and not product_metrics:
        return ["- 数据不足：订单、店铺日退款指标和商品明细都不足，不能判断退款/售后风险。"]

    if has_order_refund_basis:
        if order_refund_rate >= HIGH_PRODUCT_REFUND_RATE:
            lines.append(
                f"- 店铺订单退款占比为 {_format_percent(order_refund_rate)}，已达到高风险阈值；加推前先处理售后原因。"
            )
        elif order_refund_rate == 0:
            lines.append("- 当前订单聚合指标未识别退款；如退款表未完整导入，不能据此判断售后风险为零。")
        else:
            lines.append(f"- 店铺订单退款占比为 {_format_percent(order_refund_rate)}，暂未触发高风险阈值。")
    if has_shop_daily_refund_basis:
        if shop_daily_refund_rate >= HIGH_PRODUCT_REFUND_RATE:
            lines.append(f"- 店铺日概览退款金额率为 {_format_percent(shop_daily_refund_rate)}，需要优先复盘退款来源。")
        else:
            lines.append(f"- 店铺日概览退款金额率为 {_format_percent(shop_daily_refund_rate)}，暂未触发高风险阈值。")
    if risk_products:
        labels = "、".join(_product_label(row) for row in risk_products[:5])
        lines.append(f"- 商品级售后风险集中在：{labels}。先暂停放量或降低预算权重。")
    elif product_metrics:
        lines.append("- 商品级指标暂未发现高退款商品。")
    if risk_segments:
        labels = "、".join(_segment_label(row) for row in risk_segments[:5])
        lines.append(f"- 人群级退款风险集中在：{labels}。建议单独收缩或排除测试。")
    elif audience_metrics.get("segment_count"):
        lines.append("- 人群洞察暂未识别高退款客群。")
    else:
        lines.append("- 缺少人群/罗盘明细，不能判断售后风险是否集中在特定客群。")
    return lines


def _pricing_sku_detail_ads_decision_lines(
    order_metrics: dict[str, Any],
    shop_daily_metrics: dict[str, Any],
    product_metrics: list[dict[str, Any]],
    audience_metrics: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    average_order_value = _as_float(order_metrics.get("average_order_value"))
    if average_order_value > 0:
        lines.append(
            f"- 价格：当前客单价为 {_format_money(average_order_value)}；缺少商品价格、毛利、库存和竞品价格，不能直接下涨价或降价决策。"
        )
    else:
        lines.append("- 价格：缺少有效客单价和价格/毛利指标，不能下价格调整决策。")

    high_refund_products = [
        row
        for row in product_metrics
        if _product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION
        and _as_float(row.get("refund_rate")) >= HIGH_PRODUCT_REFUND_RATE
    ]
    if high_refund_products:
        labels = "、".join(_product_label(row) for row in high_refund_products[:5])
        lines.append(f"- SKU：优先核查 {labels} 的规格名、尺码/容量、组合装和发货配置，避免承诺与实物不一致。")
    elif product_metrics:
        top_skus = [
            row
            for row in product_metrics
            if _product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION and row.get("sku_name")
        ][:5]
        if top_skus:
            labels = "、".join(_product_label(row) for row in top_skus)
            lines.append(f"- SKU：保留当前成交 SKU 观察：{labels}；暂未看到需要因退款暂停的 SKU。")
        else:
            lines.append("- SKU：商品指标中缺少可用 SKU 名称，不能做 SKU 级拆分决策。")
    else:
        lines.append("- SKU：缺少商品明细，不能做 SKU 级决策。")

    if shop_daily_metrics.get("row_count"):
        click_through_rate = _as_float(shop_daily_metrics.get("click_through_rate"))
        click_conversion_rate = _as_float(shop_daily_metrics.get("click_conversion_rate"))
        if click_through_rate < LOW_CLICK_THROUGH_RATE:
            lines.append(
                f"- 详情页/素材：商品点击率为 {_format_percent(click_through_rate)}，先优化主图、标题卖点和价格呈现，再扩大投放。"
            )
        elif click_conversion_rate < LOW_CLICK_CONVERSION_RATE:
            lines.append(
                f"- 详情页：点击成交率为 {_format_percent(click_conversion_rate)}，先优化详情页利益点、SKU 选择和售后承诺。"
            )
        else:
            lines.append(
                f"- 详情页：点击率 {_format_percent(click_through_rate)}、点击成交率 {_format_percent(click_conversion_rate)} 暂未触发低转化阈值。"
            )
    else:
        lines.append("- 详情页：缺少店铺日曝光、点击和成交指标，不能判断详情页或素材承接问题。")

    add_candidates = [
        row
        for row in product_metrics
        if _product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION
        and _as_float(row.get("refund_rate")) < LOW_PRODUCT_REFUND_RATE
        and _as_float(row.get("contribution_rate")) >= ADD_PRODUCT_CONTRIBUTION_RATE
    ]
    primary_segments = audience_metrics.get("primary_segments", [])
    potential_segments = audience_metrics.get("potential_segments", [])
    if add_candidates:
        labels = "、".join(_product_label(row) for row in add_candidates[:3])
        lines.append(f"- 投放：预算优先给低退款高贡献商品：{labels}。")
    elif product_metrics:
        lines.append("- 投放：当前没有明确加推商品，先不要做大额放量，保留小预算验证。")
    else:
        lines.append("- 投放：缺少商品明细，不能指定投放商品。")
    if primary_segments:
        labels = "、".join(_segment_label(row) for row in primary_segments[:5])
        lines.append(f"- 人群：优先测试主力客群：{labels}。")
    elif potential_segments:
        labels = "、".join(_segment_label(row) for row in potential_segments[:5])
        lines.append(f"- 人群：主力客群不足，可用小预算验证潜力客群：{labels}。")
    else:
        lines.append("- 人群：缺少可用主力或潜力客群，不能下人群定向决策。")
    return lines


def _coverage_label(data_coverage: dict[str, Any]) -> str:
    labels = {
        "full_or_unknown": "订单覆盖未知/可能完整",
        "order_detail_available": "订单明细可用",
        "partial_order_detail": "订单明细疑似样本",
        "orders_without_items": "仅订单表，无商品明细",
        "missing_order_detail": "缺订单明细",
        "no_order_signal": "无订单信号",
    }
    status = str(data_coverage.get("status") or "")
    return labels.get(status, status or "覆盖未知")


def _data_coverage_lines(data_coverage: dict[str, Any]) -> list[str]:
    if not data_coverage:
        return ["- 数据覆盖状态未记录；报告只能按已入库聚合数据解释。"]
    lines = [
        "| 项目 | 数值 |",
        "|---|---:|",
        f"| 覆盖状态 | {_coverage_label(data_coverage)} |",
        f"| 决策等级 | {data_coverage.get('decision_level') or '未知'} |",
        f"| 订单明细数 | {_format_number(data_coverage.get('detail_order_count', 0))} |",
        f"| 订单商品明细数 | {_format_number(data_coverage.get('order_item_count', 0))} |",
        f"| 退款记录数 | {_format_number(data_coverage.get('refund_count', 0))} |",
        f"| 店铺汇总成交订单数 | {_format_number(data_coverage.get('overview_order_count', 0))} |",
        f"| 店铺汇总退款金额 | {_format_money(data_coverage.get('overview_refund_amount', 0))} |",
    ]
    reasons = data_coverage.get("reasons") or []
    if reasons:
        lines.extend(["", "覆盖限制："])
        lines.extend(f"- {reason}" for reason in reasons[:10])
    return lines


def _write_summary_csv(path: Path, metrics: dict[str, Any]) -> None:
    order_metrics = metrics.get("order_metrics", {})
    shop_daily_metrics = metrics.get("shop_daily_metrics", {})
    rows = [{"metric": f"orders.{key}", "value": value} for key, value in order_metrics.items()]
    rows.extend({"metric": f"data_coverage.{key}", "value": value} for key, value in metrics.get("data_coverage", {}).items() if key != "reasons")
    rows.extend({"metric": f"shop_daily.{key}", "value": value} for key, value in shop_daily_metrics.items())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("metric", "value"))
        writer.writeheader()
        writer.writerows(rows)


def _write_product_csv(path: Path, metrics: dict[str, Any]) -> None:
    rows = metrics.get("product_metrics", [])
    fieldnames = (
        "product_id",
        "product_name",
        "sku_id",
        "sku_name",
        "order_count",
        "refund_order_count",
        "refund_rate",
        "gross_amount",
        "refund_amount",
        "net_amount",
        "contribution_rate",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _write_audience_csv(path: Path, metrics: dict[str, Any]) -> None:
    audience_metrics = metrics.get("audience_metrics", {})
    rows = []
    for group_name in ("primary_segments", "potential_segments", "risk_segments"):
        for row in audience_metrics.get(group_name, []):
            rows.append({"group": group_name, **row})
    fieldnames = (
        "group",
        "product_id",
        "product_name",
        "category",
        "dimension",
        "segment_label",
        "visitor_count",
        "add_to_cart_count",
        "order_count",
        "payment_amount",
        "conversion_rate",
        "refund_rate",
        "region",
        "gender",
        "age_group",
        "consumption_level",
        "active_hour",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _write_decisions_csv(path: Path, metrics: dict[str, Any], warnings: list[dict[str, Any]] | None = None) -> None:
    decisions = list(metrics.get("decision_metrics", {}).get("decisions", []))
    decisions.extend(
        {
            "action": "决策限制",
            "target": item["decision"],
            "priority": "high" if str(item["status"]).startswith("受限") else "medium",
            "confidence": "high",
            "basis": f"{item['status']}；不能直接做：{item['blocked']}；需补充：{item['needed']}",
            "decision_allowed": item["allowed"],
            "blocked_reason": item["blocked"],
            "required_missing_data": item["needed"],
        }
        for item in _decision_limitations(metrics, warnings or [])
    )
    fieldnames = (
        "action",
        "target",
        "priority",
        "confidence",
        "basis",
        "decision_allowed",
        "blocked_reason",
        "required_missing_data",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in decisions:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _segment_label(row: dict[str, Any]) -> str:
    product_label = row.get("product_name") or row.get("category") or row.get("product_id") or "全店"
    segment = row.get("segment_label") or row.get("region") or row.get("gender") or row.get("age_group") or "未知"
    return f"{product_label}/{row.get('dimension') or '未标注'}={segment}"


def _product_label(row: dict[str, Any]) -> str:
    product = row.get("product_name") or row.get("product_id") or "未知商品"
    sku = row.get("sku_name") or row.get("sku_id")
    if sku:
        return f"{product} / {sku}"
    return str(product)


def _product_evidence(row: dict[str, Any]) -> str:
    return "，".join(
        [
            f"成交 {_format_number(row.get('order_count', 0))} 单",
            f"退款 {_format_number(row.get('refund_order_count', 0))} 单",
            f"商品退款率 {_format_percent(row.get('refund_rate', 0))}",
            f"净成交额 {_format_money(row.get('net_amount', 0))}",
            f"贡献 {_format_percent(row.get('contribution_rate', 0))}",
        ]
    )


def _has_product_decision_sample(product_metrics: list[dict[str, Any]]) -> bool:
    return any(_product_order_count(row) >= MIN_PRODUCT_ORDER_COUNT_FOR_DECISION for row in product_metrics)


def _product_order_count(row: dict[str, Any]) -> int:
    return int(_as_float(row.get("order_count")))


def _warning_codes(warnings: list[dict[str, Any]]) -> str:
    codes = []
    seen = set()
    for warning in warnings:
        code = str(warning.get("code") or "warning")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        return ""
    visible = "、".join(f"`{code}`" for code in codes[:8])
    if len(codes) > 8:
        visible += f" 等 {len(codes)} 类"
    return visible


def _safe_warning_message(warning: dict[str, Any]) -> str:
    message = warning.get("message")
    if message:
        return _redact_sensitive_text(message)
    return "详见数据质量记录；报告不展示订单号、买家信息等敏感字段。"


def _redact_sensitive_text(value: Any) -> str:
    text = str(value)
    text = SENSITIVE_MESSAGE_FIELD_PATTERN.sub(_redact_key_value_match, text)
    text = PHONE_PATTERN.sub("[PHONE_REDACTED]", text)
    return ORDER_ID_PATTERN.sub("[ORDER_REDACTED]", text)


def _redact_key_value_match(match: re.Match[str]) -> str:
    text = match.group(0)
    for separator in ("=", ":", "："):
        if separator in text:
            key, _ = text.split(separator, 1)
            return f"{key}{separator}[REDACTED]"
    return "[REDACTED]"


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _escape_table_cell(value: Any) -> str:
    return str(value).replace("|", "\\|")


def _format_percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _format_money(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _format_number(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "0"
