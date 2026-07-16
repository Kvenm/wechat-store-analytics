# 微信小店数据助手

本项目是一个本地优先的微信小店数据导出、导入、指标计算和经营分析工具。当前主线流程是：在管理台登录微信小店后台，按日期和业务模块导出原始表格，系统逐页校验筛选条件、处理扫码确认、保存运行日志，并将本次成功文件汇总为一个 ZIP 下载。

## 当前状态

已可用：

- 本地 FastAPI 管理台：`/`、`/docs`、`/health`、`/tasks`、`/reports`、`/exports`。
- 微信小店官方 API 同步入口：命令行 `scripts/sync/wechat_api_sync.py` 和管理台接口 `POST /api-sync/runs`，用于服务器定时拉取商品、订单、售后和资金等业务数据。
- 本地导出文件校验：自动识别订单、商品列表、评价、退款/售后、资金流水、投放、人群/罗盘等表。
- 业务化校验结果：展示每个文件/Sheet 被识别成什么表、命中哪些字段、缺哪些关键字段、能支持哪些分析、哪些经营决策仍受限。
- Excel/CSV/zip 导入 SQLite，并保留 `shop_id`、`shop_name`、来源文件、来源行号和 warning。
- 订单、退款、商品、评价、人群/流量、资金、投放等基础指标计算。
- Markdown/CSV 报告输出，并明确数据缺口与决策限制。
- 微信小店“页面表格导出”：已校准商品列表、订单明细、资金流水、交易数据、商品数据三张表和买家人群特征快照。
- 执行前检查后台登录状态；需要微信二次确认时，弹窗会标明当前表格和触发操作。
- 支持取消运行中的导出任务，保留取消前已完成的文件；部分表失败不会丢失其他已成功结果。
- 每次导出生成结构化日志、失败截图和调试 JSON，成功文件可一键下载为汇总 ZIP。页面只导出模式不会导入、分析或生成报告。

仍在校准或预留：

- 微信小店后台页面结构可能调整；导出选择器需依靠真实失败日志持续回归校准。
- 评价、售后和投放等尚未列入当前页面导出白名单，仍可通过已有文件导入链路处理。
- 多店铺自动切换仍需真实账号页面校准。
- 官方 API 同步拉取的是业务查询接口数据，不是后台“导出 Excel/CSV”按钮生成的同款文件。需要文件时，由本系统基于标准表生成 CSV/XLSX/报告。
- OpenAI 调用当前是 dry-run，请求结构和 payload 已准备好，但不会真实请求 API。
- OCR/图表解析属于下一阶段能力，当前只保留数据来源和元数据接口。

## 本地启动

```bash
git clone https://github.com/Kvenm/wechat-store-analytics.git
cd wechat-store-analytics

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

npm install
npx playwright install chromium

python scripts/api/serve.py --host 127.0.0.1 --port 8000
```

启动后打开：

```text
http://127.0.0.1:8000/
```

Swagger 文档：

```text
http://127.0.0.1:8000/docs
```

## Playwright 依赖

只有使用“订单导出 / 页面表格导出”时需要 Playwright；官方 API 同步不需要。

新机器或服务器首次部署时运行：

```bash
bash scripts/setup_playwright_chromium.sh
```

默认使用国内镜像下载浏览器；如需改回官方源：

```bash
PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.playwright.dev bash scripts/setup_playwright_chromium.sh
```

## 本地导出文件测试

1. 从微信小店后台手动导出订单、商品、评价、退款/售后、资金流水或投放数据。
2. 在项目里创建一个目录，例如：

```bash
mkdir -p data/raw/manual-demo
```

3. 把导出的 `.xlsx`、`.csv` 或 `.zip` 放进去。
4. 打开管理台，填写：

```text
source_dir=data/raw/manual-demo
导入类型=自动识别导出文件
```

5. 点击“校验本地导出文件”。确认识别结果、字段匹配和业务能力状态后，再点击“复跑本地导出文件”。

也可以用命令行校验：

