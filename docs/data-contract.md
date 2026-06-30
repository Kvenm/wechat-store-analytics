# 数据契约

本文档描述本地 Excel/CSV 导入、SQLite 入库、指标计算和报告生成的第一版契约。V1 定位为本地运行、人工介入、导出文件驱动，目标规模为 5 个微信小店。真实微信小店导出字段按样例持续校准，因此当前实现采取“核心标准字段 + 原始 JSON + warning + 可扩展字段映射”的方式，不静默丢弃不确定字段。

图表解析和 OCR 是 V2 必做能力。V1 只在采集产物 metadata 中预留来源类型和人工复核状态，不执行图片识别或图表反解。

## 目录约定

| 路径 | 用途 |
|---|---|
| `data/raw/` | 原始导出文件目录。本项目按用户要求本地明文保存，不应提交或上传 |
| `data/processed/wechat_store.sqlite` | 默认 SQLite 数据库 |
| `data/processed/standard_tables/` | 可选标准 CSV 输出目录 |
| `data/reports/` | Markdown 和 CSV 报告输出目录 |

## 输入文件

## 数据来源类型

采集和导入 metadata 统一使用 `source_type` / `source_kind` 描述数据来源：

| 类型 | 版本 | 说明 |
|---|---|---|
| `export_file` | V1 | 微信小店后台导出的 Excel/CSV，是第一版唯一正式数据源 |
| `api_pull` | V1 API 同步预留 | 后端服务调用微信小店只读 API 后归档的 JSON/JSONL；当前仅有 mock connector，不连接真实微信 |
| `web_table` | V1 兜底预留 | 页面表格读取，仅在导出不可用时作为后备 |
| `chart_image` | V2 | 图表截图或页面图表图片，第二版解析 |
| `ocr` | V2 | OCR 识别结果，第二版解析 |

V1 的导入层优先使用采集 manifest。导入会在 `--source-dir` 下、直接父级和祖父级任务目录查找 `artifacts-manifest.json`，并按 artifact 的 `saved_path` 绝对路径、相对路径、路径后缀、文件名和 `original_filename` 匹配源文件。

manifest 存在时，默认只导入 manifest 中 `status=completed` 且 `source_kind/source_type` 为 `export_file` 的 artifact；未登记文件、非导出文件、未完成 artifact、店铺/任务不一致、sha256 或文件大小不一致的文件都会跳过，不能再回退为普通目录猜表。manifest 不存在时，才使用旧的目录遍历和启发式猜表导入。

`api_pull` 不走 Excel/CSV 导入器。API 同步应先由后端 connector 归档原始响应文件，再写入 `sync_runs`、`sync_run_items`、`raw_api_responses` 等同步元数据表；再由专门 mapper 把只读 API 数据写入标准业务表。当前离线 mock 已实现商品、订单、售后退款、资金流水 endpoint 到 `products` / `product_skus` / `orders` / `order_items` / `refunds` / `fund_flows` 的 mapper，但仍不连接真实微信接口。Codex、MCP 和 AI 不直接调用微信 API，也不读取或保存 token、cookie、AppSecret、Authorization header。

标准表识别优先级为：

1. manifest `table_hint`
2. manifest `export_type` 到标准表的映射
3. 文件名、sheet 名和表头启发式猜表

`table_hint` 和 `export_type` 是强候选，但不是无条件覆盖。若提示表与表头强冲突，会写入 `table_hint_conflict` 并跳过该 sheet；若表头证据较弱但没有明显冲突，会导入并写入 `low_confidence_table_hint` 供人工复核。file-level `table_hint` 会应用到工作簿中的每个 sheet，因此多 sheet 文件会逐 sheet 检查冲突。

导入 CLI 支持：

- `.xlsx`
- `.xls`
- `.csv`

Excel 文件会逐个 sheet 读取。每个文件或 sheet 会根据文件名、sheet 名和表头启发式识别为：

- `orders`
- `order_items`
- `products`
- `product_skus`
- `refunds`
- `reviews`
- `shop_daily`
- `product_daily`
- `traffic_sources`
- `fund_flows`
- `ad_spend`
- `audience_insights`

采集任务和 AI 报告元数据表不参与普通 Excel/CSV 自动猜表导入；应通过专用任务/报告写入路径保存。

## API 同步元数据

