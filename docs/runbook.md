# 运行手册

当前项目已进入本地 V1 优化阶段。V1 定位为“本地运行、人工介入、导出文件驱动、面向 5 个微信小店的经营分析系统”。图表解析和 OCR 是 V2 必做能力，V1 只预留数据来源和解析接口，不进入执行链路。

## 当前状态

- 依赖已安装：`node_modules/`、`.venv/` 和 Playwright Chromium 已存在，不要重复安装。
- 当前不追求无人值守；首次登录、店铺确认、字段映射和异常数据确认都允许人工介入。
- 已完成 `奥尚百货甄选店` 当前店铺的真实 `products` 小范围采集；该导出实际是“核心转化概览”，已按 `shop_daily` 导入。
- 采集选择器仍是逐页校准模式；订单、评价、商品主表、多店切换未完成真实校准。
- OpenAI API 当前只有 dry-run 骨架，不会真实请求。

只有用户明确允许启动真实浏览器、服务或真实 OpenAI 调用后，才执行相关命令。普通导入、分析、报告和静态验证可以在本地执行。

## 后续预计流程

```text
1. 人工准备或导出微信小店后台 Excel/CSV
2. 将原始文件按店铺、月份、批次归档
3. 导入导出文件并确认字段映射
4. 计算指标并生成报告
5. 人工复核异常数据和分析结论
```

## 后续命令草案

依赖已安装，以下命令仅在环境重建时使用：

```bash
npm install
python3 -m pip install -r requirements.txt
npx playwright install chromium
```

准备配置：

```bash
cp config/shops.example.json config/shops.json
cp config/export-tasks.example.json config/export-tasks.json
```

采集配置静态预检（只读配置，输出 JSON，不打开浏览器）：

```bash
npm run collect -- --check-config --shop-id demo-shop --from 2026-06-01 --to 2026-06-07 --types orders,products,compass
```

采集导出，需确认当前店铺和页面 selector 已校准：

```bash
npm run collect -- --shop-id demo-shop --from 2026-06-01 --to 2026-06-07 --types orders,products,compass
```

导入、分析、报告：

```bash
python3 scripts/import/import_files.py --source-dir data/raw/example --shop-id demo-shop --task-id task_20260607
python3 scripts/analyze/analyze.py --shop-id demo-shop --from 2026-06-01 --to 2026-06-07
python3 scripts/report/report.py --analysis-run-id ar_xxxxxxxxxxxx
```

导入采集任务 metadata：

```bash
python3 scripts/import/import_task_metadata.py --metadata-path data/raw/collect_demo/task-metadata.json
python3 scripts/import/import_task_metadata.py --metadata-path data/raw/collect_demo/task-metadata.json --shop-id demo-shop --shop-name "Demo WeChat Store"
```

AI payload 干跑：

```bash
python3 scripts/ai/build_payload.py --metrics-json data/reports/example_metrics.json
python3 scripts/ai/build_payload.py --metrics-json shop_a_metrics.json --metrics-json shop_b_metrics.json --multi-shop --generate-report
python3 scripts/ai/build_payload.py --analysis-run-id ar_xxxxxxxxxxxx --db-path data/processed/wechat_store.sqlite
python3 scripts/ai/build_payload.py --analysis-run-id ar_shop_a --analysis-run-id ar_shop_b --db-path data/processed/wechat_store.sqlite --multi-shop
python3 scripts/ai/build_payload.py --analysis-run-id ar_xxxxxxxxxxxx --db-path data/processed/wechat_store.sqlite --generate-report
```

本地 API 启动草案：

```bash
python3 scripts/api/serve.py --host 127.0.0.1 --port 8000
```

## 注意事项

- 首次访问微信小店后台可能需要扫码。
- 自动化不会绕过验证码或风控。
- 原始导出文件默认放在 `data/raw/`。本项目按用户要求本地明文保存数据，但日志不要打印完整原始订单行。
- 导入时可用 `--shop-name` 写入店铺名称快照，便于多店铺导出和审计。
- `--shop-name` 是导入阶段字段；采集阶段店铺名来自 `config/shops.json` 或页面发现结果。
- 罗盘/人群导出会作为 `audience_insights` 导入，用于人群适配和投放建议。
- V1 只实现 `export_file` 来源；`screenshot`、`chart_image`、`ocr` 是 V2 解析来源，当前只做元数据预留。
- 当前人群洞察按店铺全量已导入数据计算，尚未按订单周期过滤。
- 已新增保守 schema migration 给旧版 SQLite 补缺列；如果旧表存在重复 `row_fingerprint`，仍可能需要人工清理或重建本地库。
- `task-metadata.json` 会保存本机路径和店铺名，仅用于本地排查。
