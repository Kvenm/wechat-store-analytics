from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModuleCapability:
    export_type: str
    label: str
    table_hint: str
    web_enabled: bool
    import_enabled: bool
    calibrated: bool
    source_priority: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["source_priority"] = list(self.source_priority)
        return data


EXPORT_SOURCE_PRIORITY = (
    "export_file",
    "web_table",
    "page_chart",
    "screenshot_ocr",
)

MODULE_CAPABILITIES: dict[str, ModuleCapability] = {
    "orders": ModuleCapability(
        export_type="orders",
        label="订单管理-全部导出",
        table_hint="orders",
        web_enabled=True,
        import_enabled=True,
        calibrated=True,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="真实网页导出已小范围跑通，导出 zip/xlsx 后会拆出 orders/order_items/refunds。",
    ),
    "product_list": ModuleCapability(
        export_type="product_list",
        label="商品管理-商品列表导出",
        table_hint="products",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="本轮先支持本地导出文件导入；真实页面导出按钮和状态筛选仍需登录后校准。",
    ),
    "products": ModuleCapability(
        export_type="products",
        label="店铺数据-商品数据概览",
        table_hint="shop_daily",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="历史配置里的 products 指商品数据/核心转化概览，不是商品管理基础资料。",
    ),
    "reviews": ModuleCapability(
        export_type="reviews",
        label="订单评价",
        table_hint="reviews",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="先允许本地评价表导入，真实评价页面待校准。",
    ),
    "refunds": ModuleCapability(
        export_type="refunds",
        label="退款/退货/售后导出",
        table_hint="refunds",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="先允许本地退款/退货/售后表导入，用于补充售后原因；真实售后页面待校准。",
    ),
    "audience": ModuleCapability(
        export_type="audience",
        label="人群数据",
        table_hint="audience_insights",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="页面导出/图表/OCR 待第二阶段校准。",
    ),
    "compass": ModuleCapability(
        export_type="compass",
        label="电商罗盘",
        table_hint="audience_insights",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="页面导出/图表/OCR 待第二阶段校准。",
    ),
    "funds": ModuleCapability(
        export_type="funds",
        label="资金流水与账单",
        table_hint="fund_flows",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="支持本地资金流水表导入，真实页面待校准。",
    ),
    "ads": ModuleCapability(
        export_type="ads",
        label="小店投放",
        table_hint="ad_spend",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="支持本地投放表导入，真实页面待校准。",
    ),
    "transactions": ModuleCapability(
        export_type="transactions",
        label="交易数据",
        table_hint="orders",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="建议优先使用 orders；独立交易数据页面待校准。",
    ),
    "repurchase": ModuleCapability(
        export_type="repurchase",
        label="老客复购",
        table_hint="shop_daily",
        web_enabled=False,
        import_enabled=True,
        calibrated=False,
        source_priority=EXPORT_SOURCE_PRIORITY,
        notes="支持本地表导入，真实页面待校准。",
    ),
}


def get_module_capability(export_type: str) -> ModuleCapability | None:
    return MODULE_CAPABILITIES.get(export_type)


def module_capabilities() -> list[dict[str, object]]:
    return [capability.to_dict() for capability in MODULE_CAPABILITIES.values()]


def web_enabled_types() -> set[str]:
    return {
        export_type
        for export_type, capability in MODULE_CAPABILITIES.items()
        if capability.web_enabled
    }


def import_enabled_types() -> set[str]:
    return {
        export_type
        for export_type, capability in MODULE_CAPABILITIES.items()
        if capability.import_enabled
    }


def unsupported_types_message(types: list[str], *, mode: str) -> str:
    enabled = sorted(web_enabled_types() if mode == "web" else import_enabled_types())
    return (
        f"未开放的模块：{'、'.join(types)}。"
        f"当前 {mode} 模式支持：{'、'.join(enabled) if enabled else '无'}。"
    )