API 同步链路当前只实现离线 `mock_wechat` connector，用来验证后续真实微信 API 接入的归档、审计框架和业务 mapper。真实微信 API 接入前必须先完成授权、token、额度、回调验签和隐私边界设计。

| 表 | 用途 |
|---|---|
| `sync_runs` | 一次 API pull 的任务主表，记录店铺、周期、connector、状态和脱敏参数 |
| `sync_run_items` | endpoint/page 级执行结果，记录 endpoint、页码、rid/request_id、状态、row_count 和 raw response 关联 |
| `raw_api_responses` | 原始 API 响应归档索引，只存路径、sha256、size、record_count、schema_version 和脱敏 metadata |

`raw_api_responses.raw_json` 只能保存内容类型、fixture/schema 版本、是否已脱敏等元信息；完整响应体保存到私有归档文件。归档文件和 SQLite 中都不允许出现 `access_token`、`refresh_token`、`authorization`、`cookie`、`appsecret`、`secret`、`session` 等敏感字段。

无法识别时不会导入该 sheet，会写入 `unknown_table` warning。manifest 参与识别时也会写入 warning，显式标记已使用、无效、缺失、匹配不到、被忽略或与启发式猜表不一致的情况。

## 字段映射

默认字段映射位于 `src/shared/field_mapping.py`；标准表、字段类型、必填字段和导出类型映射逐步集中到 `src/shared/table_catalog.py`。真实字段不确定时，可通过 `--field-map` 传入 JSON 文件扩展别名。

示例：

```json
{
  "orders": {
    "order_id": ["交易订单编号"],
    "payment_amount": ["订单实收"]
  },
  "refunds": {
    "refund_amount": ["实际退还金额"]
  }
}
```

自定义映射只追加别名，不覆盖内置映射。字段不一致时，V1 允许人工确认和补充映射后重跑导入。

## 本地数据保存策略

- 原始导出文件、SQLite 和报告全部保存在本机。
- 按用户要求，V1 不做强制脱敏入库；`raw_json` 可以保留原始导出行，用于字段追溯和人工复核。
- 日志和错误信息不应全量打印原始订单行，避免排查输出过大或泄露到外部工具。
- AI 分析优先消费聚合指标；除非明确需要单笔异常复核，不默认把完整订单明细塞进 prompt。
- 若后续接入云端模型或外部存储，应重新评估脱敏和最小化上传策略。

## 标准表字段

### shops

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PRIMARY KEY | 店铺 ID 或本地稳定标识 |
| `name` | TEXT | 店铺名称，可缺失 |
| `raw_json` | TEXT | 原始补充信息 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |

### orders

订单后台“全部导出”文件按以下方式处理：

- `orders` 表只保存订单级标准分析字段。
- 如果同一订单在导出文件中因多个商品出现多行，`orders` 会按 `shop_id + order_id` 合并去重，避免订单量重复计算。
- 每一行原始导出内容都会保留在对应标准记录的 `raw_json` 中，便于本地核对；报告和 AI payload 不直接展示原始订单行。
- 如果导出表同时包含商品字段，导入器会同步拆出 `order_items`。
- 如果导出表同时包含退款/售后字段且有退款信号，导入器会同步拆出 `refunds`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID |
| `task_id` | TEXT | 采集或导入任务 ID |
| `order_id` | TEXT | 订单号，缺失时用行内容生成本地指纹 |
| `buyer_id` | TEXT | 买家/用户标识 |
| `status` | TEXT | 订单状态 |
| `order_created_at` | TEXT | 下单/创建时间 |
| `paid_at` | TEXT | 支付时间 |
| `payment_amount` | REAL | 支付金额/GMV |
| `shipping_amount` | REAL | 运费 |
| `discount_amount` | REAL | 优惠金额 |
| `refund_amount` | REAL | 订单表内退款金额 |
| `currency` | TEXT | 币种，默认 `CNY` |
| `source_file` | TEXT | 来源文件 |
| `source_sheet` | TEXT | 来源 sheet |
| `source_row_number` | INTEGER | 来源行号 |
| `row_fingerprint` | TEXT UNIQUE | 幂等导入指纹 |
| `raw_json` | TEXT | 原始行 JSON |

