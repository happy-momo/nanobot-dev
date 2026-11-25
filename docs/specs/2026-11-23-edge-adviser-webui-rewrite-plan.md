# EdgeAdviser WebUI 全新重写实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按设计规范 `docs/specs/2026-11-23-edge-adviser-webui-redesign-design.md` 完全重写 `edge_adviser/webui`（后端 API/SSE 零改动）。

**Architecture:** Vue 3 + TS strict + Vite。无成品 UI 库：Reka UI（无头交互件）+ TanStack Table（表格）+ lucide 细线图标 + IBM Plex 本地字体。视觉层全部由 `src/theme/tokens.css`（唯一 token 来源）驱动；页面 = 观测台面板（hairline 分格），签名元素 FleetSpectrum + VerdictFrame。数据层照搬旧实现的 SSE/resync/http 信封逻辑。

**Tech Stack:** vue@3.4, vite@5, vue-router@4, pinia@2, echarts@5, reka-ui, @tanstack/vue-table, lucide-vue-next, @fontsource/ibm-plex-sans, @fontsource/ibm-plex-mono, marked, dompurify。

## Global Constraints

- 后端零改动：不改 `edge_adviser/`（Python）任何文件；保留 `vite.config.ts` 的 `base:'/dist/'` 与 dev proxy。
- 移除 `element-plus`、`@element-plus/icons-vue`；禁止再引入任何成品 UI 组件库。
- 所有颜色/字号/圆角必须取自 `src/theme/tokens.css` CSS 变量，组件内禁止硬编码色值（ECharts 主题文件除外，其色值必须抄自 tokens 注释值）。
- 青色 `#3BE0D8` 只用于确定性事实；紫色 `#A78BFA` 只用于 LLM 解读内容（必须带溯源行）。
- 禁止：蓝紫渐变、`backdrop-filter`（仅 QA 抽屉遮罩允许）、圆角卡片墙、emoji 图标、外部 CDN 字体。
- 所有数字/ID/时间戳用 `--font-mono` + `tabular-nums`。
- 全站仅 4 处动效（见 spec §1.4），全部尊重 `prefers-reduced-motion`。
- 触摸目标：桌面 ≥32px，<900px 底部 tab 模式 ≥44px。
- 每个任务完成必须 `npm run typecheck` 通过 + `npm run build` 通过，否则不得 commit。
- API 契约：一律经 `src/api/http.ts` 信封解包；端点清单见 spec §5 与旧实现 `webui/src/api/`（重写前先读旧文件 `overview.ts`/`assets.ts` 等确认字段名，字段名以旧实现为准，本计划不再重复枚举）。

**通用验证命令**（每任务最后一步执行）：
```bash
cd edge_adviser/webui && npm install && npm run typecheck && npm run build
```

---

### Task 1: 清场 + 依赖切换 + 工程骨架

**Files:**
- Modify: `webui/package.json`
- Modify: `webui/vite.config.ts`（不动逻辑，仅确认）
- Modify: `webui/index.html`（title/字体预载注释）
- Create: `webui/src/theme/tokens.css`（完整替换旧 `src/style.css`）
- Delete: `webui/src/style.css`、`webui/src/views/`、`webui/src/components/`（整体删除，Task 2 起新建；`src/api/`、`src/stores/runtime.ts`、`src/theme/echarts.ts`、`src/router/index.ts`、`src/App.vue` 暂保留供照搬，最后任务删除）

**Interfaces:**
- Produces: `tokens.css` 暴露的全部 CSS 变量（spec §1.1-1.3 的精确值）；后续所有任务消费这些变量名：`--bg0 --bg1 --bg2 --bg3 --line --line-2 --tx-hi --tx --tx-dim --cyan --cyan-dim --violet --violet-bg --ok --warn --crit --unknown --stale --r-s --r-m --font-sans --font-mono`。

- [ ] **Step 1: 改 package.json 依赖**

dependencies 增加：`reka-ui@^2`、`@tanstack/vue-table@^8`、`lucide-vue-next@^0.4xx`、`@fontsource/ibm-plex-sans@^5`、`@fontsource/ibm-plex-mono@^5`；删除 `element-plus`、`@element-plus/icons-vue`。其余依赖（vue/vite/pinia/router/echarts/marked/dompurify/typescript/vue-tsc）保持。

