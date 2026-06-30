# 微信小店运营分析自动化

本项目是一个本地优先的微信小店多店铺数据导出、导入、指标计算和经营分析工具。当前最稳定的主线是：把微信小店后台导出的 Excel、CSV 或 zip 文件放到本地，系统自动识别表类型、导入 SQLite、计算指标，并在本地管理台展示分析能力和数据缺口。

## 当前状态

已可用：

- 本地 FastAPI 管理台：`/`、`/docs`、`/health`、`/tasks`、`/reports`、`/exports`。
- 本地导出文件校验：自动识别订单、商品列表、评价、退款/售后、资金流水、投放、人群/罗盘等表。
- 业务化校验结果：展示每个文件/Sheet 被识别成什么表、命中哪些字段、缺哪些关键字段、能支持哪些分析、哪些经营决策仍受限。
- Excel/CSV/zip 导入 SQLite，并保留 `shop_id`、`shop_name`、来源文件、来源行号和 warning。
- 订单、退款、商品、评价、人群/流量、资金、投放等基础指标计算。
- Markdown/CSV 报告输出，并明确数据缺口与决策限制。

仍在校准或预留：

- 真实微信小店网页自动导出只开放已小范围校准的订单路径；商品列表、评价、售后、资金、投放等网页按钮仍需登录后台后继续校准。
- 多店铺自动切换仍需真实账号页面校准。
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
node scripts/test/test_collector_overlay_dismiss_regression.mjs
node scripts/test/test_collector_table_hint_regression.mjs
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
