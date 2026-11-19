# EdgeAdviser — 更新记录 / Changelog

本埋记录 EdgeAdviser（边缘设备智能顾问）在 nanobot 仓库中的新增与更新。

---

## 0.4.1 — 2026-11-19（系统性加固：可信度契约修复 + 稳定性 + 安全 + cron 调度）

对全部四个层面（分析引擎 / 调度 / HMI 服务端 / 前端）做了一次系统性代码审查，修复了审查确认的真实缺陷，并接入仓库 CI。全部 52 项测试通过。

### 可信度契约（fact 层正确性）

- **NaN/Inf 防护**：分析引擎此前只过滤 `None`，NaN 会污染全部统计并使快照序列化为非法 JSON（HMI 多个接口 500）。现在非数值/NaN/Inf 样本在进入统计前被丢弃，全 NaN 窗口判 UNKNOWN 而非"nominal"。
- **分析异常不再逃逸**：`analyze()` 崩溃此前会被 web 循环的裸 `except` 吞掉——资产静默消失（无快照、无审计、永不判 OFFLINE）。pull 失败 / 空窗口 / 分析失败三条路径统一归档空快照 + runlog 审计 + 推进状态机。
- **时区统一 UTC aware**：空快照此前用 naive 本地时间被读门禁当作 UTC 判定"永远新鲜"，绕过"无数据即拒绝"契约；runlog/报告/记忆/告警时间戳全部统一为 aware UTC；修复 aware/naive 混减导致的 `TypeError` 崩溃。
- **报告结论链接通**：归档时把报告的 Executive Summary 提取为 `conclusion` 写入 snapshot.json，学习闭环的"上次报告结论"输入不再恒为空；对历史归档自动回退到从 report.md 提取。

### 稳定性（长期运行）

- **SSE/事件总线泄漏**：断开的浏览器此前永远留在订阅列表（幽灵订阅者，每次 publish 都往无人读的队列推数据）；`EventBus` 的内部队列只进不出。两者都已修复。
- **数据 retention**：新增 `max_snapshots` / `max_runlog_rows` 配置（快照 2000、账本 10000 行，0=不限制），归档时自动剪枝——长期运行的边缘部署不再填满磁盘。
- **异常设备告警链路**：持续 pull 失败的设备（判 OFFLINE 后）的空快照此前被 web 调度循环跳过、从不派生告警；现在正常广播并触发 offline 告警。
- **空报告不再归档**：provider 全挂时不再把 `"(empty)"` 写入报告归档（假造一个"成功日"），改记 runlog `provider_failed`。
- **错误码分类**：`ask`/`report`/`learn` 的失败区分 `no_fresh_snapshot`（数据问题）与 `provider_failed`（模型故障），前端分别渲染"门禁拒绝"与"模型服务不可用"，操作员不再被误导。
- **cron 调度实现**：`deep_report_cron` / `learn_cron` 此前是虚设（从不生效）。新增 stdlib 轻量 cron 匹配器，CLI serve 与 web 循环按配置真实触发深度报告与学习，每分钟槽最多一次。
- **同秒快照覆盖**：手动"立即采集"与调度 tick 同秒写盘时不再互相覆盖（文件名追加微秒消歧）。
- **学习结论输入**、**Monitor 图表顺序**：`/snapshots` 端点统一旧→新返回，监控曲线不再时间倒序+右端回跳。

### 安全

- **PIN 暴破防护**：`/api/auth/pin` 增加滑动窗口限速（5 次失败锁定 5 分钟，锁定期间正确 PIN 也返回 429）。
- **token 重启失效**：HMAC 密钥持久化到 workspace（0600），重启后已发 token 仍然有效；前端增加兜底——任何 401 自动清除失效 token 并重入 PIN 流程，操作员不再被锁死在 401 循环里。
- **query token 收紧**：`?token=` 只对 GET/HEAD 生效（URL 中的 token 会进日志/历史，不得重放写操作）。
- **demo 写防护**：demo 模式（无 PIN）拦截携带外部 Origin 的写请求（403），阻断跨站页面驱动演示系统。

