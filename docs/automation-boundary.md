# 自动化边界说明

本项目的本地采集 worker 只负责在用户本机、用户授权登录后的微信小店后台中，按配置触发页面导出并保存下载文件。自动化脚本使用 Playwright persistent profile，默认目录为 `data/browser-profile/`，用于复用本机登录态。

## 明确不会做的事

- 不绕过扫码登录、验证码、风控、人机校验或平台权限控制。
- 不抓取、导出、写入 cookie、token、账号密码或浏览器 profile 内容。
- 不伪造接口请求，不尝试调用未由页面正常触发的敏感后台接口。
- Codex、AI、MCP 不直接调用微信小店开放 API；真实 API 的 access token、AppSecret、回调验签、接口额度和重试逻辑只允许后端服务处理。
- API 同步日志、SQLite、raw response 归档文件不得保存 access token、refresh token、Authorization header、cookie、AppSecret、session 或浏览器 profile 内容。
- 不修改 `data/browser-profile/` 中的登录态文件；仓库只保留 `.gitkeep`。
- 不提交 `data/raw/` 下的真实导出数据。

## 需要人工参与的环节

- 首次打开 `https://store.weixin.qq.com/` 时，如平台要求扫码或校验，需要用户在浏览器中手动完成。
- 微信小店后台页面结构和选择器需要在真实页面中校准，目前采集骨架中的选择器以 `TODO_SELECTOR_*` 标记。
- 店铺切换、日期控件、导出按钮、下载完成状态等关键选择器必须在真实页面确认后写入 `config/export-tasks.json`。

## 当前自动化范围

- CLI 参数解析：`--shop-id`、`--from`、`--to`、`--types`、`--headless`。
- 为每次采集生成 `task_id`。
- 为任务准备 `data/raw/<task_id>/` 下载目录。
- 按 `data/raw/<task_id>/<shop_id>/<export_type>/` 归档本地导出文件；文件名包含 `shop_id`、`export_type`、日期范围和导出时间。
- 通过 Playwright persistent context 使用 `data/browser-profile/`。
- 封装打开微信小店、等待登录、店铺切换、按类型导出、保存下载文件和写入任务元数据的流程骨架。
- 为每次任务写入 `task-metadata.json` 和 `artifacts-manifest.json`。V1 artifact 只登记人工页面导出的 Excel/CSV 文件，`source_kind`/`source_type` 固定为 `export_file`，字段包括 `export_type`、`table_hint`、`shop_id`、`shop_name`、`date_range`、`saved_path`、`original_filename`、`sha256`、`size_bytes`、`status`、`error`、`created_at`。
- `source_kind` 预留 `ocr`、`chart_image`，用于 V2 OCR 或图表类采集产物；V1 不实现 OCR、截图解析或图表结构化。
- API 同步框架当前只有 `mock_wechat` 离线 connector，用于验证 `api_pull` 的归档、审计和幂等写入；它不读取真实环境 token，不连接微信网络。
- `api_pull` 原始响应先保存到私有归档目录，并在 `sync_runs`、`sync_run_items`、`raw_api_responses` 中写入索引；当前离线 mock endpoint 已通过专门 mapper 写入商品、订单、售后退款和资金流水等标准业务表，但仍不连接真实微信接口。

真实采集前必须完成页面选择器校准，并确认所有操作符合微信小店平台规则和账号权限。
