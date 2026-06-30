from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FieldKind = Literal["text", "amount", "datetime"]

FIELD_KIND_TEXT: FieldKind = "text"
FIELD_KIND_AMOUNT: FieldKind = "amount"
FIELD_KIND_DATETIME: FieldKind = "datetime"
FIELD_KINDS: tuple[FieldKind, ...] = (FIELD_KIND_TEXT, FIELD_KIND_AMOUNT, FIELD_KIND_DATETIME)

SOURCE_TYPE_MANUAL = "manual"
SOURCE_TYPE_MANUAL_EXPORT = "manual_export"
SOURCE_TYPE_API_PULL = "api_pull"
SOURCE_TYPE_COLLECTOR_EXPORT = "collector_export"
SOURCE_TYPE_COLLECTOR_TASK_METADATA = "collector_task_metadata"
SOURCE_TYPES: tuple[str, ...] = (
    SOURCE_TYPE_MANUAL,
    SOURCE_TYPE_MANUAL_EXPORT,
    SOURCE_TYPE_API_PULL,
    SOURCE_TYPE_COLLECTOR_EXPORT,
    SOURCE_TYPE_COLLECTOR_TASK_METADATA,
)

STANDARD_TABLES: tuple[str, ...] = (
    "orders",
    "order_items",
    "products",
    "product_skus",
    "refunds",
    "reviews",
    "shop_daily",
    "product_daily",
    "traffic_sources",
    "fund_flows",
    "ad_spend",
    "audience_insights",
)

METADATA_TABLES: tuple[str, ...] = (
    "ai_reports",
    "collection_tasks",
    "collection_task_items",
)

ALL_BATCH_TABLES: tuple[str, ...] = STANDARD_TABLES + METADATA_TABLES

EXPORT_TYPE_TO_TABLE: dict[str, str] = {
    "ads": "ad_spend",
    "audience": "audience_insights",
    "compass": "audience_insights",
    "funds": "fund_flows",
    "orders": "orders",
    "products": "products",
    "reviews": "reviews",
    "transactions": "orders",
}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    aliases: tuple[str, ...]
    kind: FieldKind = FIELD_KIND_TEXT
    required: bool = False


FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "orders": (
        FieldSpec("order_id", ("订单号", "订单编号", "订单ID", "交易单号", "订单id", "order_id", "order no", "order_no", "orderNo"), required=True),
        FieldSpec("buyer_id", ("买家ID", "用户ID", "客户ID", "买家账号", "openid", "buyer_id", "customer_id")),
        FieldSpec("status", ("订单状态", "交易状态", "状态", "order_status", "status")),
        FieldSpec("order_created_at", ("下单时间", "订单创建时间", "创建时间", "成交时间", "order_created_at", "created_at", "create_time"), kind="datetime", required=True),
        FieldSpec("paid_at", ("付款时间", "支付时间", "paid_at", "pay_time", "payment_time"), kind="datetime"),
        FieldSpec("payment_amount", ("实付金额", "买家实付", "订单实付金额", "买家实付金额", "支付金额", "订单金额", "成交金额", "付款金额", "payment_amount", "paid_amount", "gmv"), kind="amount", required=True),
        FieldSpec("shipping_amount", ("运费", "运费金额", "邮费", "shipping_amount", "freight"), kind="amount"),
        FieldSpec("discount_amount", ("优惠金额", "店铺优惠", "平台优惠", "折扣金额", "discount_amount"), kind="amount"),
        FieldSpec("refund_amount", ("退款金额", "已退款金额", "订单退款金额", "售后金额", "refund_amount"), kind="amount"),
        FieldSpec("currency", ("币种", "currency")),
    ),
    "order_items": (
        FieldSpec("order_item_id", ("子订单号", "订单明细ID", "明细ID", "商品单号", "item_id", "order_item_id")),
        FieldSpec("order_id", ("订单号", "订单编号", "订单ID", "交易单号", "order_id", "order_no"), required=True),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "商品标题", "商品信息", "商品", "标题", "product_name", "goods_name", "item_name"), required=True),
        FieldSpec("sku_id", ("SKU ID", "规格ID", "sku_id")),
        FieldSpec("sku_name", ("规格", "规格名称", "商品规格", "SKU", "sku_name", "sku")),
        FieldSpec("quantity", ("数量", "购买数量", "商品数量", "件数", "quantity", "qty"), kind="amount"),
        FieldSpec("item_amount", ("商品实付", "商品实付金额", "商品金额", "明细金额", "商品小计", "小计", "item_amount", "goods_amount"), kind="amount"),
        FieldSpec("refund_amount", ("商品退款金额", "退款金额", "已退款金额", "售后金额", "refund_amount"), kind="amount"),
    ),
    "products": (
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "标题", "product_name", "goods_name", "item_name"), required=True),
        FieldSpec("sku_id", ("SKU ID", "规格ID", "sku_id")),
        FieldSpec("sku_name", ("规格", "规格名称", "SKU", "sku_name", "sku")),
        FieldSpec("category", ("类目", "分类", "商品类目", "category")),
        FieldSpec("status", ("商品状态", "状态", "status")),
        FieldSpec("price", ("价格", "售价", "商品价格", "price"), kind="amount"),
        FieldSpec("stock", ("库存", "库存数量", "stock"), kind="amount"),
    ),
    "product_skus": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "标题", "商品标题", "product_name", "goods_name", "item_name"), required=True),
        FieldSpec("sku_id", ("SKU ID", "规格ID", "sku_id", "sku编号", "规格编码"), required=True),
        FieldSpec("sku_name", ("规格", "规格名称", "SKU", "sku_name", "sku", "规格值")),
        FieldSpec("category", ("类目", "分类", "商品类目", "category")),
        FieldSpec("status", ("商品状态", "SKU状态", "状态", "status")),
        FieldSpec("sku_price", ("SKU价格", "规格价格", "售价", "价格", "sku_price", "price"), kind="amount"),
        FieldSpec("stock", ("库存", "可售库存", "库存数量", "stock"), kind="amount"),
        FieldSpec("barcode", ("条形码", "商品条码", "barcode")),
    ),
    "refunds": (
        FieldSpec("refund_id", ("售后单号", "退款单号", "售后ID", "退款ID", "refund_id", "after_sale_id")),
        FieldSpec("order_id", ("订单号", "订单编号", "订单ID", "交易单号", "order_id", "order_no"), required=True),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "商品标题", "商品信息", "商品", "标题", "product_name", "goods_name", "item_name")),
        FieldSpec("sku_id", ("SKU ID", "规格ID", "sku_id")),
        FieldSpec("refund_status", ("售后状态", "退款状态", "退货状态", "状态", "refund_status", "status")),
        FieldSpec("refund_amount", ("退款金额", "已退款金额", "订单退款金额", "商品退款金额", "售后金额", "退还金额", "refund_amount"), kind="amount", required=True),
        FieldSpec("refund_created_at", ("申请时间", "售后申请时间", "退款申请时间", "refund_created_at", "created_at"), kind="datetime"),
        FieldSpec("refund_completed_at", ("退款完成时间", "售后完成时间", "完成时间", "refund_completed_at", "completed_at"), kind="datetime"),
        FieldSpec("reason", ("退款原因", "售后原因", "退货原因", "原因", "reason")),
    ),
    "reviews": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("review_id", ("评价ID", "评论ID", "评价编号", "review_id", "comment_id")),
        FieldSpec("order_id", ("订单号", "订单编号", "订单ID", "交易单号", "order_id", "order_no")),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "标题", "商品标题", "product_name", "goods_name", "item_name")),
        FieldSpec("sku_id", ("SKU ID", "规格ID", "sku_id")),
        FieldSpec("sku_name", ("规格", "规格名称", "SKU", "sku_name", "sku")),
        FieldSpec("buyer_id", ("买家ID", "用户ID", "客户ID", "买家账号", "openid", "buyer_id", "customer_id")),
        FieldSpec("rating", ("评分", "星级", "评价分", "rating", "score"), kind="amount"),
        FieldSpec("review_content", ("评价内容", "评论内容", "买家评价", "评价", "评论", "review_content", "content"), required=True),
        FieldSpec("review_created_at", ("评价时间", "评论时间", "创建时间", "review_created_at", "comment_time"), kind="datetime"),
        FieldSpec("reply_content", ("商家回复", "回复内容", "reply_content", "seller_reply")),
        FieldSpec("reply_created_at", ("回复时间", "reply_created_at", "reply_time"), kind="datetime"),
        FieldSpec("is_positive", ("是否好评", "好评", "情感", "评价类型", "is_positive", "sentiment")),
    ),
    "shop_daily": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("stat_date", ("日期", "统计日期", "数据日期", "时间", "date", "stat_date", "day"), kind="datetime", required=True),
        FieldSpec("visitor_count", ("访客数", "访问人数", "店铺访客数", "UV", "uv", "visitor_count"), kind="amount"),
        FieldSpec("view_count", ("浏览量", "访问次数", "PV", "pv", "view_count"), kind="amount"),
        FieldSpec("exposure_user_count", ("商品曝光人数", "曝光人数", "商品曝光用户数", "exposure_user_count", "impression_user_count"), kind="amount"),
        FieldSpec("click_user_count", ("商品点击人数", "点击人数", "商品点击用户数", "click_user_count"), kind="amount"),
        FieldSpec("click_count", ("商品点击次数", "点击次数", "商品点击量", "click_count"), kind="amount"),
        FieldSpec("order_amount", ("下单金额", "提交订单金额", "order_amount"), kind="amount"),
        FieldSpec("order_submit_count", ("下单订单数", "提交订单数", "order_submit_count"), kind="amount"),
        FieldSpec("order_user_count", ("下单人数", "下单用户数", "order_user_count"), kind="amount"),
        FieldSpec("order_count", ("成交订单数", "支付订单数", "订单数", "下单单量", "order_count"), kind="amount"),
        FieldSpec("buyer_count", ("支付买家数", "成交人数", "买家数", "buyer_count"), kind="amount"),
        FieldSpec("payment_amount", ("成交金额", "支付金额", "付款金额", "GMV", "gmv", "payment_amount"), kind="amount"),
        FieldSpec("refund_amount", ("退款金额", "售后金额", "成交退款金额", "refund_amount"), kind="amount"),
        FieldSpec("sold_quantity", ("成交件数", "支付件数", "售出件数", "sold_quantity"), kind="amount"),
        FieldSpec("conversion_rate", ("转化率", "支付转化率", "成交转化率", "conversion_rate", "cvr"), kind="amount"),
        FieldSpec("click_conversion_rate", ("点击成交率（次数）", "点击成交率(次数)", "点击成交率次数", "点击成交率", "click_conversion_rate"), kind="amount"),
        FieldSpec("refund_rate", ("退款率", "售后率", "refund_rate"), kind="amount"),
    ),
    "product_daily": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("stat_date", ("日期", "统计日期", "数据日期", "时间", "date", "stat_date", "day"), kind="datetime", required=True),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "标题", "商品标题", "product_name", "goods_name", "item_name"), required=True),
        FieldSpec("sku_id", ("SKU ID", "规格ID", "sku_id")),
        FieldSpec("category", ("类目", "分类", "商品类目", "category")),
        FieldSpec("visitor_count", ("访客数", "商品访客数", "访问人数", "UV", "uv", "visitor_count"), kind="amount"),
        FieldSpec("view_count", ("浏览量", "商品浏览量", "PV", "pv", "view_count"), kind="amount"),
        FieldSpec("add_to_cart_count", ("加购人数", "加购数", "加购件数", "add_to_cart", "cart_count"), kind="amount"),
        FieldSpec("order_count", ("成交订单数", "支付订单数", "订单数", "order_count"), kind="amount"),
        FieldSpec("buyer_count", ("支付买家数", "成交人数", "买家数", "buyer_count"), kind="amount"),
        FieldSpec("payment_amount", ("成交金额", "支付金额", "付款金额", "GMV", "gmv", "payment_amount"), kind="amount"),
        FieldSpec("refund_amount", ("退款金额", "售后金额", "refund_amount"), kind="amount"),
        FieldSpec("conversion_rate", ("转化率", "支付转化率", "成交转化率", "conversion_rate", "cvr"), kind="amount"),
        FieldSpec("refund_rate", ("退款率", "售后率", "refund_rate"), kind="amount"),
    ),
    "traffic_sources": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("stat_date", ("日期", "统计日期", "数据日期", "时间", "date", "stat_date", "day"), kind="datetime", required=True),
        FieldSpec("source_name", ("流量来源", "来源名称", "渠道", "渠道名称", "入口", "source_name", "traffic_source"), required=True),
        FieldSpec("source_type", ("来源类型", "渠道类型", "流量类型", "source_type")),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "标题", "商品标题", "product_name", "goods_name", "item_name")),
        FieldSpec("visitor_count", ("访客数", "访问人数", "UV", "uv", "visitor_count"), kind="amount"),
        FieldSpec("view_count", ("浏览量", "PV", "pv", "view_count"), kind="amount"),
        FieldSpec("order_count", ("成交订单数", "支付订单数", "订单数", "order_count"), kind="amount"),
        FieldSpec("payment_amount", ("成交金额", "支付金额", "付款金额", "GMV", "gmv", "payment_amount"), kind="amount"),
        FieldSpec("conversion_rate", ("转化率", "支付转化率", "成交转化率", "conversion_rate", "cvr"), kind="amount"),
    ),
    "fund_flows": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("flow_id", ("流水号", "资金流水号", "交易流水号", "flow_id", "transaction_id")),
        FieldSpec("flow_date", ("入账时间", "发生时间", "交易时间", "流水时间", "日期", "flow_date", "transaction_time"), kind="datetime", required=True),
        FieldSpec("flow_type", ("收支类型", "流水类型", "资金类型", "类型", "flow_type")),
        FieldSpec("biz_type", ("业务类型", "业务场景", "账务类型", "biz_type")),
        FieldSpec("order_id", ("订单号", "订单编号", "订单ID", "交易单号", "order_id", "order_no")),
        FieldSpec("amount", ("金额", "收支金额", "入账金额", "支出金额", "交易金额", "amount"), kind="amount", required=True),
        FieldSpec("currency", ("币种", "currency")),
        FieldSpec("direction", ("收支方向", "方向", "收入支出", "direction")),
        FieldSpec("balance", ("账户余额", "余额", "balance"), kind="amount"),
        FieldSpec("remark", ("备注", "说明", "摘要", "remark", "memo")),
    ),
    "ad_spend": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("stat_date", ("日期", "统计日期", "数据日期", "投放日期", "date", "stat_date", "day"), kind="datetime", required=True),
        FieldSpec("platform", ("平台", "投放平台", "广告平台", "platform")),
        FieldSpec("campaign_id", ("计划ID", "广告计划ID", "campaign_id", "plan_id")),
        FieldSpec("campaign_name", ("计划名称", "广告计划", "广告计划名称", "campaign_name", "plan_name")),
        FieldSpec("ad_group_id", ("单元ID", "广告组ID", "ad_group_id", "unit_id")),
        FieldSpec("ad_group_name", ("单元名称", "广告组名称", "ad_group_name", "unit_name")),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "标题", "商品标题", "product_name", "goods_name", "item_name")),
        FieldSpec("impressions", ("展现量", "曝光量", "展示量", "impressions", "show"), kind="amount"),
        FieldSpec("clicks", ("点击量", "点击数", "clicks", "click"), kind="amount"),
        FieldSpec("spend_amount", ("消耗", "花费", "广告花费", "投放消耗", "spend_amount", "cost"), kind="amount", required=True),
        FieldSpec("payment_amount", ("成交金额", "支付金额", "转化金额", "GMV", "gmv", "payment_amount"), kind="amount"),
        FieldSpec("orders", ("成交订单数", "支付订单数", "订单数", "orders"), kind="amount"),
        FieldSpec("roi", ("ROI", "投产比", "roi", "roas"), kind="amount"),
    ),
    "audience_insights": (
        FieldSpec("insight_id", ("洞察ID", "记录ID", "insight_id", "row_id")),
        FieldSpec("product_id", ("商品ID", "商品编号", "商品id", "product_id", "spu_id", "goods_id")),
        FieldSpec("product_name", ("商品名称", "商品名", "标题", "product_name", "goods_name", "item_name")),
        FieldSpec("sku_id", ("SKU ID", "规格ID", "sku_id")),
        FieldSpec("category", ("类目", "分类", "商品类目", "category")),
        FieldSpec("dimension", ("维度", "画像维度", "人群维度", "分析维度", "dimension")),
        FieldSpec("segment_label", ("人群标签", "标签", "分组", "分层", "客群", "segment", "segment_label")),
        FieldSpec("visitor_count", ("访客数", "访问人数", "浏览人数", "uv", "visitor_count", "visitors"), kind="amount"),
        FieldSpec("add_to_cart_count", ("加购人数", "加购数", "加购件数", "add_to_cart", "cart_count"), kind="amount"),
        FieldSpec("order_count", ("成交订单数", "下单人数", "支付订单数", "订单数", "order_count"), kind="amount"),
        FieldSpec("payment_amount", ("成交金额", "支付金额", "付款金额", "gmv", "payment_amount"), kind="amount"),
        FieldSpec("conversion_rate", ("转化率", "支付转化率", "成交转化率", "conversion_rate", "cvr"), kind="amount"),
        FieldSpec("refund_rate", ("退款率", "售后率", "refund_rate"), kind="amount"),
        FieldSpec("active_hour", ("活跃时段", "高峰时段", "访问时段", "下单时段", "active_hour")),
        FieldSpec("region", ("地域", "地区", "省份", "城市", "region", "city", "province")),
        FieldSpec("gender", ("性别", "gender")),
        FieldSpec("age_group", ("年龄", "年龄段", "age", "age_group")),
        FieldSpec("consumption_level", ("消费层级", "消费力", "价格偏好", "consumption_level")),
    ),
    "ai_reports": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("report_id", ("报告ID", "report_id")),
        FieldSpec("report_type", ("报告类型", "分析类型", "report_type", "type"), required=True),
        FieldSpec("report_title", ("报告标题", "标题", "report_title", "title")),
        FieldSpec("report_date", ("报告日期", "生成日期", "日期", "report_date"), kind="datetime"),
        FieldSpec("content", ("报告内容", "分析内容", "正文", "content", "markdown")),
        FieldSpec("metrics_json", ("指标JSON", "指标", "metrics_json", "metrics")),
        FieldSpec("warnings_json", ("告警JSON", "告警", "warnings_json", "warnings")),
    ),
    "collection_tasks": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("collection_task_id", ("采集任务ID", "任务ID", "collection_task_id", "task_id"), required=True),
        FieldSpec("task_name", ("任务名称", "采集任务", "task_name")),
        FieldSpec("status", ("任务状态", "状态", "status")),
        FieldSpec("started_at", ("开始时间", "启动时间", "started_at", "start_time"), kind="datetime"),
        FieldSpec("finished_at", ("结束时间", "完成时间", "finished_at", "finish_time"), kind="datetime"),
        FieldSpec("source_type", ("来源类型", "采集来源", "source_type")),
    ),
    "collection_task_items": (
        FieldSpec("shop_name_snapshot", ("店铺名称", "店铺名", "小店名称", "shop_name", "shop")),
        FieldSpec("collection_task_id", ("采集任务ID", "任务ID", "collection_task_id", "task_id"), required=True),
        FieldSpec("item_id", ("采集项ID", "明细ID", "item_id"), required=True),
        FieldSpec("table_name", ("表名", "数据表", "table_name")),
        FieldSpec("item_status", ("采集项状态", "明细状态", "状态", "item_status")),
        FieldSpec("error_message", ("错误信息", "失败原因", "error_message")),
    ),
}