### order_items

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID |
| `task_id` | TEXT | 采集或导入任务 ID |
| `order_item_id` | TEXT | 明细/子订单 ID，缺失时生成本地指纹 |
| `order_id` | TEXT | 订单号 |
| `product_id` | TEXT | 商品 ID，缺失但有商品名时生成本地指纹 |
| `product_name` | TEXT | 商品名称 |
| `sku_id` | TEXT | SKU ID |
| `sku_name` | TEXT | SKU/规格名称 |
| `quantity` | REAL | 数量，缺失默认 `1` |
| `item_amount` | REAL | 商品明细实付金额 |
| `refund_amount` | REAL | 商品明细退款金额 |
| `source_file/source_sheet/source_row_number` | TEXT/INTEGER | 来源定位 |
| `row_fingerprint` | TEXT UNIQUE | 幂等导入指纹 |
| `raw_json` | TEXT | 原始行 JSON |

### products

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID |
| `task_id` | TEXT | 采集或导入任务 ID |
| `product_id` | TEXT | 商品 ID，缺失但有商品名时生成本地指纹 |
| `product_name` | TEXT | 商品名称 |
| `sku_id` | TEXT | SKU ID |
| `sku_name` | TEXT | SKU/规格名称 |
| `category` | TEXT | 商品类目 |
| `status` | TEXT | 商品状态 |
| `price` | REAL | 售价 |
| `stock` | REAL | 库存 |
| `source_file/source_sheet/source_row_number` | TEXT/INTEGER | 来源定位 |
| `row_fingerprint` | TEXT UNIQUE | 幂等导入指纹 |
| `raw_json` | TEXT | 原始行 JSON |

### refunds

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID |
| `task_id` | TEXT | 采集或导入任务 ID |
| `refund_id` | TEXT | 售后/退款单 ID，缺失时生成本地指纹 |
| `order_id` | TEXT | 订单号 |
| `product_id` | TEXT | 商品 ID |
| `product_name` | TEXT | 商品名称 |
| `sku_id` | TEXT | SKU ID |
| `refund_status` | TEXT | 售后/退款状态 |
| `refund_amount` | REAL | 退款金额 |
| `refund_created_at` | TEXT | 售后申请时间 |
| `refund_completed_at` | TEXT | 退款完成时间 |
| `reason` | TEXT | 退款原因 |
| `source_file/source_sheet/source_row_number` | TEXT/INTEGER | 来源定位 |
| `row_fingerprint` | TEXT UNIQUE | 幂等导入指纹 |
| `raw_json` | TEXT | 原始行 JSON |

### shop_daily

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID |
| `task_id` | TEXT | 采集或导入任务 ID |
| `stat_date` | TEXT | 统计日期 |
| `visitor_count` | REAL | 店铺访客数；不等同于商品曝光人数 |
| `view_count` | REAL | 店铺浏览量；不等同于商品曝光人数 |
| `exposure_user_count` | REAL | 商品曝光人数，来自“核心转化概览” |
| `click_user_count` | REAL | 商品点击人数，来自“核心转化概览” |
| `click_count` | REAL | 商品点击次数，来自“核心转化概览” |
| `order_amount` | REAL | 下单金额 |
| `order_submit_count` | REAL | 下单订单数 |
| `order_user_count` | REAL | 下单人数 |
| `order_count` | REAL | 成交订单数 |
| `buyer_count` | REAL | 成交人数/支付买家数 |
| `sold_quantity` | REAL | 成交件数 |
| `payment_amount` | REAL | 成交金额 |
| `refund_amount` | REAL | 成交退款金额 |
| `conversion_rate` | REAL | 店铺访客转化率；真实表无该字段时可缺失 |
| `click_conversion_rate` | REAL | 点击成交率（次数），导入值作为分析兜底 |
| `refund_rate` | REAL | 退款率 |
| `source_file/source_sheet/source_row_number` | TEXT/INTEGER | 来源定位 |
| `row_fingerprint` | TEXT UNIQUE | 幂等导入指纹；店铺日表按店铺和日期生成 |
| `raw_json` | TEXT | 原始行 JSON |

