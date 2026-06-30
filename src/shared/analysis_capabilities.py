from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


BusinessReadiness = dict[str, Any]


def build_business_readiness(inspection: Mapping[str, Any]) -> list[BusinessReadiness]:
    tables = _tables(inspection)
    sources = _sources(inspection)
    return [
        _order_kpi_readiness(tables, sources),
        _product_contribution_readiness(tables, sources),
        _refund_risk_readiness(tables, sources),
        _review_readiness(tables, sources),
        _traffic_readiness(tables, sources),
        _ad_roi_readiness(tables, sources),
        _product_profile_readiness(tables, sources),
        _audience_readiness(tables, sources),
        _fund_readiness(tables, sources),
    ]


def _order_kpi_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    count = tables.get("orders", 0)
    source_ids = _basis_sources(sources, "orders")
    missing = _missing_columns(sources, "orders", ("order_id", "order_created_at", "payment_amount"))
    if count <= 0:
        return _blocked(
            "order_kpi",
            "订单量、GMV、客单价基础复盘",
            ["orders"],
            ["orders"],
            "缺订单数据，不能判断订单量、GMV 和客单价。",
        )
    return {
        "key": "order_kpi",
        "label": "订单量、GMV、客单价基础复盘",
        "status": "limited" if missing else "supported",
        "basis_tables": ["orders"],
        "basis_sources": source_ids,
        "supported_analysis": ["订单量", "GMV", "客单价", "时段成交复盘"],
        "limited_decisions": ["缺少关键列时，GMV/客单价口径需要人工复核。"] if missing else [],
        "missing": missing,
        "summary": f"已识别 {count} 条订单，可做店铺经营基础复盘。",
    }


def _product_contribution_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    item_count = tables.get("order_items", 0)
    if item_count <= 0:
        return _blocked(
            "product_contribution",
            "商品成交贡献与 SKU 拆分",
            ["order_items"],
            ["order_items"],
            "缺订单商品明细，不能把成交额、订单量准确归因到具体商品或 SKU。",
        )
    return {
        "key": "product_contribution",
        "label": "商品成交贡献与 SKU 拆分",
        "status": "supported",
        "basis_tables": ["order_items"],
        "basis_sources": _basis_sources(sources, "order_items"),
        "supported_analysis": ["商品成交排行", "SKU 成交拆分", "商品加推候选初筛"],
        "limited_decisions": ["仍需要结合退款、评价和流量数据再决定加推或下架。"],
        "missing": [],
        "summary": f"已识别 {item_count} 条订单明细，可做商品级成交贡献分析。",
    }


def _refund_risk_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    refund_count = tables.get("refunds", 0)
    orders = tables.get("orders", 0)
    if refund_count <= 0:
        return _blocked(
            "refund_risk",
            "退款/退货风险与售后原因",
            ["refunds"],
            ["refunds"],
            "缺退款/售后记录，不能判断商品退款率、退货率和退款原因。",
        )
    status = "supported" if orders > 0 else "limited"
    limited = [] if orders > 0 else ["缺订单分母，只能看退款记录分布，不能计算准确退款率。"]
    return {
        "key": "refund_risk",
        "label": "退款/退货风险与售后原因",
        "status": status,
        "basis_tables": ["orders", "refunds"] if orders > 0 else ["refunds"],
        "basis_sources": _basis_sources(sources, "orders", "refunds"),
        "supported_analysis": ["退款商品清单", "退款金额", "售后原因聚合"],
        "limited_decisions": limited,
        "missing": [] if orders > 0 else ["orders"],
        "summary": f"已识别 {refund_count} 条退款/售后记录，可做退款风险初判。",
    }


def _review_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    count = tables.get("reviews", 0)
    if count <= 0:
        return _blocked(
            "review_insights",
            "评价与差评关键词",
            ["reviews"],
            ["reviews"],
            "缺评价数据，不能提取差评关键词和详情页/尺码/质量问题。",
        )
    return {
        "key": "review_insights",
        "label": "评价与差评关键词",
        "status": "supported",
        "basis_tables": ["reviews"],
        "basis_sources": _basis_sources(sources, "reviews"),
        "supported_analysis": ["差评关键词", "商品口碑风险", "详情页和售后优化线索"],
        "limited_decisions": ["评价样本少时，只能作为问题线索，不能单独决定下架。"],
        "missing": [],
        "summary": f"已识别 {count} 条评价，可分析差评和口碑问题。",
    }


def _traffic_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    count = tables.get("shop_daily", 0) + tables.get("product_daily", 0) + tables.get("traffic_sources", 0)
    if count <= 0:
        return _blocked(
            "traffic_conversion",
            "流量、点击与转化判断",
            ["shop_daily", "product_daily", "traffic_sources"],
            ["shop_daily", "product_daily", "traffic_sources"],
            "缺店铺/商品流量或来源数据，不能判断曝光高转化差、转化好曝光少等投放机会。",
        )
    return {
        "key": "traffic_conversion",
        "label": "流量、点击与转化判断",
        "status": "supported",
        "basis_tables": _present_tables(tables, "shop_daily", "product_daily", "traffic_sources"),
        "basis_sources": _basis_sources(sources, "shop_daily", "product_daily", "traffic_sources"),
        "supported_analysis": ["流量来源", "点击转化", "商品曝光机会", "时段投放参考"],
        "limited_decisions": [],
        "missing": [],
        "summary": f"已识别 {count} 条流量/转化相关数据，可做转化链路判断。",
    }


