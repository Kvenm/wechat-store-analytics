# 多 Agent 开发边界

本项目按真实多 agent 模式执行，不在单个 agent 内部模拟角色。

## 当前 sub-agent

| Agent | ID | 职责 | 文件边界 |
|---|---|---|---|
| 产品经理 agent | `019ea2a5-922a-7f70-94bb-58e528c2e76b` | MVP、非目标、用户流程、验收标准、报告结构 | 只读，不改文件 |
| 数据架构 agent | `019ea2a6-2e58-76f3-a4c2-91ecc8f4055d` | 技术栈、模块边界、数据模型、接口契约、风险 | 只读，不改文件 |
| 本地采集 worker | `019ea2a8-6df6-70c3-a289-937bbed5637a` | Playwright 采集骨架、配置示例、采集边界文档 | `package.json`、`config/**`、`src/collector/**`、`scripts/collect/**`、`docs/automation-boundary.md` |
| 数据分析 worker | `019ea2a8-f5a6-73b1-a81d-08b72a6f0522` | 导入、SQLite、指标计算、报告生成 | `requirements.txt`、`src/ingestion/**`、`src/warehouse/**`、`src/analytics/**`、`src/reporting/**`、`scripts/import/**`、`scripts/analyze/**`、`scripts/report/**`、`docs/data-contract.md` |
| 静态 reviewer agent | `019ea2b9-1307-7da0-a7cc-369ac74beb98` | 只读检查路径、文档一致性、契约断裂和隐私风险 | 只读，不改文件 |

## 主控职责

- 调度 sub-agent。
- 整合文档。
- 检查文件边界。
- 更新 `PROGRESS.md`。
- 更新 `.codex-handoff/latest.md`。
- 最终总结。

## 本轮约束

- 不启动浏览器。
- 不启动服务。
- 不运行测试。
- 不安装依赖。
- 不写入真实登录态、cookie 或用户隐私数据。