```bash
python scripts/import/import_files.py \
  --source-dir data/raw/manual-demo \
  --shop-id demo-shop \
  --shop-name "Demo WeChat Store" \
  --task-id manual-demo-task \
  --expected-types auto \
  --manifest-policy ignore \
  --check-only
```

## 页面表格只导出

先通过管理台 `/login` 打开扫码浏览器并完成登录，等待状态明确显示“已进入小店后台”，再在“页面表格导出”里启动任务。管理台会在创建任务前再次检查真实后台登录壳层；如果仍是二维码页，只提示继续扫码，不会创建失败任务。页面只执行用户明确勾选的 6 个业务组，并展开为 8 个采集目标：商品管理商品列表、订单明细、资金流水、交易数据、店铺数据商品数据三张表，以及电商罗盘买家人群特征页面快照。未传 `targets` 会直接拒绝执行，不会回退扫描其他页面。任务运行期间可取消，扫码弹窗会显示“当前需要验证的表格”和触发操作。

命令行等价入口：

```bash
npm run export:visible -- \
  --shop-id demo-shop \
  --shop-name "Demo WeChat Store" \
  --from 2026-06-01 \
  --to 2026-06-07 \
  --cdp-url http://127.0.0.1:9333 \
  --targets product_list,orders,fund_flows,transactions,product_core_conversion,product_traffic_funnel,product_detail,compass_buyer_profile
```

结果保存在 `data/raw/export_only_*/`：

- `task-metadata.json`：任务、页面和范围语义。
- `artifacts-manifest.json`：下载文件哈希和归档路径。
- `export-results.json`：每个页面的成功、跳过和失败清单。
- `execution-log.jsonl`：任务从登录预检、页面导航、日期筛选、导出控件、扫码到下载结果的结构化时间线。
- `<module>/`：原始下载文件；失败时保留截图和调试 JSON。
- `微信小店数据导出_<shop>_<from>_<to>.zip`：本次所有成功文件的汇总下载包。

失败调试 JSON 会同时保存主页面与 iframe 摘要、可交互控件清单和日期类输入值，便于根据真实页面结构修复选择器。诊断文件默认保留 14 天，服务启动时立即清理一次，之后每 6 小时清理一次；ZIP、Excel、CSV、metadata、结果清单和文件 manifest 永远不在该清理范围内。可通过环境变量调整，并通过 `GET /diagnostics/status` 查看最近一次清理结果：

```bash
WECHAT_STORE_DIAGNOSTIC_RETENTION_DAYS=14
WECHAT_STORE_DIAGNOSTIC_CLEANUP_INTERVAL_SECONDS=21600
```

日期规则：

- 商品管理商品列表没有时间维度，导出当前页面筛选下的商品清单。
- 订单明细、资金流水、交易数据、商品数据和买家人群特征会尝试应用 `--from/--to`。
- 日期控件、查询请求或导出内容无法验证时，该项目会明确失败，不会将全店数据标记为所选周期。
- 资金流水会以页面公布的数据截止日为上限，并校验真实列表请求的起止时间。

## 官方 API 同步

API-only 链路适合放在服务器或定时任务中运行，不依赖 Playwright、浏览器 profile、扫码登录或后台导出文件。它会调用微信小店开放 API，把原始响应归档到本地私有目录，写入 `sync_runs`、`sync_run_items`、`raw_api_responses`，再映射到标准业务表并生成分析报告。

管理台“授权状态”只需要填写微信小店 `AppID` 和 `AppSecret`，系统会自动：

- 获取并安全保存 `access_token`、获取时间和有效期；
- 调用微信官方 `GET /channels/ec/basics/info/get` 获取店铺名称和店铺原始 ID；
- 在 Token 即将过期时于同步前自动刷新。

也可以直接在服务端配置 AppID 和 AppSecret，其余字段由管理台连接流程自动生成：

```bash
WECHAT_STORE_APP_ID=your_wechat_store_appid
WECHAT_STORE_APP_SECRET=your_wechat_store_appsecret
WECHAT_STORE_API_BASE_URL=https://api.weixin.qq.com
WECHAT_STORE_RAW_ARCHIVE_DIR=data/raw/api
```

