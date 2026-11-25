# EdgeAdviser — 边缘设备智能顾问 / 端侧数据·分析·报告·问答 Agent

> 基于 nanobot 内核之上构建的边缘部署协调层。**不改动 nanobot 源码**，完全通过官方 Python SDK 复用其能力（多 Provider、Ollama 离线、记忆/Dream、会话持久化、上下文注入、Hooks）。

---

## 1. 为什么用「确定性分析 + LLM 解读」的混合架构

工业数据不可靠的地方不在"理解"，而在**事实抽取**。温度 1℃、转速漂移 3%、OEE 掉 12%，这些必须由**确定性算法**产出，绝不能由 LLM 凭感觉生成——否则数字错了，再专业的解读也是错的。

这是本方案最大的一个提升点，也是正确性的根基：

```
原始时序数据（温/压/转速/OEE…）
        │
        ▼
   ┌────────────────────────────────────────────┐
   │   Analytics 引擎 (纯确定性的 numpy/pandas)  │  ← 事实层：可审计、可复现、零 token 成本
   │  · 降采样与清洗                            │
   │  · 统计量：均值/极值/std/分位数/OEE         │
   │  · 超限检测：区间/持续时间/能量             │
   │  · 趋势/突变：斜率、均值漂移、TP 突变       │
   └──────────────────┬─────────────────────────┘
                      ▼
   AnalyticsSnapshot（结构化 JSON：纯事实）
        │                      ▲
        ▼                      │
   ┌────────────────────────────────────────────┐
   │   LLM（在线 API 或本地 Ollama，可降级）      │  ← 解读层：只负责把事实讲成专业报告/评估
   │   输入 = 资产档案 + 快照(JSON) + 相关历史   │     以及回答"现在设备状态如何？"
   │         知识 + 上期报告摘要                 │
   └────────────────────────────────────────────┘
```

**收益**：
- **准确性**：数字是算出来的，不是"猜"的，报告可复现、可审计。
- **离线可用**：本地小模型只需读 JSON 快照说话，不需要自己在海量时序里找规律，能力门槛大幅降低。
- **成本**：快照是高度压缩的，注入上下文远比灌原始数据便宜；例行监控走本地小模型，几乎零成本。
- **可解释**：每次结论都能追溯回快照里的具体统计量和超限事件。

---

## 2. 双模型自动降级策略

配置中有 `online`（在线 API）与 `offline`（本地 Ollama）两个 Provider 预设。`LLMGateway` 按任务类型与可用性分流：

| 任务类型 | 首选路由 | 降级规则 |
|---|---|---|
| 例行监控 / 快速问答 | 本地 Ollama（零成本、隐私） | 失败→在线 |
| 定时深度报告 | 在线 API（强模型、深度） | 网络/API 失败→本地 Ollama，**服务不中断** |
| 历史学习 / 知识固化 | 在线 API | 失败→本地，退化为保守固化 |

nanobot 本身有 `FallbackProvider`，但这里我们在**编排层**做任务级路由 + 失败重试，原因是：不同任务对模型质量要求不同，且我们要在降级时**记录发生了降级**并写进报告元数据（合规需要知道"这份报告是强模型还是轻模型写的"）。

失败检测依据：nanobot SDK 在 `result.error` 或 stream 的 `run.failed` 事件中暴露错误；本地 Provider 延迟/超时我们要在网关层做超时包裹。

---

## 3. 上下文注入（让离线小模型也能干活的关键）

利用 nanobot 的 `bot.runtime.add_context_provider()` 每轮注入**受控上下文**，全程只注入"当前快照 + 相关历史知识 + 上期摘要"，**不重放全量历史**：

```
[场景元数据]  设备资产档案：型号、参数单位、正常范围、维护规程（来自 ASSET.md）
[事实基座]    AnalyticsSnapshot JSON（本次窗口的统计/超限/趋势）
[历史知识]    检索到的该设备历史 MEMORY.md / 历史报告要点（RAG，最多 K 条）
[上期摘要]    上次报告的一句话结论
```

- 用 `RuntimeContextBlock(source=..., content=...)` 包裹，且用 JSON 编码 + 转义 `[`/`]`（nanobot 约定），作为"元数据而非指令"，防止检索到的知识误导模型。
- 上下文总量用 token 预算约束（默认几条知识 + 截断），保证任何本地模型窗口容得下。
- `session_key = asset:<id>:<mode>` 让每台设备的监控/报告/问答各自隔离历史。

---

## 4. 历史运行数据的学习闭环（→ 企业知识资产）

这是"数据量有限也能沉淀价值"的核心机制：

```
运行数据 + 报告 + 人工反馈
        │
  每天学习任务（Supervisor 触发）
        ▼
  用 LLM 从「新产生的运行快照 + 该设备的 MEMORY.md」提炼：
     · 该设备实际上限/下限（从数据反推）
     · 已出现的故障先兆信号
     · 维护建议 + 上次建议的执行结果
        ▼
  写入 <assets>/<asset_id>/MEMORY.md（版本化、可回溯）
        ▼
  下次分析/问答通过 RAG 检索到它 → 越用越准
```

- 每个资产一个独立工作区目录，`MEMORY.md` 即该设备的**领域知识库/知识资产**。
- 采用与 nanobot Dream 同构的思路（history.jsonl → MEMORY.md），但聚焦"运行规律"而非"对话偏好"。
- 报告按 `<assets>/<asset_id>/reports/<YYYY-MM-DD>/report.md + snapshot.json` 归档，天然版本化，是可持续交的"知识资产"。