HEURISTIC_TOKENS: dict[str, tuple[tuple[str, ...], ...]] = {
    "order_id": (("订单", "号"), ("订单", "id"), ("交易", "号"), ("order", "id"), ("order", "no")),
    "buyer_id": (("买家", "id"), ("用户", "id"), ("customer", "id")),
    "status": (("状态",), ("status",)),
    "order_created_at": (("下单", "时间"), ("创建", "时间"), ("create", "time")),
    "paid_at": (("支付", "时间"), ("付款", "时间"), ("paid", "time")),
    "payment_amount": (("实付",), ("支付", "金额"), ("订单", "金额"), ("成交", "金额"), ("paid", "amount")),
    "shipping_amount": (("运费",), ("邮费",), ("shipping",)),
    "discount_amount": (("优惠", "金额"), ("折扣", "金额"), ("discount",)),
    "refund_amount": (("退款", "金额"), ("售后", "金额"), ("refund", "amount")),
    "product_id": (("商品", "id"), ("商品", "编号"), ("product", "id"), ("goods", "id")),
    "product_name": (("商品", "名称"), ("商品", "名"), ("goods", "name"), ("product", "name")),
    "sku_id": (("sku", "id"), ("规格", "id")),
    "sku_name": (("规格",), ("sku",)),
    "quantity": (("数量",), ("件数",), ("quantity",), ("qty",)),
    "item_amount": (("商品", "金额"), ("明细", "金额"), ("小计",), ("goods", "amount")),
    "refund_id": (("售后", "单号"), ("退款", "单号"), ("refund", "id"), ("after", "sale", "id")),
    "refund_status": (("售后", "状态"), ("退款", "状态"), ("refund", "status")),
    "refund_created_at": (("申请", "时间"), ("created", "at")),
    "refund_completed_at": (("完成", "时间"), ("completed", "at")),
    "category": (("类目",), ("分类",), ("category",)),
    "price": (("价格",), ("售价",), ("price",)),
    "sku_price": (("sku", "价格"), ("规格", "价格"), ("售价",)),
    "stock": (("库存",), ("stock",)),
    "barcode": (("条码",), ("barcode",)),
    "reason": (("原因",), ("reason",)),
    "currency": (("币种",), ("currency",)),
    "review_id": (("评价", "id"), ("评论", "id"), ("comment", "id")),
    "rating": (("评分",), ("星级",), ("rating",), ("score",)),
    "review_content": (("评价", "内容"), ("评论", "内容"), ("买家", "评价"), ("content",)),
    "review_created_at": (("评价", "时间"), ("评论", "时间"), ("comment", "time")),
    "reply_content": (("回复", "内容"), ("商家", "回复"), ("reply",)),
    "reply_created_at": (("回复", "时间"), ("reply", "time")),
    "is_positive": (("好评",), ("情感",), ("sentiment",)),
    "stat_date": (("统计", "日期"), ("数据", "日期"), ("日期",), ("date",), ("day",)),
    "view_count": (("浏览量",), ("访问", "次数"), ("pv",), ("view", "count")),
    "exposure_user_count": (("商品", "曝光", "人数"), ("曝光", "人数"), ("exposure", "user"), ("impression", "user")),
    "click_user_count": (("商品", "点击", "人数"), ("点击", "人数"), ("click", "user")),
    "click_count": (("商品", "点击", "次数"), ("点击", "次数"), ("click", "count")),
    "order_amount": (("下单", "金额"), ("提交", "订单", "金额"), ("order", "amount")),
    "order_submit_count": (("下单", "订单", "数"), ("下单", "单量"), ("提交", "订单", "数"), ("order", "submit", "count")),
    "order_user_count": (("下单", "人数"), ("下单", "用户"), ("order", "user", "count")),
    "buyer_count": (("买家", "数"), ("成交", "人数"), ("buyer", "count")),
    "sold_quantity": (("成交", "件数"), ("支付", "件数"), ("sold", "quantity")),
    "click_conversion_rate": (("点击", "成交", "率"), ("click", "conversion")),
    "source_name": (("流量", "来源"), ("来源", "名称"), ("渠道",), ("入口",), ("traffic", "source")),
    "source_type": (("来源", "类型"), ("渠道", "类型"), ("source", "type")),
    "flow_id": (("流水", "号"), ("transaction", "id")),
    "flow_date": (("入账", "时间"), ("发生", "时间"), ("交易", "时间"), ("流水", "时间")),
    "flow_type": (("收支", "类型"), ("流水", "类型"), ("资金", "类型")),
    "biz_type": (("业务", "类型"), ("业务", "场景"), ("账务", "类型")),
    "amount": (("金额",), ("收支", "金额"), ("入账", "金额"), ("支出", "金额")),
    "direction": (("收支", "方向"), ("方向",), ("收入", "支出")),
    "balance": (("余额",), ("balance",)),
    "remark": (("备注",), ("说明",), ("摘要",), ("remark",)),
    "platform": (("平台",), ("投放", "平台"), ("广告", "平台")),
    "campaign_id": (("计划", "id"), ("campaign", "id"), ("plan", "id")),
    "campaign_name": (("计划", "名称"), ("广告", "计划"), ("campaign", "name"), ("plan", "name")),
    "ad_group_id": (("单元", "id"), ("广告组", "id"), ("ad", "group", "id")),
    "ad_group_name": (("单元", "名称"), ("广告组", "名称"), ("ad", "group", "name")),
    "impressions": (("展现",), ("曝光",), ("展示",), ("impressions",)),
    "clicks": (("点击",), ("click",)),
    "spend_amount": (("消耗",), ("花费",), ("广告", "花费"), ("cost",), ("spend",)),
    "orders": (("订单", "数"), ("orders",)),
    "roi": (("roi",), ("投产比",), ("roas",)),
    "report_id": (("报告", "id"), ("report", "id")),
    "report_type": (("报告", "类型"), ("分析", "类型"), ("report", "type")),
    "report_title": (("报告", "标题"), ("标题",), ("title",)),
    "report_date": (("报告", "日期"), ("生成", "日期"), ("report", "date")),
    "content": (("报告", "内容"), ("分析", "内容"), ("正文",), ("content",)),
    "metrics_json": (("指标",), ("metrics",)),
    "warnings_json": (("告警",), ("warnings",)),
    "collection_task_id": (("采集", "任务", "id"), ("任务", "id"), ("task", "id")),
    "task_name": (("任务", "名称"), ("采集", "任务")),
    "started_at": (("开始", "时间"), ("启动", "时间"), ("start", "time")),
    "finished_at": (("结束", "时间"), ("完成", "时间"), ("finish", "time")),
    "item_id": (("采集项", "id"), ("明细", "id"), ("item", "id")),
    "table_name": (("表名",), ("数据表",), ("table", "name")),
    "item_status": (("采集项", "状态"), ("明细", "状态"), ("item", "status")),
    "error_message": (("错误", "信息"), ("失败", "原因"), ("error", "message")),
    "dimension": (("维度",), ("dimension",)),
    "segment_label": (("人群", "标签"), ("客群",), ("分组",), ("segment",)),
    "visitor_count": (("访客",), ("访问", "人数"), ("visitor",), ("uv",)),
    "add_to_cart_count": (("加购",), ("cart",)),
    "order_count": (("成交", "订单"), ("支付", "订单"), ("order", "count")),
    "conversion_rate": (("转化率",), ("conversion",), ("cvr",)),
    "refund_rate": (("退款率",), ("售后率",), ("refund", "rate")),
    "active_hour": (("活跃", "时段"), ("访问", "时段"), ("下单", "时段")),
    "region": (("地域",), ("地区",), ("省份",), ("城市",), ("region",)),
    "gender": (("性别",), ("gender",)),
    "age_group": (("年龄",), ("age",)),
    "consumption_level": (("消费", "层级"), ("消费力",), ("价格", "偏好")),
}