### 修复的配置与降级语义

- **`--demo` 强制生效**：此前 `create_app` 先构建服务图（已选定真实 Gateway）后才设置 `config.demo`——强制演示模式实际仍用真模型。现在在构建前生效。
- **CLI workspace 解析**：相对路径此前按进程 CWD 解析（与 server 行为不一致，0.3.0 的 CHANGELOG 宣称只对 server 成立）。CLI 现在与 server 同规则按配置文件目录解析。
- **`model_preset` 降级链**：显式指定 preset 此后会替换候选链（失败即全挂）；现在改为前置，失败后继续走角色默认降级链。
- **`config.sample.json`**：移除硬编码 Windows 路径与过期停机窗口、误导性的死配置 `api_base`。
- **demo 同日重启**：tower1 的计划停机窗口现在每次启动重新注入，不再漂移成"断连"告警。

### 前端

- 连接状态聚合：两个 SSE 流不再互相覆盖 `conn`（状态抖动）。
- 后端不可达时显示"无法连接服务器 + 重试"页面，不再永久骨架屏。
- 任务流：被拒绝/失败的动作正确上报 `phase=failed`，不再显示为完成。

### 工程

- **edge_adviser 接入仓库 CI**：`ci_scope.py` 新增 `edge_required` 域，`ci.yml` 新增 edge job（editable 安装根包 + edge_adviser 及其测试依赖后跑 `pytest edge_adviser/tests`），纳入 required 门禁。
- **测试 52 → 111**：新增四批回归测试（`test_p0_fixes` / `test_p1_fixes` / `test_p2_fixes` / `test_gateway`），覆盖每个修复；gateway 降级核心首次有测试。
- **死代码清理**：删除 `analytics/windows.py`、`asdict_like`、`KIND_SCHEDULE`。

---

## 0.4.0 — 2026-11-19（HMI 重构升级 + 后端 Demo 模式）

在 0.3.0 的 HMI 基础上做 **UI 全面重设计 + 后端确定性 Demo 模式 + 一批真实 bug 修复**。一条命令即可启动完整的本地演示系统，无需任何 LLM/模型基础设施。

### 后端 Bug 修复（0.3.0 引入或遗留）

| # | 文件 | 问题 | 修复 |
|---|---|---|---|
| 1 | `server/alerts.py` | `KIND_ALERT` 未导入 → NameError 被吞 → **告警 SSE 从未广播** | 补导入；SSE 告警实测送达 |
| 2 | `server/app.py` | `check_auth` 定义了但从未注册 → **PIN 模式完全不生效** | 注册为 HTTP 中间件；`?token=` query 兜底（EventSource 无法带头） |
| 3 | `server/security.py` | `token_for` 签 `"<exp>.<sig>"` 但 `verify_token` 校验剥离点号后的 body → **签名永不匹配，token 一发即失效** | 统一为校验 `"<exp>."` 的精确字节 |
| 4 | `server/app.py` | `PinIn` 类定义在 `create_app` 内部，`from __future__ import annotations` 下 FastAPI 无法解析 → PIN 交换恒 422 | 移到模块级 |
| 5 | `server/app.py` | `/dist/monitor/pump1` 等深链接刷新 404 | 404 handler 把无扩展名的 `/dist/*` 重写到 SPA shell |
| 6 | `ingest/simulator.py` | 内部时钟只在首次 pull 锚定 → 时钟漂移（未来时间戳/陈旧快照） | 每次 pull 以请求窗口锚定 |
| 7 | `server/router_actions.py` | `/stream/task/{token}` 不过滤 token；动作无唯一 token | uuid token + `predicate` 过滤；响应返回 token |
| 8 | `server/router_assets.py` | 快照摘要行无 metrics → Monitor 无法画历史曲线 | `?include_metrics=1` 参数 |
| 9 | `config/model.py` | `MetricSpec.to_dict` 笔误 `asdict-like(self)` | `asdict(self)` |

### 后端 Demo 模式（本地测试，零模型依赖）

