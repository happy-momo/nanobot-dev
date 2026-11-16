# EdgeAdviser — 边缘设备智能顾问

基于 [nanobot](https://github.com/HKUDS/nanobot) 内核构建的**边缘部署设备智能顾问**。不改动 nanobot 源码，通过官方 Python SDK 复用其多 Provider、Ollama 离线、记忆与持久化、上下文注入能力。

**核心理念：确定性分析算事实，LLM 做解读。**

```
原始时序 → [Analytics 引擎: 统计/超限/趋势/突变] → AnalyticsSnapshot(JSON 事实)
                                                        ↓
         [LLM 在线API 或 本地Ollama, 自动降级] → 专业报告 / 问答 / 知识沉淀
```

## 为什么这样设计

工业数据的**数字必须是算出来的，不是让模型猜的**。本方案把"事实抽取"（均值/极值/超限持续时间/趋势斜率/变点）交给纯确定性的分析引擎，LLM 只负责把事实讲成专业报告、回答问题、沉淀领域知识。这让：

- **数字可审计、可复现**（每次结论能追溯回快照的具体指标）
- **离线小模型也能用**（只需读 JSON 说话，无需在海量时序中找规律）
- **成本低**（快照压缩注入，例行监控走本地小模型，几乎零成本）
- **可长期沉淀**（历史学习把运行规律写进每台设备的 MEMORY.md，越用越准，形成企业知识资产）

## 双模型自动降级

| 任务 | 首选 | 降级 |
|---|---|---|
| 顶深报告 | 在线 API（强模型） | 失败→本地 Ollama，服务不中断 |
| 例行监控 / 问答 | 本地 Ollama（零成本） | 失败→在线 |
| 历史学习 | 在线 API | 失败→本地保守固化 |

每次降级都被记录进报告/元数据，**合规上可知道这份报告是谁写的**。

## 快速开始

### 1. 安装

```bash
cd edge_adviser
python -m pip install -e .          # 安装依赖 + 入口命令
# 或仅跑源码：
export PYTHONPATH=$PWD/edge_adviser
```

### 2. 配置模型

编辑 `config.sample.json` 与 `nanobot.runtime.json`：
- `online`：在线 API preset（如 `primary`，填好 key/模型）
- `offline` / `routine`：本地 Ollama preset（`qwen2.5:7b`，指向 `http://127.0.0.1:11434/v1`）
- 至少配好一个可用的模型，否则 nanobot SDK 拒绝启动。

### 3. 端到端跑通

```bash
# 生成设备档案(ASSET.md)
python -m edge_adviser.cli setup config.sample.json

# 拉取+分析+归档一个时间窗（确定性分析，无需模型）
python -m edge_adviser.cli run  config.sample.json pump1

# 深度报告（在线→降级到本地Ollama），归档到 reports/<date>/
python -m edge_adviser.cli report config.sample.json pump1

# 历史学习：把运行规律固化进该设备 MEMORY.md
python -m edge_adviser.cli learn   config.sample.json pump1

# 人工问答：回答"当前设备状态"
python -m edge_adviser.cli ask   config.sample.json pump1 "这台泵当前风险如何？建议什么维护动作？"

# 记录操作员纠正（反馈闭环）
python -m edge_adviser.cli feedback config.sample.json pump1 "昨天振动 4.3mm/s 实际是联轴器松动，不是轴承问题"

# 常驻调度
python -m edge_adviser.cli serve  config.sample.json
```

### 4. 运行测试

```bash
PYTHONPATH=$PWD/edge_adviser python -m pytest edge_adviser/tests -q
```

## 目录结构

```
edge_adviser/
  edge_adviser/
    config/     领域模型 + 配置(Dataclass/JSON)
    core/       资产注册表 + 知识存储 + Supervisor 调度
    analytics/  确定性分析引擎（事实层）
    ingest/     数据源：模拟/MQTT/OPC-UA/Modbus/REST
    llm/        nanobot SDK 网关 + 上下文注入 + 双模型降级
    report/     报告生成与归档
    learn/      历史学习(MEMORY.md 固化) + 反馈
    qa/         人工问答引擎
    cli.py      命令行入口
  config.sample.json     示例配置
  nanobot.runtime.json   nanobot 模型预设
  workspace/assets/<id>/ 每台设备的：
      ASSET.md            场景元数据/档案
      MEMORY.md           历史学习沉淀的领域知识
      snapshots/*.json    每次分析的确定性快照
      reports/<date>/     归档报告(Markdown + JSON)
```

## 数据源接入

`ingest/` 定义了统一接口 `DataSource.pull(window_start, window_end)`：

- **SimulatorSource**：内置，带漂移/故障克隆，离线跑通全程（MVP 默认）
- **RestSource**：从 HTTP 拉取 JSON 样本窗口（已实现）
- **MqttSource / OpcUaSource / ModbusSource**：结构化骨架，按字段部署时接入对应客户端（paho-mqtt / asyncua / pymodbus）

## 配置模型参考（config.sample.json）

| 字段 | 含义 |
|---|---|
| `assets[].metrics[]` | 每个参数：单位、normal/warn/critical 区间、`persist`（连续 N 采样才告警） |
| `assets[].scenario_notes` | 场景背景知识，注入模型上下文（元数据，非指令） |
| `providers.*` | online/offline/routine 三档模型预设 |

## 与 nanobot 的边界

**不改动 nanobot 任何源码**。全部新增在 `edge_adviser/` 内。复用的能力：`Nanobot.from_config()` 多 Provider、`add_context_provider` 注入 `RuntimeContextBlock`、`bot.run(..., session_key=...)` 隔离会话、nanobot 记忆/持久化机制。升级 nanobot、独立分发、单独测试都更干净。