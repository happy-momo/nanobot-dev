# EdgeAdviser — 更新记录 / Changelog

本埋记录 EdgeAdviser（边缘设备智能顾问）在 nanobot 仓库中的新增与更新。

---

## 0.1.0 — 2026-11-17（初始端到端闭环）

针对已评定的方案实现的首个可运行版本。**不改动 nanobot 内核**，全部为新增代码。

### 新增内容

| 模块 | 内容 | 说明 |
|---|---|---|
| `edge_adviser/DESIGN.md` | 方案/架构设计文档 | 记录"确定性分析+LLM解读"、"双模型自动降级"、"上下文注入"、"历史学习闭环"四大设计决策 |
| `edge_adviser/config/model.py` | 领域模型 | `AssetSpec`、`MetricSpec`（单位/正常/告警/严重区间+连续N采样）、`SourceSpec`、`ProviderSpec`(online/offline/routine)、`AdviserConfig` |
| `edge_adviser/config/loader.py` | 配置加载 | JSON→Dataclass 校验；`ASSET.md` 自动生成 |
| `edge_adviser/core/asset_store.py` | 资产注册表 | 每台设备的 `ASSET.md / MEMORY.md / snapshots/ / reports/` 目录管理 + 反馈记录 |
| `edge_adviser/core/scheduler.py` | Supervisor 调度 | 周期采集→分析→归档；顶深报告；学习任务 |
| `edge_adviser/analytics/*` | 确定性分析引擎 | 统计/分位数/持久化超限/线性趋势/变点检测→`AnalyticsSnapshot`(JSON 事实) |
| `edge_adviser/ingest/*` | 数据源适配 | `SimulatorSource`(漂移+故障注入, MVP) + `RestSource` + MQTT/OPC-UA/Modbus 骨架 |
| `edge_adviser/llm/gateway.py` | nanobot SDK 网关 | 多 Provider 路由+自动降级+超时+防御性 JSON 解析 |
| `edge_adviser/llm/context.py` | 上下文注入 | 资产档案+快照+检索知识+上期摘要, 用 `RuntimeContextBlock` 框定为元数据 |
| `edge_adviser/report/generator.py` | 报告生成/归档 | Markdown 专业报告 + `snapshot.json` 结构化归档, 审计头部 |
| `edge_adviser/learn/*` | 历史学习闭环 | 消费快照+反馈→固化到各设备 MEMORY.md |
| `edge_adviser/qa/engine.py` | 人工问答 | 检索快照+知识回答"当前设备状态" |
| `edge_adviser/cli.py` | 命令行入口 | `setup/run/monitor/report/learn/ask/feedback/serve` |
| `edge_adviser/tests/test_pipeline.py` | 测试 | 7 个离线确定性测试 |
| `config.sample.json` / `nanobot.runtime.json` | 示例配置 | 模拟源 pump1 + online/offline 模型预设 |

### 更新/修复摘要

- **依赖**：安装 nanobot 声明的运行时依赖（`loguru`、`json-repair` 等，来自 `pyproject.toml` 清单）。
- **bug 修复**：`scheduler.start()` 中 SourceSpec 误用为 `.spec` 的 bug 已修复；`analytics` 去掉对 `pandas` 的依赖改用 stdlib 解析时间戳；超限检测修复非数组分支。
- **绝对工作区路径**：`config.sample.json` 的 workspace 改为绝对路径，避免进程 CWD 不同导致数据分流。

### 已验证行为

- `run`：拉取模拟数据→确定性分析→快照归档（`overall_level=warn` 等正确判定）。
- `report`：真实 nanobot SDK 网关成功启动并绑定本地 Ollama preset；模型未启动时**优雅降级**，以结构化 JSON 报错并仍归档审计 `report.md`（含 provider/degraded 头）。
- `serve`：常驻调度循环连续运行，自动完成分析+报告尝试。
- 测试：`7 passed`。

### 下一步（待投入模型后）

- 配置可用模型后，`report` 输出真实专业分析报告；`learn` 固化领域知识。
- 接入真实数据源（MQTT/OPC-UA/Modbus）实现 `DataSource.pull()`。
- 报告渲染升级（HTML/PDF）。