### audience_insights

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID |
| `task_id` | TEXT | 采集或导入任务 ID |
| `insight_id` | TEXT | 人群洞察记录 ID，缺失时生成本地指纹 |
| `product_id` | TEXT | 商品 ID，可缺失 |
| `product_name` | TEXT | 商品名称，可缺失 |
| `sku_id` | TEXT | SKU ID |
| `category` | TEXT | 商品类目 |
| `dimension` | TEXT | 人群维度，例如地域、性别、年龄、消费层级、活跃时段 |
| `segment_label` | TEXT | 人群标签，例如华东、女、25-34、高消费力 |
| `visitor_count` | REAL | 访客数 |
| `add_to_cart_count` | REAL | 加购数/人数 |
| `order_count` | REAL | 成交订单数/人数 |
| `payment_amount` | REAL | 成交金额 |
| `conversion_rate` | REAL | 转化率 |
| `refund_rate` | REAL | 退款率 |
| `active_hour` | TEXT | 活跃时段 |
| `region` | TEXT | 地域 |
| `gender` | TEXT | 性别 |
| `age_group` | TEXT | 年龄段 |
| `consumption_level` | TEXT | 消费层级 |
| `source_file/source_sheet/source_row_number` | TEXT/INTEGER | 来源定位 |
| `row_fingerprint` | TEXT UNIQUE | 幂等导入指纹 |
| `raw_json` | TEXT | 原始行 JSON |

### analysis_runs

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PRIMARY KEY | 分析 run ID |
| `shop_id` | TEXT | 店铺 ID |
| `date_from` | TEXT | 分析开始日期 |
| `date_to` | TEXT | 分析结束日期 |
| `params_json` | TEXT | CLI 参数 |
| `metrics_json` | TEXT | 指标结果 JSON |
| `warnings_json` | TEXT | 数据质量 warnings |
| `created_at` | TEXT | 创建时间 |

### strategy_reports

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PRIMARY KEY | 报告 ID |
| `analysis_run_id` | TEXT | 关联分析 run |
| `shop_id` | TEXT | 店铺 ID |
| `markdown_path` | TEXT | Markdown 报告路径 |
| `summary_csv_path` | TEXT | 摘要 CSV 路径 |
| `product_csv_path` | TEXT | 商品 CSV 路径 |
| `audience_csv_path` | TEXT | 人群洞察 CSV 路径 |
| `warnings_json` | TEXT | 报告携带的 warnings |
| `created_at` | TEXT | 创建时间 |

### collection_tasks

`task-metadata.json` 通过专用 CLI 入库，不参与普通 Excel/CSV 自动猜表导入。`raw_json` 仅保存本机排查 metadata（包含本地路径、店铺名等），不得进入 AI payload 或外部上传。

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID，支持 CLI 覆盖 |
| `shop_name_snapshot` | TEXT | 店铺名称快照，支持 CLI 覆盖 |
| `task_id` | TEXT | collector 任务 ID |
| `collection_task_id` | TEXT | 采集任务 ID，默认等于 `task_id` |
| `task_name` | TEXT | 任务名称或日期范围摘要 |
| `status` | TEXT | 任务状态 |
| `started_at` | TEXT | 任务开始时间 |
| `finished_at` | TEXT | 任务完成时间，来自 `finished_at` 或 `completed_at` |
| `source_type` | TEXT | 固定为 `collector_task_metadata` |
| `source_file` | TEXT | `task-metadata.json` 路径 |
| `row_fingerprint` | TEXT UNIQUE | 幂等入库指纹 |
| `raw_json` | TEXT | 本地 metadata JSON，仅用于排查 |

### collection_task_items

| 字段 | 类型 | 说明 |
|---|---|---|
| `shop_id` | TEXT | 店铺 ID |
| `shop_name_snapshot` | TEXT | 店铺名称快照 |
| `task_id` | TEXT | collector 任务 ID |
| `collection_task_id` | TEXT | 关联采集任务 ID |
| `item_id` | TEXT | 采集项 ID，缺失时按任务、店铺、类型和来源路径生成 |
| `table_name` | TEXT | 采集项对应的标准表或导出类型 |
| `item_status` | TEXT | 采集项状态，例如 `completed`、`failed`、`skipped`、`no_permission` |
| `source_file` | TEXT | 下载文件路径；无下载时回退到 metadata 路径 |
| `source_row_number` | INTEGER | metadata items 序号 |
| `error_message` | TEXT | 采集项错误信息 |
| `row_fingerprint` | TEXT UNIQUE | 幂等入库指纹 |
| `raw_json` | TEXT | 单个采集项 metadata JSON，仅用于排查 |

