# 字段清单

等待真实微信小店导出文件后继续校准。当前先提供标准字段骨架和常见别名，真实字段以导出样例为准。

## 订单字段

- 订单号 / 订单编号 / 交易单号 -> `order_id`
- 下单时间 / 创建时间 -> `order_created_at`
- 支付金额 / 实付金额 / GMV -> `payment_amount`
- 订单状态 -> `status`

## 售后/退款字段

- 售后单号 / 退款单号 -> `refund_id`
- 订单号 -> `order_id`
- 退款金额 / 售后金额 -> `refund_amount`
- 退款原因 / 售后原因 -> `reason`

## 商品字段

- 商品 ID / 商品编号 -> `product_id`
- 商品名称 / 标题 -> `product_name`
- SKU / 规格 -> `sku_name`
- 售价 / 价格 -> `price`
- 库存 -> `stock`

## 罗盘/人群字段

- 画像维度 / 人群维度 / 分析维度 -> `dimension`
- 人群标签 / 客群 / 分组 -> `segment_label`
- 访客数 / UV / 浏览人数 -> `visitor_count`
- 加购数 / 加购人数 -> `add_to_cart_count`
- 成交订单数 / 支付订单数 -> `order_count`
- 转化率 / 支付转化率 -> `conversion_rate`
- 退款率 / 售后率 -> `refund_rate`
- 地域 / 性别 / 年龄段 / 消费层级 / 活跃时段 -> `region` / `gender` / `age_group` / `consumption_level` / `active_hour`