| 模块 | 内容 |
|---|---|
| `config.demo.json`（新） | 5 台设备覆盖全部状态：motor1=healthy / pump1=warn / chiller1=warn（spike）/ tower1=planned_downtime / feeder1=stale |
| `llm/mock.py`（新） | `MockGateway`：确定性、无 LLM 的 gateway —— 报告/QA/学习内容为快照事实的纯函数；含人工延迟让 UI 加载态可见 |
| `server/demo.py`（新） | `seed_demo_workspace`：启动时播种 24 份快照/资产、4 天归档报告、3 条告警（1 条已确认）、runlog/反馈/MEMORY.md/claims+conflicts；按日幂等 |
| `ingest/simulator.py` | 新 regime `offline`（返回空）/`fault`（抛传输异常）；新 `seed` 参数 |
| `config/model.py` + loader | `demo: bool` 字段 |
| `cli.py` | `web --demo` 强制演示模式 |
| `/api/version` + `/api/config` | 返回 `demo`/`version`/`pin_enabled`/`assets`，UI 显示 DEMO 徽章 |

### API 增强（向后兼容）

- `GET /api/overview?history=8` — 每卡附带首指标 sparkline 系列（一次请求免 N+1）
- `GET /api/assets/{id}/snapshots?include_metrics=1` — 行内嵌 metrics 数组
- `GET /api/assets/{id}` — 附 `source_kind`
- `POST /api/assets/{id}/run-now|report|learn` — 响应含 `token`（订阅 `/stream/task/{token}`）

### 前端重设计（10 页全部重做）

- **设计系统**：`style.css` 全面 token 化（渐变/玻璃拟态面板/阴影/圆角/间距/字阶/统一滚动条）；`theme/echarts.ts` 注册 `ea-dark` 图表主题；`levelMeta` 作为级别色单一来源；`relativeTime/fmtNum/formatTs` 收纳重复工具
- **App 壳**：可折叠侧栏（图标导航 + localStorage 记忆）、<900px 抽屉、顶栏 DEMO 徽章 + **SSE 连接状态指示**（实时连接/重连中）、新告警 ElNotification 弹出
- **Dashboard**：KPI 条（设备数/正常/警告/严重/数据劣化/未确认告警/平均覆盖）+ 卡片墙每卡 sparkline + 带单位指标值 + 级别 chip + 相对时间
- **Monitor**：修复未定义 `metricKeys`（历史无数据时图表根本不渲染的 bug）；`include_metrics=1` 拉历史曲线；阈值 markArea 色带；新鲜度进度条；「立即采集」按钮
- **Alerts**：设备/级别过滤 + 计数 chip + 逐行确认 loading + 已确认历史（含 acked_at）
- **Reports**：左侧归档时间线 + MarkdownView（marked+DOMPurify 替换损坏的手写渲染器）+ 认证下载（window.open 丢 token 修复）+ 生成 busy 态
- **Qa**：每资产独立线程、真实 trust 徽章、门禁拒绝/网络错误分类卡片、建议问题、Shift+Enter 换行、markdown 答案
- **Knowledge**：章节导航 + claims/conflicts 状态卡 + 空态「运行学习」CTA
- **Feedback**：快捷类别 chips + 内容校验 + 时间格式化
- **Assets**：左列表右详情 + MetricBand 阈值色带现值标记 + source_kind + ASSET.md 展示
- **Runlog**：缺口日检测（无审计记录的日历日高亮）+ 结果过滤 + 成功率
- **Settings**:服务状态卡（版本/DEMO/健康检查）+ provider 空态提示 + 错误重试态
- **构建**：新增 `npm run typecheck`（vue-tsc 0 错误）；新增依赖 marked/dompurify/@element-plus/icons-vue（全部本地打包，离线约束不破）

### 已验证