## CLI 契约

### 导入文件

```bash
python scripts/import/import_files.py \
  --source-dir data/raw/example \
  --shop-id shop_demo \
  --task-id task_20260607
```

可选参数：

| 参数 | 说明 |
|---|---|
| `--db-path` | SQLite 路径，默认 `data/processed/wechat_store.sqlite` |
| `--field-map` | 自定义字段别名 JSON |
| `--standard-dir` | 额外输出标准 CSV 表 |
| `--standard-only` | 只输出标准 CSV，不写 SQLite |

输出为 JSON，包含批次数量、入库数量、标准表路径和 warnings。

### 导入采集任务 metadata

```bash
python scripts/import/import_task_metadata.py \
  --metadata-path data/raw/collect_demo/task-metadata.json
```

可选参数：

| 参数 | 说明 |
|---|---|
| `--db-path` | SQLite 路径，默认 `data/processed/wechat_store.sqlite` |
| `--shop-id` | 覆盖 metadata 中的店铺 ID |
| `--shop-name` | 覆盖 metadata 中的店铺名称快照 |

输出为 JSON，包含 metadata 路径、数据库路径、任务 ID 和入库数量。该脚本只写 SQLite，不启动采集。

### 计算指标

```bash
python scripts/analyze/analyze.py \
  --shop-id shop_demo \
  --from 2026-06-01 \
  --to 2026-06-07
```

可选参数：

| 参数 | 说明 |
|---|---|
| `--db-path` | SQLite 路径，默认 `data/processed/wechat_store.sqlite` |

输出为 JSON，包含 `analysis_run_id`、指标和 warnings。结果同时写入 `analysis_runs`。

### 生成报告

```bash
python scripts/report/report.py \
  --analysis-run-id ar_xxxxxxxxxxxx
```

可选参数：

| 参数 | 说明 |
|---|---|
| `--db-path` | SQLite 路径，默认 `data/processed/wechat_store.sqlite` |
| `--reports-dir` | 报告目录，默认 `data/reports` |

输出为 JSON，包含 Markdown、摘要 CSV、商品 CSV、人群洞察 CSV 路径。结果同时写入 `strategy_reports`。

报告产物包含：

- Markdown 分析报告
- 摘要 CSV
- 商品表现 CSV
- 人群洞察 CSV

## 指标口径

订单指标：

- 总订单量：指定周期内创建的订单数。
- 有效成交订单量：支付金额大于 0，且未命中取消/关闭状态，且未全额退款的订单。
- 退款订单量：指定周期订单中，订单表或退款表存在退款金额的订单数。
- 退款占比：退款订单量 / 有效成交订单量。
- GMV：指定周期订单支付金额合计。
- 净成交额：GMV - 退款金额。
- 客单价：净成交额 / 有效成交订单量。

店铺日概览：

- `shop_daily_metrics`/`overview_daily_metrics` 按 `shop_daily.stat_date` 过滤，汇总店铺日表中的曝光、点击、下单、成交、退款和日均指标。
- 微信小店“核心转化概览”的“商品曝光人数”落到 `exposure_user_count`，不会写入 `visitor_count` 或 `view_count`。
- `click_through_rate` 优先按 `click_user_count / exposure_user_count` 计算；无点击人数但有点击次数时按 `click_count / exposure_user_count` 兜底。
- `click_conversion_rate` 优先按 `order_count / click_count` 计算；无法计算时使用导入的“点击成交率（次数）”日均值。

商品指标：

- 商品成交订单数：包含该商品的有效成交订单数。
- 商品退款订单数：包含该商品且发生退款的订单数。
- 商品退款率：商品退款订单数 / 商品成交订单数。
- 商品成交贡献：商品净成交额 / 全店净成交额。

人群指标：

- 主力客群：有成交、成交金额或转化率的人群标签，按转化率、成交金额、成交数、访客数排序。
- 潜力客群：有访客但未识别成交的人群标签，用于小预算测试。
- 风险客群：退款率不低于 20%，且有一定成交基础的人群标签，用于缩减投放或单独复盘。
- 当前人群洞察按店铺全量已导入数据计算，尚未按订单分析周期过滤。
- 采集类型 `compass` 的导出文件导入后会映射为 `audience_insights` 表。