- [ ] **Step 2: 写 tokens.css**

`webui/src/theme/tokens.css` 完整内容（spec §1 逐字落地）：

```css
:root {
  --bg0: #04070D; --bg1: #080F1A; --bg2: #0C1626; --bg3: #101D30;
  --line: #14243A; --line-2: #1E3552;
  --tx-hi: #E9F2FC; --tx: #9FB4CC; --tx-dim: #5C7189;
  --cyan: #3BE0D8; --cyan-dim: rgba(59,224,216,.12);
  --violet: #A78BFA; --violet-bg: rgba(167,139,250,.08);
  --ok: #3DDC97; --warn: #F5B84C; --crit: #FF6B5E;
  --unknown: #273043; --stale: #7E93AC;
  --r-s: 5px; --r-m: 8px;
  --font-sans: 'IBM Plex Sans','PingFang SC','MiSans','Noto Sans SC',system-ui,sans-serif;
  --font-mono: 'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,monospace;
  --glow-num: 0 0 18px rgba(59,224,216,.35);
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg0); color: var(--tx-hi);
  font-family: var(--font-sans); font-size: 13px; line-height: 1.55; }
.mono, .mono * { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.eyebrow { font-size: 9px; letter-spacing: 2px; color: var(--tx-dim);
  font-weight: 600; text-transform: uppercase; }
.num-glow { text-shadow: var(--glow-num); }
a { color: var(--cyan); text-decoration: none; }
button { font-family: inherit; }
button:focus-visible, [role="button"]:focus-visible, input:focus-visible,
textarea:focus-visible, select:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: 1px; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: var(--line-2); border-radius: 99px; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
/* 通用面板/hairline 基元 */
.panel-frame { background: var(--bg1); border: 1px solid var(--line); border-radius: var(--r-m); }
.hline { height: 1px; background: var(--line); }
.vline { width: 1px; background: var(--line); }
.haircell { padding: 12px 14px; }
```

- [ ] **Step 3: main.ts 挂字体与 token（旧 bug D 顺带）**

```ts
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-sans/700.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/600.css'
import './theme/tokens.css'
import './theme/echarts'      // 显式注册 ea-obs 主题 —— 修复旧 bug D
```

同时把 `src/theme/echarts.ts` 内主题名 `'ea-dark'` 改为 `'ea-obs'`，色值改为抄自 tokens（背景 transparent、文字 `#9FB4CC`、网格线 `#14243A`、青 `#3BE0D8`、warn `#F5B84C`、crit `#FF6B5E`、ok `#3DDC97`）。

- [ ] **Step 4: 验证**

`npm run typecheck && npm run build` 通过（旧 views 已删，App.vue 若引用旧组件会有编译错 → 本步允许把 App.vue 临时替换为最小占位 `<template><router-view/></template>`）。

- [ ] **Step 5: Commit** `git add -A edge_adviser/webui && git commit -m "feat(webui): rewrite scaffold — tokens/plex fonts/reka deps, drop element-plus"`

---

### Task 2: 主题元数据 + 设计基元组件（theme/meta.ts、ObsPanel、VerdictFrame、StateDot、TrustBar、CoverageBar、LiveNumber、EmptyState、ConnStatus）

**Files:**
- Create: `webui/src/theme/meta.ts`
- Create: `webui/src/components/ObsPanel.vue` `VerdictFrame.vue` `StateDot.vue` `TrustBar.vue` `CoverageBar.vue` `LiveNumber.vue` `EmptyState.vue` `ConnStatus.vue`
- Test: 无单测设施 → 验证方式为本步末的临时 render 自检（可选）+ typecheck/build

**Interfaces:**
- Produces（后续任务全部按这些签名消费）:

```ts
// theme/meta.ts
export type AssetState = 'healthy'|'warn'|'critical'|'insufficient'|'unknown'|'offline'|'stale'|'planned_downtime'
export type TrustLevel = 'high'|'medium'|'low'
export type AlertLevel = 'ok'|'warn'|'crit'
export const stateMeta: Record<AssetState, { color: string; label: string; glow?: boolean }>
export const levelMeta: Record<AlertLevel, { color: string; label: string }>
export const trustMeta: Record<TrustLevel, { color: string; label: string }>
// stateMeta 映射: healthy→ok/正常; warn→warn/警告; critical→crit/严重(glow);
// offline→crit/离线; stale→stale/过期; planned_downtime→unknown/计划停机;
// insufficient|unknown→unknown/数据不足|未知
```

```vue
<!-- ObsPanel.vue  props: { title: string; meta?: string }  slots: default, actions -->
<!-- VerdictFrame.vue props: { color?: string }  slot: default（渲染四角 L 形角标）-->
<!-- StateDot.vue  props: { state: AssetState; glow?: boolean } -->
<!-- TrustBar.vue  props: { trust: number; level?: TrustLevel }  （数字 mono + 5px 细条）-->
<!-- CoverageBar.vue props: { coverage: number; samples?: number }  ≥80 青/≥50 warn/其余 crit -->
<!-- LiveNumber.vue props: { value: number; digits?: number; glow?: boolean; color?: string } -->
<!--   300ms rAF 数值缓动；reduced-motion 时直接显示 -->
<!-- EmptyState.vue props: { label: string; hint?: string } -->
<!-- ConnStatus.vue props: { conn: 'online'|'reconnecting'|'connecting'; lastEventAt?: number } -->
```

- [ ] **Step 1:** 写 `theme/meta.ts`（上面映射表逐字实现，颜色引用 tokens 值的十六进制字面量——meta.ts 是 TS 无法用 CSS 变量给 SVG/canvas，故集中放一份并在注释声明与 tokens 同源）。
- [ ] **Step 2:** 逐个实现 8 个组件。`VerdictFrame` 角标实现：

```css
.vf { position: relative; display: inline-block; padding: 6px 10px; }
.vf::before, .vf::after { content: ''; position: absolute; width: 9px; height: 9px; }
.vf::before { left: 0; top: 0; border-style: solid; border-width: 1.5px 0 0 1.5px; border-color: var(--vf-c, var(--cyan)); }
.vf::after  { right: 0; bottom: 0; border-style: solid; border-width: 0 1.5px 1.5px 0; border-color: var(--vf-c, var(--cyan)); }
```

`LiveNumber` 核心：`watch(() => props.value)` 启动 rAF，300ms 内 `displayed = from + (to-from)*easeOutCubic(t)`；`matchMedia('(prefers-reduced-motion: reduce)')` 命中则直赋。
- [ ] **Step 3:** 验证 `npm run typecheck && npm run build`。
- [ ] **Step 4: Commit** `feat(webui): design primitives — meta/panel/verframe/badges/livenumber`

---

### Task 3: http 层 + runtime store 照搬重组

**Files:**
- Create: `webui/src/api/http.ts`（从旧实现复制，移除 Element 依赖）
- Create: `webui/src/api/index.ts`（端点函数集合：旧散落各 view 的 fetch 全部收拢为具名函数）
- Create: `webui/src/stores/runtime.ts`（从旧实现复制，路径不变）
- Create: `webui/src/stores/qa.ts`

**Interfaces:**
- Consumes: 旧实现 `webui/src/api/http.ts`、`webui/src/stores/runtime.ts`（git 历史里有，`git show HEAD~1:edge_adviser/webui/src/api/http.ts` 或重写前旧工作区）。
- Produces:

```ts
// api/http.ts —— 签名与旧实现完全一致
export class ApiError extends Error {}
export const api = { get, post, patch }   // 信封 {ok,data,error} 解包
export function setUnauthorizedHandler(fn: () => void): void
export async function downloadFile(path: string, filename: string): Promise<void>
export function connectSSE(path: string, onEvent: (kind: string, data: any) => void,
  onStatus: (s: 'open'|'connecting'|'closed') => void): () => void
// api/index.ts —— 每个后端端点一个具名函数（字段名先读旧 view 源码确认）
export const fetchOverview = (history = 8) => api.get(`/api/overview?history=${history}`)
export const fetchVersion / fetchConfig / fetchHealth / fetchStats
export const fetchAssets / fetchAsset / fetchSnapshots(id, limit, includeMetrics)
export const fetchReports(id) / fetchReport(id, date) / fetchKnowledge(id)
export const fetchRunlog(id, before?, limit?) / fetchAlerts(id, includeAcked)
export const runNow(id) / triggerReport(id) / triggerLearn(id)
export const ask(id, question, allowStale) / ackAlert(id, ts, note?) / ackAllAsset(id, subject?, note?) / ackAllFleet(note?)
export const patchThresholds(id, metricKey, bands) / sendFeedback(id, note)
```

