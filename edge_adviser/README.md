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

## 可信度与可解释性（0.2.0）

工业现场的数据不可靠时，**系统必须承认"不可信"，而不是趁数据稀疏时最有信心地报"正常"**。快照(snapshot)是贯通报告/问答/监控/学习的事实脊椎，因此每一份快照都携带机器生成的可信度判定：

- **数据量下限** `MetricSpec.min_samples`：样本不足→`INSUFFICIENT`，不产出可信结论。
- **覆盖率** `coverage`：实际采样数 / 期望采样数，偏低→`PARTIAL`。
- **空度量**：`n=0→UNKNOWN`，报告报"无数据"而非"正常"。
- **断连/停机状态机**：连续空窗口→`offline`；计划停机（`downtime_windows`）不算异常。
- **新鲜度门禁** `data_freshness_s`：`ask/monitor/report` 无新鲜快照即确定性拒绝，不驱动模型猜。
- **审计账本** `runlog.jsonl`：每次 run/报告/学习都记录结果（含失败原因），缺失的报告日可对账。
- **可信度徽章**：报告顶部写机器生成的 DATA-SCOPE front-matter（样本数/覆盖率/窗口/provider/降级），模型必须引用、不得改写。
- **学习闸门** `learn_gate`（off/soft/strict）：坏数据不写回 MEMORY.md。
- **记忆检索** `memory_index`（off/keywords）：开启后按相关性检索；冲突/陈旧知识被标记。

所有判定均为**确定性逻辑**（无 LLM、可复现、可测试），配置项缺省值与旧行为一致，向后兼容。

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

### 5. 现场人机交互界面（B/S HMI）

0.3.x 提供一套给现场人员用的 Web 界面：**确定性事实与 LLM 解读分层展示**，弱网/离线可用，可追溯审计。技术栈：FastAPI 后端 + Vue3/Element Plus/ECharts 前端（静态打包，单端口）。

```bash
# 1) 构建前端（Vue3 + Vite + Element Plus + ECharts）
cd edge_adviser/webui
npm install
npm run build                # 产物 webui/dist/

# 2) 启动后端（FastAPI 托管 API + SSE + 已构建前端，单端口）
cd edge_adviser
PYTHONPATH=$PWD/edge_adviser python -m edge_adviser.cli web config.sample.json
# 或：PYTHONPATH=$PWD/edge_adviser python -m uvicorn edge_adviser.server.app:app_factory \
#       --env EDGE_ADVISER_CONFIG=config.sample.json --host 0.0.0.0 --port 8000

# 3) 打开浏览器 http://<host>:8000/
```

**页面**：总览(Dashboard)、实时监控(Monitor)、告警中心(Alerts)、深度报告(Reports)、智能问答(Q&A)、知识库(Knowledge)、反馈(Feedback)、设备管理(Assets)、审计(Runlog)、系统设置(Settings)。

**界面与 0.2.0 可信度契约一致**：
- **状态色与可信色分离**：`状态`（healthy/warn/critical/insufficient/unknown/offline/stale/计划停机）与`信任度`（full/partial/low/none）独立徽章并排——"状态"和"数据可信度"是两件事，同时可见。
- **无新鲜快照即确定性拒绝**：`ask/monitor/report` 走门禁；无新鲜数据时界面明确显示"无新鲜快照，已拒绝回答"，**不驱动模型猜**。
- **报告含机器生成 DATA-SCOPE 徽章**：顶部固定数据范围/覆盖率/Provider/是否降级，模型不得改写。
- **告警去重/去抖**：只对真实状态变化报警（真实告警级 OR 断连/过时），计划停机豁免；未确认前不重复刷屏；操作员可确认。
- **审计可追溯**：Runlog 页面逐日展示 run/报告/学习结果，缺失/失败日标出，可对账。
- **深色工业风 + 角色折叠**：操作工/工程师视图切换，触控友好，弱网离线仍可看最近快照（前端缓存）。

**访问控制**：`hmi_pin`（`config` 顶部）为 `"none"` 时局域网开放；设为字符串则启用共享 PIN，浏览器先 `POST /api/auth/pin` 换短期 HMAC token。

**设备与连接器配置**：设置页可维护资产、指标、REST 接口、MQTT 消息源、OPC UA 节点和 Modbus TCP 寄存器。配置管理及连接测试要求启用 `hmi_pin`，避免未认证请求读取接入拓扑或复用后端凭据。REST 请求头、MQTT/OPC UA 用户名密码及 OPC UA 私钥密码使用 Fernet 加密后保存在工作区 `.connector_credentials.enc`，必须在服务环境中设置 `EDGE_ADVISER_CREDENTIAL_KEY`；可用 `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` 生成密钥，并通过部署密钥管理系统保存。不要将密钥写入项目文件或配置备份；丢失密钥后原凭据无法恢复。删除资产前会下载不含密钥值和凭据标识的 JSON 备份，历史记录会保留。

