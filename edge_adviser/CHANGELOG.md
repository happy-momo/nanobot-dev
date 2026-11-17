# EdgeAdviser — 更新记录 / Changelog

本埋记录 EdgeAdviser（边缘设备智能顾问）在 nanobot 仓库中的新增与更新。

---

## 0.2.0 — 2026-11-17（确定性可信度加固 + 记忆升级）

针对工业可解释性对 0.1.0 的事实脊椎（AnalyticsSnapshot）做的**确定性可信度加固**。不动 nanobot 内核，全部为 edge_adviser 内新增/增强。

### 核心问题（本轮修复）

工业现场的数据**不可信时，系统必须说"不可信"，而不是趁数据稀疏时最有信心地报"正常"**。上一版对空/稀疏/断连/过期数据存在假阳性风险，本版把"数据是否可信"从 LLM 的自制力移到**确定性层**强制判定。

### 新增内容

| 模块 | 内容 | 说明 |
|---|---|---|
| `config/model.py` | `AssetState` / `TrustLevel` 枚举、`AlertLevel` 增 `INSUFFICIENT`/`UNKNOWN`、`MetaData`/`RunLogEntry` dataclass | 数据状态、信任等级、可信度徽章、审计账本的数据契约 |
| `analytics/engine.py` | `MetricSpec.min_samples` 数据量下限；覆盖率/实际跨度计算；空度量改判 `UNKNOWN`；`overall` 聚合识别稀疏/全空；`empty_snapshot` | **N1/N2/N3**：数据不足不再报"正常" |
| `analytics/gates.py`（新） | 数据新鲜度门禁、计划停机判定、连续空窗→`offline` 状态机、`trust_from_state` | **N4/P1**：断连/停机可确定性感知 |
| `core/runlog.py` + `AssetStore.runlog_path` | append-only `runlog.jsonl` 审计账本 | **P2/N5**：缺数据/异常日可追溯 |
| `report/badge.py`（新） | 机器生成的可信度徽章 front-matter | **P3**：数据范围由确定性层证明，模型不得改写 |
| `learn/learner.py` | learn 输入可信度闸门（off/soft/strict） | **N7**：坏数据不写回 MEMORY.md |
| `core/scheduler.py` | `run_once` 空窗归档 offline 快照；`Supervisor.start` 零快照告警；`default_regime` 注入 | **N4/P4/N9**：空窗不再静默、regime 不再硬编码 |
| `core/memory_service.py`（新） | 包装 nanobot MemoryStore 的版本化读写、轻量关键词索引、claim 冲突/陈旧账本 | **N6/N8**：按相关性检索，坏知识可标记 |
| `qa/engine.py` | `ask` 快照门禁，无有效快照拒绝驱动模型 | **P1**：无数据不猜 |
| `tests/*` | 新增 19 个确定性离线测试 | 覆盖所有可信度判定 |

### 关键行为变化（0.1.0 → 0.2.0）

- **空/稀疏窗口**：`n=0→UNKNOWN`、`n<min_samples→INSUFFICIENT`，快照带 `status/trust/coverage`，报告报"数据不足"而非"正常"。
- **断电/断连**：连续 3 个空窗口→`offline`，可区分计划停机（`downtime_windows`）。
- **消费侧门禁**：`ask/monitor/report` 无新鲜快照（过 `data_freshness_s`）→ 确定性拒绝，不调模型。
- **审计**：每次 run/报告/学习都写 `runlog.jsonl`，缺失日可对账。
- **记忆**：默认仍走"最近优先"检索（`memory_index=off`）；开启 `keywords` 后按相关性检索，冲突/陈旧知识被标记。
- **N9**：调度层改用 `config.analysis_regime` 注入，不再写死 `degrade`。

### 向后兼容

所有新配置项缺省值与旧行为一致（`min_samples=0`、`data_freshness_s=3600`、`memory_index=off` 等），旧 `config.sample.json` 与现有 7 个测试均可直接运行。新增字段均进 `to_dict()` 且有空值兜底。

### 已验证

- `26 passed`（原 7 个确定性测试 + 新增 19 个）离线全绿。
- `edge-adviser run`：快照带 `status=healthy/trust=full/coverage=1.0/regime=clean`；报告含机器生成 DATA-SCOPE front-matter。

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