```ts
// stores/qa.ts
export const useQaStore = defineStore('qa', () => {
  // threads: Record<assetId, {role:'user'|'ai'; text:string; ref?:{ts:number;trust:number;provider:string}; kind:'answer'|'gate_refused'|'provider_failed'}[]>
  // 持久化 localStorage key 'ea_qa_threads'
  function ask(assetId: string, question: string, allowStale: boolean): Promise<void>
  function clearThread(assetId: string): void
})
```

- [ ] **Step 1:** 复制/微调 `http.ts`：`ElMessage` 全部替换为 `toast.ts`（Create: `src/stores/toast.ts` —— `export function toast(msg: string, tone?: 'info'|'warn'|'crit')`，暂用 console + 事件总线，Task 4 的 Toast UI 消费）。
- [ ] **Step 2:** 收拢端点到 `api/index.ts`，逐个对照旧 views 的调用点核对字段名（旧文件在 git HEAD 中：`git show HEAD:edge_adviser/webui/src/views/Qa.vue` 等）。
- [ ] **Step 3:** 复制 `stores/runtime.ts`（不变，它无 Element 依赖）。
- [ ] **Step 4:** 验证 + Commit `feat(webui): data layer — http/endpoint catalog/runtime store carried over`

---

### Task 4: App 外壳（rail 导航 + 全局顶情 + QA 抽屉容器 + Toast）

**Files:**
- Create: `webui/src/App.vue`、`webui/src/components/AppRail.vue`、`webui/src/components/QaDrawer.vue`、`webui/src/components/ToastHost.vue`
- Modify: `webui/src/router/index.ts`（新路由表）

**Interfaces:**
- Consumes: Task 2/3 全部产物。
- Produces:

```vue
<!-- AppRail.vue props: { alertsUnacked: number; conn: string } -->
<!--   emit: navigate(routeName), openQa -->
<!-- 图标: lucide Gauge(总览)/Activity(资产)/BellRing(告警)/MessagesSquare(问答)/ScrollText(审计)/Settings(设置) -->
<!-- 导航始终显示全部入口；rail<900px 变底部 tab（media query in component）-->
<!-- QaDrawer.vue props: { open: boolean; assetId?: string }  emit: close  内部用 useQaStore + QaPanel(Task 8) -->
```

- [ ] **Step 1:** 写新路由表（懒加载，路径见 spec §4：`/`→Overview、`/asset/:assetId`→AssetDetail、`/alerts`、`/qa`、`/audit`、`/settings`；占位视图先建 `views/*.vue` 最小骨架逐个 Task 填充）。
- [ ] **Step 2:** App.vue：rail + `<router-view>` + 全局 `QaDrawer`（provide `openQa(assetId?)` 函数供任意页面调用）+ ToastHost + demo 红标 + 401 重入（照搬旧 App.vue 逻辑）。不提供纯前端角色切换；所有入口始终可见。
- [ ] **Step 3:** ToastHost：Reka Toast，右下 hairline 小条，tone 着色左边框。
- [ ] **Step 4:** 验证：build 通过 + 手动 `npm run dev` 后端 demo 模式（`edge-adviser web --demo`）打开页面 rail 渲染、路由可达。
- [ ] **Step 5: Commit** `feat(webui): app shell — rail/router/drawer container/toast/role`

---

### Task 5: 总览页 Overview（签名① FleetSpectrum + verdicts 面板 + 全场台账）

**Files:**
- Create: `webui/src/components/FleetSpectrum.vue`
- Create: `webui/src/views/Overview.vue`