退款归因：

- 当前版本按订单创建时间筛选周期，退款归因到这些周期内订单。
- 退款表有商品 ID 时按商品聚合。
- 退款表缺商品 ID 时，退款订单关联的所有商品都计为发生退款；退款金额按订单明细金额比例分摊，并写入 warning。

## 缺失字段处理规则

导入阶段不会因为字段缺失直接失败，除非 source-dir 不存在或文件格式不支持。缺失和不确定情况必须写入 warnings。

常见 warning：

| code | 说明 |
|---|---|
| `no_source_files` | 来源目录没有可识别文件 |
| `unknown_table` | 文件或 sheet 无法识别为标准表 |
| `manifest_table_hint_used` | 已使用 `artifacts-manifest.json` 中的有效 `table_hint` |
| `manifest_export_type_used` | `table_hint` 缺失或不可用时，已使用 manifest `export_type` 映射出的标准表 |
| `manifest_table_hint_mismatch` / `manifest_export_type_mismatch` | manifest 提示表与启发式猜表结果不一致，但未达到强冲突阈值，导入优先使用 manifest |
| `table_hint_conflict` | manifest 提示表与表头强冲突，跳过该 sheet，避免误导入 |
| `low_confidence_table_hint` | manifest `table_hint` 对应表头匹配分较低，已导入但需要人工复核 |
| `unsupported_table_hint` | manifest `table_hint` 不是已知标准表，尝试使用 `export_type` 或启发式猜表 |
| `unsupported_export_type` | manifest `export_type` 无法映射到标准表，回退启发式猜表 |
| `missing_manifest_table_hint` | 匹配到 artifact 但缺少 `table_hint/export_type`，回退启发式猜表 |
| `manifest_artifact_not_matched` | 存在 manifest，但源文件未匹配到 artifact |
| `unlisted_source_file_ignored` | manifest 模式下源文件未登记或未匹配，已跳过 |
| `manifest_source_file_ignored` | manifest 模式下源文件无法唯一匹配可导入 artifact，已跳过 |
| `manifest_artifact_ambiguous` | 源文件匹配到多个同分 artifact；若 table hint 一致且高置信则使用第一个，否则跳过 |
| `manifest_no_completed_export_files` | 发现 manifest 但没有可导入的 completed export_file artifact，跳过目录内表格 |
| `invalid_artifact_manifest` | manifest 文件结构不可用 |
| `invalid_manifest_artifact` | 单个 manifest artifact 结构不可用，跳过该 artifact |
| `unsupported_source_kind` | V1 只导入 `export_file` artifact，其他 source kind 跳过 |
| `artifact_not_completed` | artifact 状态不是 `completed`，跳过该 artifact |
| `artifact_shop_mismatch` / `artifact_task_mismatch` | artifact 与本次导入的店铺或任务不一致，跳过该文件 |
| `artifact_size_mismatch` / `artifact_hash_mismatch` | artifact 文件大小或 sha256 与当前文件不一致，跳过该文件 |
| `artifact_file_missing` | artifact 源文件不存在或不可读取，跳过该文件 |
| `missing_required_column` | 表头缺少关键列 |
| `heuristic_field_match` | 字段通过启发式匹配，不是精确别名 |
| `empty_sheet` | 文件或 sheet 无可导入行 |
| `missing_required_value` | 单行缺少关键字段值 |
| `missing_order_created_at` | 指定周期分析时订单缺少创建时间 |
| `invalid_order_created_at` | 创建时间无法解析 |
| `unknown_order_status` | 订单状态未命中已知状态集合 |
| `missing_order_items` | 缺少订单明细，商品指标不可计算 |
| `refund_without_product` | 退款缺少商品 ID，商品退款指标按订单关联估算 |
| `estimated_item_amount` | 订单明细缺少商品实付金额，按商品价格和数量估算 |

## 当前限制

- 尚未接入真实微信小店导出样例，字段别名需要持续校准。
- 已提供罗盘/人群导出的通用 `audience_insights` 表，但真实字段仍需样例校准。
- 本地旧版 SQLite 会在初始化时自动补充新增标准列；重复导入按 `row_fingerprint` 幂等更新。
- 多商品订单部分退款的真实分摊规则待确认。
- 取消订单、售后中订单、跨周期退款的最终业务口径待确认。