运行一次同步：

```bash
python scripts/sync/wechat_api_sync.py \
  --from 2026-06-01 \
  --to 2026-06-07 \
  --endpoint products \
  --endpoint orders \
  --endpoint aftersale \
  --endpoint funds
```

只同步不出报告时加 `--skip-report`。

也可以通过管理台接口触发：

```bash
curl -X POST http://127.0.0.1:8000/api-sync/runs \
  -H 'Content-Type: application/json' \
  -d '{"date_from":"2026-06-01","date_to":"2026-06-07","endpoints":["products","orders","aftersale","funds"]}'
```

`access_token`、`authorizer_access_token`、`AppSecret`、cookie、Authorization header 等敏感值不会写入同步元数据或 raw archive。
罗盘数据可追加 `compass_shop`、`compass_product`、`compass_audience`。评价、流量来源和投放表目前仍走本地导出导入；有明确官方 endpoint 后，可用 `reviews:/channels/...`、`traffic_sources:/channels/...`、`ad_spend:/channels/...` 这类自定义 endpoint 先写入对应标准表。

## 配置文件

仓库只提交示例配置，真实配置不会上传：

```bash
cp config/shops.example.json config/shops.json
cp config/export-tasks.example.json config/export-tasks.json
cp config/ai.example.json config/ai.json
```

注意：

- `config/*.json`、`.env.local`、`data/raw/`、`data/processed/`、`data/reports/`、`data/browser-profile/` 都不会提交到 Git。
- 微信扫码登录态保存在 `data/browser-profile/`，每台机器都需要自己登录。
- API-only 同步不使用 `data/browser-profile/`；它只读取 `.env.local` 或 CLI 传入的微信开放 API token。
- SQLite 数据库默认生成在 `data/processed/wechat_store.sqlite`。
- 原始导出文件默认放在 `data/raw/`，只在本地使用。

## 能力边界

- 只采集和分析你有权限访问的微信小店数据。
- 不保存微信账号密码。
- 不绕过验证码、风控或平台权限。
- 默认本地明文保存导出数据；如要上传到云端或发送给外部模型，需要另行做脱敏和合规检查。
- AI payload 会尽量使用聚合指标，不直接发送原始订单大表。

## 常用命令

运行核心回归：

```bash
.venv/bin/python scripts/test/test_manifest_import_regression.py
.venv/bin/python scripts/test/test_admin_ui_config_regression.py
.venv/bin/python scripts/test/test_report_strategy_decisions_regression.py
.venv/bin/python scripts/test/test_analytics_metrics_regression.py
.venv/bin/python scripts/test/test_api_task_persistence_regression.py
.venv/bin/python scripts/test/test_web_login_regression.py
.venv/bin/python scripts/test/test_wechat_api_sync_regression.py
.venv/bin/python scripts/test/test_mock_api_sync_regression.py
.venv/bin/python scripts/test/test_api_product_mapper_regression.py
.venv/bin/python scripts/test/test_api_business_mapper_regression.py
node scripts/test/test_collector_overlay_dismiss_regression.mjs
node scripts/test/test_collector_table_hint_regression.mjs
node scripts/test/test_export_visible_tables_regression.mjs
node scripts/test/test_check_wechat_store_login_regression.mjs
.venv/bin/python scripts/test/test_diagnostic_retention_regression.py
```

启动扫码登录窗口：

```text
http://127.0.0.1:8000/login
```

采集配置静态预检：

```bash
npm run collect -- --check-config --shop-id demo-shop --from 2026-06-01 --to 2026-06-07 --types orders
```

真实网页采集仍需选择器校准，请优先使用本地导出文件链路。

## 文档

- 运行手册：[docs/runbook.md](docs/runbook.md)
- 数据契约：[docs/data-contract.md](docs/data-contract.md)
- 指标定义：[docs/metric-definition.md](docs/metric-definition.md)
- 自动化边界：[docs/automation-boundary.md](docs/automation-boundary.md)
- V1 验收矩阵：[docs/v1-acceptance-matrix.md](docs/v1-acceptance-matrix.md)