**Interfaces:**
- Consumes: `runtime` store（overviewCards/live/alerts）、`fetchOverview`、Task 2 基元。
- Produces:

```vue
<!-- FleetSpectrum.vue props: { cards: OverviewCard[] }  emit: select(assetId) -->
<!--   段宽 = 样本数权重(无快照等分)；段色 = stateMeta.color；crit 段 glow + '!' 后缀；warn 有正斜率加 '↗' -->
<!--   hover: Reka Tooltip（状态/覆盖/最新值）；短码 = assetId 大写截断 6 字 -->
```

- [ ] **Step 1:** 写 FleetSpectrum（26px 高 flex 条、段内 8.5px mono 标签、hover 抬亮 `--bg3`）。
- [ ] **Step 2:** 写 Overview：光谱带 + ACTIVE VERDICTS 面板（筛选规则：`state ∈ {critical,offline,stale} || overall_level ∈ {warn,crit} || alerts.length>0`；warn/crit 资产展开行 = reticle 状态字 + Sparkline(主指标 history) + 斜率标注 + TrustBar + 「查详情/问 AI」；fault 类紧凑行 + 「确认告警」）+ 全场台账（hairline 行：ID/名称 + 各指标 mono 值 + CoverageBar + StateDot，点击行 `router.push(/asset/{id})`）+ LIVE 指示 + 空状态"全场判定正常"。
- [ ] **Step 3:** Sparkline 改为手绘 SVG polyline（props `{ points: number[]; color: string }`，删除旧 ECharts 版以减 bundle）。
- [ ] **Step 4:** demo 走查：5 资产段正确着色（motor 绿/pump 黄/chiller 绿/tower 灰"计划停机"/feeder 红光晕）、SSE 更新时段色即时变化、pump 行展开有趋势与 TRUST。
- [ ] **Step 5: Commit** `feat(webui): overview — fleet spectrum strip + verdicts panel + fleet ledger`

---

### Task 6: 资产详情页骨架 + tab 实时（MetricChart + 告警卡 + AI 解读卡）

**Files:**
- Create: `webui/src/views/AssetDetail.vue`、`webui/src/components/MetricChart.vue`、`webui/src/components/MetricBand.vue`
- Create: `webui/src/views/asset/TabLive.vue`（骨架 tab 占位 TabHistory/TabReports/TabKnowledge/TabSettings 本任务建空壳）

**Interfaces:**
- Produces:

```vue
<!-- MetricChart.vue props: { spec: MetricSpecMeta; points: {ts:number; v:number}[]; unit?: string } -->
<!--   ECharts 'ea-obs' 主题；markArea: warn 带 rgba(245,184,76,.07) / crit 带 rgba(255,107,94,.07)；markLine 阈值虚线；dataZoom inside -->
<!-- AssetDetail.vue props(route param assetId)；页头 = 面包屑 + 名称 + VerdictFrame(state · trust) + 门禁事实行 + [立即采集][问 AI] -->
<!--   provide('openQa') 打开抽屉带当前 assetId -->
```

- [ ] **Step 1:** 页头 + tab 结构（实时/历史/报告/知识/设置，报告 tab 名带计数徽标）。
- [ ] **Step 2:** TabLive：左列 1.6fr 每指标 MetricChart（历史 `fetchSnapshots(id,40,true)` + SSE snapshot 合并，逻辑照搬旧 Monitor.vue，git 可查）；右列 1fr：AI 解读卡（紫 eyebrow + 溯源行，数据 = 当日报告 conclusion，`fetchReport(id, today)` 失败静默隐藏）+ 活跃告警卡（状态色左竖条 + 「确认」→ emit 给 Task 9 的 AckDialog 占位）。
- [ ] **Step 3:** 立即采集按钮：`runNow(id)` 同步等待，loading 态，成功 toast + 刷新。
- [ ] **Step 4:** demo 走查：pump-03 温度图有 warn 色带与越限虚线、SSE 到达曲线尾部前移。
- [ ] **Step 5: Commit** `feat(webui): asset detail shell + live tab with threshold-band charts`

---

### Task 7: 资产详情其余 tab（历史 / 报告 / 知识 / 设置）