---

## 5. 系统架构

```
┌────────────────────────────────────────────────────────────────────┐
│  EdgeAdviser Supervisor（调度器, CRON/事件循环）                     │
│  · 按周期：采集 → 分析 → (门控) 监控报告 / 定时深度报告            │
│  · 每日：历史学习任务                                              │
└───────┬────────────────────────┬────────────────┬────────────────┘
        ▼                        ▼                ▼
   DataIngest               Analytics        LLMGateway
   数据源连接器              确定性分析          多Provider+降级
   · MQTT/OPC-UA/Modbus TCP → AnalyticsSnapshot +上下文注入
   · REST/HTTP 拉取                              →Nanobot SDK
   · 模拟数据源(MVP)                          (bot.run/stream)
        │                        │                │
        └─────────┬──────────────┴────────┬───────┘
                  ▼                       ▼
            ReportGenerator            QAEngine
             报告归档                  人工问答服务
            <assets>/<id>/            (CLI/Web 接口)
                │
                ▼
         知识资产库（工作区）
         <workspace>/assets/<asset_id>/
            ├── ASSET.md        （场景元数据/档案）
            ├── MEMORY.md       （历史学习沉淀的领域知识）
            ├── snapshots/      （每次分析的 JSON 快照）
            └── reports/        （Markdown 报告 + 结构化 JSON 归档）
```

---

## 6. 模块规划与文件

```
edge_adviser/
  edge_adviser/
    config/
      __init__.py
      model.py          # 数据类：AssetSpec, MetricSpec, SourceSpec, ReporterConfig...
      loader.py         # 配置文件(JSON/YAML)加载
      defaults.py       # 默认参数、告警阈值、token预算
    core/
      asset_store.py    # 资产注册表：assets 目录读写、ASSET.md/MEMORY.md 管理
      snapshot.py       # AnalyticsSnapshot 数据类 + schema
      scheduler.py      # Supervisor 调度循环
    analytics/
      windows.py        # 滑动窗口/降采样/清洗
      stats.py          # 统计量、OEE
      detectors.py      # 超限、趋势、突变检测
      engine.py         # 组装各步骤 → AnalyticsSnapshot
    ingest/
      base.py           # DataSource 抽象基类
      simulator.py      # 模拟数据源(漂移+故障注入), MVP 用
      mqtt.py / opcua.py / modbus.py / rest.py   # 真实源
    llm/
      gateway.py        # LLMGateway: 多Provider路由+降级+token预算
      context.py        # 上下文构建: 资产档案+快照+检索知识+上期摘要
      providers.py      # nanobot SDK 封装(在线/离线 preset)
    report/
      generator.py      # 用LLM生成 Markdown 报告, 附 snapshot.json
      archived.py       # 报告归档 & 索引
    learn/
      learner.py        # 历史学习任务 → MEMORY.md
      feedback.py       # 人工反馈写入 history.jsonl
    qa/
      engine.py         # QA: 检出知识库+快照,回答"当前设备状态"
      cli.py            # 命令行问答入口
    cli.py              # 主 CLI: setup / simulate / monitor / report / learn / ask / assets
  profiles/             # 示例资产档案 (JSON/YAML)
  tests/                # 单元+集成测试
  DESIGN.md             # 本文档
  README.md
  pyproject.toml
```

---

## 7. 实施顺序（MVP 端到端闭环）

1. **领域模型 + 资产注册表**（config + asset_store）— 先定义"设备档案/参数规格/数据源"这三类核心对象。
2. **模拟数据源**（ingest/simulator）— 生成带漂移+故障注入的时序，离线可跑。
3. **确定性分析引擎**（analytics）— 产出 AnalyticsSnapshot。
4. **LLMGateway + 上下文注入**（llm）— 封装 nanobot SDK，双模型降级，注入快照。
5. **报告生成与归档**（report）— Markdown + JSON。
6. **历史学习 + QA**（learn + qa）— 学习沉淀 MEMORY.md，问答检索回答。
7. **Supervisor 调度 + CLI + 端到端验证**。

> 第 1 步领域模型是后面一切的契约，建议最先确认（Metrics 的名、单位、正常范围、告警阈值、采集频率）。

---

## 8. 与 nanobot 内核的边界契约

| 能力 | 复用 nanobot 的方式 | 位置 |
|---|---|---|
| 多 Provider / Ollama 离线 | 配置多个 `modelPresets`，SDK `Nanobot.from_config(model_preset=...)` | `llm/providers.py` |
| 会话隔离 | `bot.run(..., session_key="asset:<id>:<mode>")` | 各处 |
| 上下文注入 | `bot.runtime.add_context_provider(...)` 返回 `RuntimeContextBlock` | `llm/context.py` |
| 记忆/持久化 | 复用 nanobot 的 SQLite 会话 + 我们自己的 `assets/<id>/MEMORY.md` | `core/asset_store.py` |
| 流式 | `bot.stream(...)` 观察进度/tool/失败 | `llm/providers.py` |
| 结构化失败 | `RunResult.error` 判断降级 | `llm/gateway.py` |

**不改动 nanobot 内核任何文件**，全部新增在 `edge_adviser/`。这样升级 nanobot、独立测试、单独分发都更干净。