- `62 passed`（46 原有 + 10 修复测试 + 6 demo 测试）离线全绿。
- `python -m edge_adviser.cli web config.demo.json` 一条命令完整演示；10 项 curl 清单全过（版本/五资产状态/sparkline/metrics/4 天报告/DATA-SCOPE/mock 问答/门禁拒绝/深链接/根路径）；SSE 告警在首个调度 tick 实测推送。
- `npm run typecheck` + `npm run build` 全绿。

---

## 0.3.0 — 2026-11-18（现场人机交互界面 HMI）

在 CLI 之上叠加一套 **B/S 现场人机界面**，让运维/巡检/工程师无需敲命令即可掌握设备状态并追溯审计。**不改动 nanobot 内核**，全部新增在 `edge_adviser/` 内。

### 新增内容

| 模块 | 内容 | 说明 |
|---|---|---|
| `server/app.py` | `create_app()` + lifespan | FastAPI 应用：组装服务、后台启动 Supervisor 调度 task、托管 Vue SPA |
| `server/deps.py` | `AppState`/`build_state` | 组合 CL/`Supervisor` 同款服务图；workspace 相对路径按配置文件目录解析（修复 CLI 的 CWD 隐患）|
| `server/router_*.py` | REST 读侧 API | assets/snapshots/reports/knowledge/feedback/runlog/overview/config/health/version |
| `server/router_actions.py` | 写侧动作 + SSE | run-now/report/learn/ask；/stream/telemetry /alerts /task |
| `server/alerts.py` | 告警派生/去重/确认 | 真实告警级(warn/critical) OR 数据故障(offline/stale) 才告警；计划停机/数据覆盖判定豁免；未确认前去重 |
| `server/security.py` | 可选共享 PIN | `hmi_pin` 非 none 时启用，HMAC 短期 token，无用户表（LAN 内部边界）|
| `server/schemas.py` | 统一响应信封 | `{ok, data, error}`；门禁拒绝仍 200 返回，前端渲染为确定性拒绝而非异常 |
| `core/bus.py` | 进程内事件总线 | async pub/sub + replay，供 SSE 流分发新快照/告警 |
| `core/asset_store.py` | 补 list_reports/read_report/read_persisted_snapshot | API 层直达归档报告/快照 |
| `webui/` | Vue3 + Vite + Element Plus + ECharts 前端 | 10 页面 + 深色工业风主题 + 状态/可信色分离 + 角色折叠 |
| `cli.py` | `web` 子命令 | 一条命令启动 HMI（`edge-adviser web <config>`）|
| `config/model.py` + loader | `hmi_pin/hmi_host/hmi_port` | HMI 访问控制与绑定配置（缺省=开放，向后兼容）|
| `config.sample.json` | 补 hmi 字段 | |

### 界面与可信度契约（关键设计）

- **状态色与可信色分离**：状态（healthy/warn/critical/insufficient/unknown/offline/stale/计划停机）与信任度（full/partial/low/none）独立徽章并排，同时可见。
- **无新鲜快照即确定性拒绝**：ask/monitor/report 走门禁；界面明确显示"无新鲜快照，已拒绝回答"，不驱动模型猜。
- **报告含机器生成 DATA-SCOPE 徽章**：top 固定，模型不得改写。
- **告警去重/去抖**：真实状态变化才报，计划停机豁免，未确认前不重复刷屏，可确认。
- **审计可追溯**：Runlog 逐日展示结果，缺失/失败日标出。
- **深色工业风 + 触控友好 + 弱网离线**看最近快照。

### 依赖变更（`webui/pyproject`）
- `edge_adviser/pyproject.toml`：新增 `fastapi`、`uvicorn[standard]`、`sse-starlette`。
- `webui/package.json`：vue/vue-router/pinia/element-plus/echarts + vite/vue-tsc 工具链。

### 已验证
- `46 passed`（原 26 + 新增 server/bus/alerts 20 个离线确定性测试）。
- 端到端：`create_app` 起 uvicorn 后 `/api/health` 200、SPA 在 `/` 与 `/dist` 托管、主 JS bundle 可载、`run-now` 产出 healthy 快照、SSE telemetry/alerts 推送实时数据、告警派生/持久化/确认闭环通过。

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