**Files:**
- Create: `webui/src/views/asset/TabHistory.vue` `TabReports.vue` `TabKnowledge.vue` `TabSettings.vue`
- Create: `webui/src/components/TaskProgress.vue`、`webui/src/components/ScopeBadge.vue`、`webui/src/components/MarkdownView.vue`

**Interfaces:**
- Produces:

```vue
<!-- TaskProgress.vue —— 统一任务流（修复旧 bug B）:
  props: { run: () => Promise<{token:string}>; label: string }  emit: done
  内部: connectSSE(`/stream/task/${token}`) 收 queued/start/done/failed；
  600s 超时 → phase='timeout' → 复位按钮 loading（三态 done/failed/timeout 全部复位）-->
<!-- ScopeBadge.vue props: { meta: ReportMeta } —— DATA-SCOPE 青色系徽章 -->
<!-- MarkdownView.vue —— marked + DOMPurify 照搬旧实现 -->
```

- [ ] **Step 1:** TabHistory：TanStack Table 列 = 时间(mono)/overall/各指标/coverage/trust；数据 `fetchSnapshots(id, 100)` 翻页；行展开显指标明细（MetricBand）。
- [ ] **Step 2:** TabReports：左日期列表（`fetchReports`）+ 右 ScopeBadge + MarkdownView + TaskProgress（label"生成报告"，`run: () => triggerReport(id)`）+ 下载 .md/.json（`downloadFile`）。
- [ ] **Step 3:** TabKnowledge：MEMORY.md 章节导航（按 `##` split，照搬旧 Knowledge.vue）+ claims/conflicts（冲突行 warn 色"已被取代"）+ learn_gate 状态 + TaskProgress（label"运行学习"）。
- [ ] **Step 4:** TabSettings（工程师）：只读档案 + MetricBand 行内阈值编辑（PATCH 校验：有限值/warn≤crit/重叠拒绝，错误即时 inline 提示——照搬旧 Assets.vue 校验逻辑）+ 计划停机窗只读 + 页脚"提交反馈"（textarea → `sendFeedback`）。文案如实：不写"只读"。
- [ ] **Step 5:** demo 走查：报告生成三态（done）、知识 tab 冲突账本渲染、阈值编辑保存后曲线色带变化。
- [ ] **Step 6: Commit** `feat(webui): asset tabs — history table/reports/knowledge/threshold settings`

---

### Task 8: 问答 QaPanel（抽屉 + 全屏共用）

**Files:**
- Create: `webui/src/components/QaPanel.vue`
- Modify: `webui/src/views/Qa.vue`（全屏模式 = QaPanel 铺满）、`QaDrawer.vue` 接入 QaPanel

**Interfaces:**
- Consumes: `useQaStore`、`ask`。
- Produces: `QaPanel.vue props: { assetId?: string; fullscreen?: boolean }`

- [ ] **Step 1:** 实现资产上下文条（无 assetId 显示 Reka Select 选资产）+ 会话流 + 输入框（Enter 发送/Shift+Enter 换行）+ allow_stale 开关（开启时输入框边框 `--warn`）。
- [ ] **Step 2:** 三态渲染：answer 气泡（左紫条 + 紫 eyebrow "AI 解读" + 尾部 mono 溯源 `依据快照 HH:mm · trust N% · provider`）；gate_refused 灰卡 + lock 图标 + 人话原因 + "允许旧数据重答"按钮；provider_failed 独立文案"模型服务不可用，请稍后重试"。两拒绝态视觉必须不同（灰卡 vs warn 边卡）。
- [ ] **Step 3:** demo 走查：motor-01 正常回答；feeder-01 门禁拒绝卡（stale 26 分钟文案正确）；断网 provider_failed。
- [ ] **Step 4: Commit** `feat(webui): qa panel — drawer/fullscreen, three-state answers with provenance`

---

### Task 9: 告警中心（TanStack Table + AckDialog + 批量确认）

**Files:**
- Create: `webui/src/views/Alerts.vue`、`webui/src/components/AckDialog.vue`

**Interfaces:**
- Produces: `AckDialog.vue props: { alert: AlertItem | null; assets?: string[] } emit: confirmed`