FILE_HINTS: dict[str, tuple[str, ...]] = {
    "orders": ("order", "订单", "交易"),
    "order_items": ("item", "明细", "子订单"),
    "products": ("product", "goods", "商品"),
    "product_skus": ("sku", "规格", "商品规格", "库存"),
    "refunds": ("refund", "after", "售后", "退款"),
    "reviews": ("review", "comment", "评价", "评论"),
    "shop_daily": ("shop_daily", "店铺日", "店铺日报", "店铺数据", "经营概览"),
    "product_daily": ("product_daily", "商品日", "商品日报", "商品数据", "商品分析"),
    "traffic_sources": ("traffic", "source", "流量", "来源", "渠道"),
    "fund_flows": ("fund", "flow", "finance", "资金", "流水", "账单", "收支"),
    "ad_spend": ("ad", "ads", "campaign", "广告", "投放", "消耗", "推广"),
    "audience_insights": ("compass", "audience", "insight", "traffic", "罗盘", "人群", "画像", "流量"),
    "ai_reports": ("ai", "report", "报告", "分析报告"),
    "collection_tasks": ("collection_task", "采集任务", "任务"),
    "collection_task_items": ("collection_item", "采集明细", "任务明细"),
}


FIELD_SPECS_BY_TABLE: dict[str, dict[str, FieldSpec]] = {
    table: {spec.name: spec for spec in specs}
    for table, specs in FIELD_SPECS.items()
}