def _ad_roi_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    count = tables.get("ad_spend", 0)
    if count <= 0:
        return _blocked(
            "ad_roi",
            "投放消耗与 ROI",
            ["ad_spend"],
            ["ad_spend"],
            "缺投放消耗数据，不能判断 ROI、预算分配和投放暂停/加量。",
        )
    return {
        "key": "ad_roi",
        "label": "投放消耗与 ROI",
        "status": "supported",
        "basis_tables": ["ad_spend"],
        "basis_sources": _basis_sources(sources, "ad_spend"),
        "supported_analysis": ["ROI", "投放成交", "消耗效率", "预算调整线索"],
        "limited_decisions": ["若缺商品维度或成交金额，只能做计划级粗判断。"],
        "missing": [],
        "summary": f"已识别 {count} 条投放数据，可做 ROI 初判。",
    }


def _product_profile_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    count = tables.get("products", 0) + tables.get("product_skus", 0)
    if count <= 0:
        return _blocked(
            "product_profile",
            "商品基础资料、价格与库存",
            ["products", "product_skus"],
            ["products", "product_skus"],
            "缺商品列表，不能辅助判断上下架状态、价格、SKU 和库存。",
        )
    return {
        "key": "product_profile",
        "label": "商品基础资料、价格与库存",
        "status": "supported",
        "basis_tables": _present_tables(tables, "products", "product_skus"),
        "basis_sources": _basis_sources(sources, "products", "product_skus"),
        "supported_analysis": ["商品清单", "价格/SKU/库存辅助判断", "上下架状态核对"],
        "limited_decisions": ["缺毛利、库存周转和竞品数据，不能单独决定价格策略。"],
        "missing": [],
        "summary": f"已识别 {count} 条商品/SKU 基础资料。",
    }


def _audience_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    count = tables.get("audience_insights", 0)
    if count <= 0:
        return _blocked(
            "audience_targeting",
            "商品受众与人群定向",
            ["audience_insights"],
            ["audience_insights"],
            "缺人群画像数据，不能直接判断年龄、性别、地域和消费层级定向。",
        )
    return {
        "key": "audience_targeting",
        "label": "商品受众与人群定向",
        "status": "supported",
        "basis_tables": ["audience_insights"],
        "basis_sources": _basis_sources(sources, "audience_insights"),
        "supported_analysis": ["主力客群", "潜力客群", "投放定向线索"],
        "limited_decisions": [],
        "missing": [],
        "summary": f"已识别 {count} 条人群画像数据，可做受众适配分析。",
    }


def _fund_readiness(
    tables: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
) -> BusinessReadiness:
    count = tables.get("fund_flows", 0)
    if count <= 0:
        return _blocked(
            "fund_reconciliation",
            "资金流水与结算核对",
            ["fund_flows"],
            ["fund_flows"],
            "缺资金流水，不能核对实收、退款、平台费用和结算差异。",
        )
    return {
        "key": "fund_reconciliation",
        "label": "资金流水与结算核对",
        "status": "supported",
        "basis_tables": ["fund_flows"],
        "basis_sources": _basis_sources(sources, "fund_flows"),
        "supported_analysis": ["实收金额", "退款金额", "结算流水核对"],
        "limited_decisions": ["若缺平台费用字段，售后成本率和净收入需要人工复核。"],
        "missing": [],
        "summary": f"已识别 {count} 条资金流水，可做资金核对。",
    }


def _blocked(
    key: str,
    label: str,
    basis_tables: list[str],
    missing: list[str],
    summary: str,
) -> BusinessReadiness:
    return {
        "key": key,
        "label": label,
        "status": "blocked",
        "basis_tables": basis_tables,
        "basis_sources": [],
        "supported_analysis": [],
        "limited_decisions": [summary],
        "missing": missing,
        "summary": summary,
    }


def _tables(inspection: Mapping[str, Any]) -> dict[str, int]:
    totals = inspection.get("totals")
    if not isinstance(totals, Mapping):
        return {}
    raw_tables = totals.get("tables")
    if not isinstance(raw_tables, Mapping):
        return {}
    tables: dict[str, int] = {}
    for table, count in raw_tables.items():
        try:
            tables[str(table)] = int(count)
        except (TypeError, ValueError):
            continue
    return tables


def _sources(inspection: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = inspection.get("source_inspections")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, Mapping)]


def _basis_sources(sources: Sequence[Mapping[str, Any]], *tables: str) -> list[str]:
    wanted = set(tables)
    result: list[str] = []
    for source in sources:
        source_id = str(source.get("source_id") or "")
        if not source_id:
            continue
        selected_table = str(source.get("selected_table") or "")
        row_counts = source.get("row_counts")
        derived_tables = {}
        if isinstance(row_counts, Mapping) and isinstance(row_counts.get("derived_tables"), Mapping):
            derived_tables = row_counts["derived_tables"]
        if selected_table in wanted or any(table in derived_tables for table in wanted):
            result.append(source_id)
    return list(dict.fromkeys(result))


def _missing_columns(
    sources: Sequence[Mapping[str, Any]],
    table: str,
    required_fields: tuple[str, ...],
) -> list[str]:
    relevant = [source for source in sources if source.get("selected_table") == table]
    if not relevant:
        return list(required_fields)
    missing: set[str] = set()
    for source in relevant:
        values = source.get("missing_required_columns")
        if isinstance(values, list):
            missing.update(str(value) for value in values if value)
    return sorted(missing)


def _present_tables(tables: Mapping[str, int], *names: str) -> list[str]:
    return [name for name in names if tables.get(name, 0) > 0]