- [ ] **Step 1:** 页面：级别计数 chips（mono）+ Reka 筛选下拉（资产/级别/已确认）+ 表格（状态色左竖条 | 资产 | 标题 | 首次时间 mono | 持续时长实时计算 | 确认按钮）。行点击开 AckDialog。批量（按资产/全场）在筛选栏右，Reka AlertDialog 二次确认。
- [ ] **Step 2:** AckDialog：告警详情 + note textarea（1-2000 字校验）+ 确认调 `ackAlert`；成功后 toast"已确认并记入反馈"。
- [ ] **Step 3:** demo 走查：feeder-01 FAULT 告警确认 → 备注出现在 feedback（`/api/assets/feeder1/feedback` 验证）。
- [ ] **Step 4: Commit** `feat(webui): alerts center — table/ack dialog/batch ack`

---

### Task 10: 审计页（修复旧 bug C）+ 设置页

**Files:**
- Create: `webui/src/views/Audit.vue`、`webui/src/views/Settings.vue`

- [ ] **Step 1:** Audit：资产选择器 + **kind chips 筛选**（`pull/analyze/alert/ack/ask/report/learn/threshold`，"确认"= `kind==='ack'` —— 修复旧 bug C）+ 表格（时间/kind/outcome 着色/actor/reason/elapsed/window_end）+ 缺口日行 + `before` 游标 loadMore + CSV 导出（downloadFile）。
- [ ] **Step 2:** Settings：三面板（服务状态 `/api/version`+`/api/health`；运行观测 `/api/stats`——成功率/MTTA/gated_refusals/磁盘/每资产快照数；运行参数 `/api/config` 只读）。demo=true 时 App.vue 顶部红字 DEMO 横幅（本步接入）。
- [ ] **Step 3:** 验证 + Commit `feat(webui): audit (kind-based filter fix) + settings`

---

### Task 11: 收尾 —— 删除旧代码、全局走查、验收清单

**Files:**
- Delete: 旧残留（`views/` 旧文件、`theme/` 旧 CSS、残留 import）
- Modify: `webui/package.json`（最终依赖核对）

- [ ] **Step 1:** 全局搜索 `element-plus|ElMessage|el-` 确认零残留；`grep -rn "state === 'warn'" src/` 零残留（旧 bug E）；grep `backdrop-filter` 仅 QaDrawer 遮罩一处。
- [ ] **Step 2:** spec §10 验收清单逐项走查（demo 模式 `edge-adviser web --demo`）：光谱带→资产 5 tab→告警确认→QA 三态→报告任务流三态→学习→阈值编辑→审计 ack 筛选→设置；SSE 断连重连 resync；平板窄屏(<900px)底部 tab 布局且六个入口可见；PIN 登录/401 重入。
- [ ] **Step 3:** `npm run typecheck && npm run build`；bundle 检查 `grep -c element dist/assets/*.js` 为 0。
- [ ] **Step 4: Commit** `feat(webui): redesign complete — deep-space observatory UI per spec`
- [ ] **Step 5:** 更新 `edge_adviser/CHANGELOG.md` 加一行（仅文档）。

---

## Self-Review 记录

- Spec 覆盖：§1 tokens→T1；§2 布局→T1/T4；§3 签名→T2/T5；§4 路由/角色→T4；§5.1→T5；§5.2→T6/T7；§5.3→T9；§5.4→T8；§5.5→T10；§5.6→T10；§5.7→T4/T10；§6 组件→T2/T5/T6/T7/T9；§7 数据层→T3；§8 工程约束→Global Constraints+T11；§9 A 架构根治(T4/T8)、B(T7 TaskProgress)、C(T10)、D(T1)、E(T11 grep 验证)、F(T7 TabSettings)、G(T4)；§10→T11。无缺口。
- 占位符扫描：无 TBD；端点字段名以"读旧实现源码核对"为显式指令而非省略（字段枚举以旧代码为准是正确的单一来源）。
- 类型一致性：`stateMeta/TrustBar/FleetSpectrum/QaPanel/TaskProgress` 的签名在各任务间已对齐。