### 6. 本地演示模式（无需任何模型）

0.4.x 提供一条命令的**完整本地演示**：确定性 MockGateway 替代真实 LLM，启动时自动播种快照/报告/告警/审计历史，5 台模拟设备覆盖全部状态（healthy / warn / planned_downtime / stale 等）。

```bash
cd edge_adviser
PYTHONPATH=$PWD/edge_adviser python -m edge_adviser.cli web config.demo.json
# 或对任意配置强制演示模式：  ... web config.sample.json --demo
```

- 问答/报告/学习由 `MockGateway`（`edge_adviser/llm/mock.py`）**确定性生成**——内容是快照事实的纯函数，无需 nanobot/Ollama/API key。
- 演示数据按日幂等播种（`workspace-demo/`），时间戳相对启动时刻重生成，新鲜度门禁必然通过；feeder1 故意保持 stale 以演示"无新鲜快照→确定性拒绝"。
- 界面顶栏出现 **DEMO** 徽章；`/api/version` 返回 `"demo": true`。
- 生产部署务必使用 `demo: false`（缺省）的配置。

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
    server/     HMI 后端：FastAPI(REST/SSE)、事件总线、告警、共享 PIN
    cli.py      命令行入口
  webui/               现场 HMI 前端（Vue3 + Vite + Element Plus + ECharts）
    src/                视图/组件/主题(stores, router, components, views, theme)
    dist/              构建产物，由后端托管（npm run build 生成，gitignored）
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
- **MqttSource**：订阅 `mqtt://` 或 `mqtts://` Broker 的 JSON 消息，支持 QoS 0–2、后端用户名/密码凭据、时间戳/响应路径/指标映射，并在进程内保留有界样本缓存；连接测试等待并校验实时消息，不模拟历史查询；启用认证时建议使用 `mqtts://` 保护传输中的凭据
- **OpcUaSource**：按配置的 NodeId 周期读取 OPC UA 数值节点，保留有界样本窗口；支持匿名安全模式或使用客户端证书/可信服务端证书的签名及签名加密连接，并将用户名、密码和私钥密码存于后端凭据库
- **ModbusSource**：使用 Modbus TCP 按采样周期读取线圈、离散输入、保持寄存器和输入寄存器；支持 bool、uint16/int16、uint32/int32、float32、缩放、偏移和 32 位字序配置

MQTT 消息为单条 JSON 对象，默认时间戳字段为 `ts`；例如 `{"ts":"2026-11-23T10:00:00Z","temperature":63.2}`。Unix 秒时间戳也可用；时间戳路径从消息根对象解析，响应路径选取指标对象，字段映射支持点分隔路径。MQTT 缓存只存在于后端进程内，服务重启后重新订阅，离线期间的历史消息不补采。

OPC UA 每个资产需为所有指标配置一个 NodeId（例如 `temperature=ns=2;s=Pump.Temperature`），并将证书文件预置在运行后端的主机上。用户名密码认证必须选择签名或签名加密模式并配置客户端证书、客户端私钥和可信服务器证书；无安全模式仅用于可信网络中的匿名读取。

Modbus TCP 每个指标需配置功能区、从零开始的地址和数据类型；32 位寄存器可指定 `big` 或 `little` 字序，并可设置缩放与偏移。Modbus TCP 不提供传输加密或身份认证，仅应连接可信的隔离网络；首版不包含串口 RTU。

## 配置模型参考（config.sample.json）

| 字段 | 含义 |
|---|---|
| `assets[].metrics[]` | 每个参数：单位、normal/warn/critical 区间、`persist`（连续 N 采样才告警） |
| `assets[].scenario_notes` | 场景背景知识，注入模型上下文（元数据，非指令） |
| `providers.*` | online/offline/routine 三档模型预设 |

## 与 nanobot 的边界

**不改动 nanobot 任何源码**。全部新增在 `edge_adviser/` 内。复用的能力：`Nanobot.from_config()` 多 Provider、`add_context_provider` 注入 `RuntimeContextBlock`、`bot.run(..., session_key=...)` 隔离会话、nanobot 记忆/持久化机制。升级 nanobot、独立分发、单独测试都更干净。