COLUMN_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    table: tuple(spec.name for spec in specs if spec.required)
    for table, specs in FIELD_SPECS.items()
}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    **COLUMN_REQUIRED_FIELDS,
    "audience_insights": ("dimension", "segment_label"),
}

FIELDS_BY_KIND: dict[FieldKind, dict[str, tuple[str, ...]]] = {
    kind: {
        table: tuple(spec.name for spec in specs if spec.kind == kind)
        for table, specs in FIELD_SPECS.items()
    }
    for kind in FIELD_KINDS
}


def specs_for_table(table: str) -> tuple[FieldSpec, ...]:
    return FIELD_SPECS[table]


def spec_for_field(table: str, field_name: str) -> FieldSpec | None:
    return FIELD_SPECS_BY_TABLE.get(table, {}).get(field_name)


def required_fields_for_table(table: str) -> tuple[str, ...]:
    return REQUIRED_FIELDS.get(table, ())


def required_columns_for_table(table: str) -> tuple[str, ...]:
    return COLUMN_REQUIRED_FIELDS.get(table, ())


def kind_for_field(table: str, field_name: str) -> FieldKind:
    spec = spec_for_field(table, field_name)
    if spec is not None:
        return spec.kind
    return FIELD_KIND_TEXT


def fields_for_kind(table: str, kind: FieldKind) -> tuple[str, ...]:
    return FIELDS_BY_KIND.get(kind, {}).get(table, ())


def table_for_export_type(export_type: str) -> str:
    return EXPORT_TYPE_TO_TABLE.get(export_type, export_type)
