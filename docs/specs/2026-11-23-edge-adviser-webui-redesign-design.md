# EdgeAdviser WebUI 全新前端设计规范

日期：2026-11-23
状态：已与用户确认（视觉方向 A「深空观测台」+ 新信息架构，均已确认）
范围：**仅前端**（`edge_adviser/webui`）。后端 API / SSE 契约一律不动。
本文是唯一的实现蓝图：实现者（可为较小模型）按本文执行，不需要再做视觉决策。

---

## 0. 背景与目标

EdgeAdviser 是边缘部署的工业设备智能顾问：确定性 numpy 引擎算事实（均值/斜率/越限/覆盖率/信任度），门禁（数据不新鲜即拒绝驱动模型，fail-closed），LLM 只做解读。用户是现场操作工与工程师，弱网/平板场景。

旧前端（Vue3 + Element Plus，11 页）问题：

1. 页面重叠：Dashboard / AssetHub / Monitor 三者功能高度重叠，动线过深。
2. 跨页设备上下文丢失：AssetHub 跳转 `?asset=` 被 QA/Reports/Knowledge 页忽略（bug A）。
3. 若干功能 bug（见 §9）。
4. Element Plus 通用后台风，视觉无辨识度。

本次重设计：

- **视觉**：深空观测台（Deep-Space Observatory）。近黑底 + 青色 accent + 微光晕 + 等宽数字。炫酷、简洁大气、有质感，**严禁 AI 味**（无蓝紫渐变滥用、无玻璃拟态卡片堆砌、无圆角卡片墙、无 emoji 图标）。
- **架构**：11 页收敛为 5 个顶级页 + 1 个资产详情页；问答改为全局侧滑抽屉。
- **技术栈**：Vue 3 (`<script setup>` + TS strict) + Vite + vue-router + Pinia + ECharts + **Reka UI**（无头组件：对话框/下拉/弹层/焦点管理）+ **TanStack Table**（表格）。**不引入任何成品 UI 组件库**（移除 Element Plus）。

核心语义（贯穿全站，必须在视觉上永远分清）：

> **青色 = 确定性事实（引擎算的）**；**紫色 = AI 解读（模型说的）**。
> 任何 LLM 生成内容必须出现紫色标记 + 溯源行（快照时间 / trust / provider）。

---

## 1. 设计 Token（`src/theme/tokens.css`，全部为 CSS 自定义属性）

### 1.1 色板

```
--bg0:      #04070D   页面底（近黑、微蓝偏移）
--bg1:      #080F1A   面板底
--bg2:      #0C1626   抬升层 / hover / 选中
--bg3:      #101D30   强 hover / 输入框底

--line:     #14243A   hairline（面板边框、分隔线）
--line-2:   #1E3552   强分隔（hover 边框、次级边框）

--tx-hi:    #E9F2FC   主文字 / 大数字
--tx:       #9FB4CC   正文
--tx-dim:   #5C7189   次级 / eyebrow / 占位

--cyan:     #3BE0D8   主 accent：确定性事实、主按钮、LIVE、链接
--cyan-dim: rgba(59,224,216,.12)   accent 光晕底 / focus 环
--violet:   #A78BFA   AI 解读专属色（问答气泡标记、AI 洞察卡、报告徽章）
--violet-bg: rgba(167,139,250,.08)

--ok:       #3DDC97   healthy
--warn:     #F5B84C   warn
--crit:     #FF6B5E   critical / fault
--unknown:  #273043   unknown / offline / planned（灰段）
--stale:    #7E93AC   stale（在 unknown 上叠斜纹或降低饱和区分，见 §5.3）

--glow-num: 0 0 18px rgba(59,224,216,.35)   大数字光晕（仅 verdict 级数字）
```

状态→颜色映射（`stateMeta`，唯一来源 `src/theme/meta.ts`）：

| state / level | 颜色 | 备注 |
|---|---|---|
| healthy / ok | `--ok` | |
| warn | `--warn` | |
| critical / crit | `--crit` + box-shadow 光晕 | 唯一允许光晕的状态 |
| planned_downtime | `--unknown` | 文案必须写"计划停机中"，不计告警 |
| offline | `--crit` | 数据故障按 crit 处理 |
| stale | `--stale` | |
| unknown / insufficient | `--unknown` | |
| trust 高 | `--cyan` | trust 一律用青色（事实可信度） |

禁用色：纯紫渐变、蓝紫 gradient、玻璃 blur 面板（`backdrop-filter` 仅允许用于 QA 抽屉遮罩）。

### 1.2 字体

```
--font-sans: 'IBM Plex Sans', 'PingFang SC', 'MiSans', 'Noto Sans SC', system-ui, sans-serif;
--font-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace;
```

- IBM Plex Sans/Mono 走 npm 包 `@fontsource/ibm-plex-sans` / `@fontsource/ibm-plex-mono`（400/500/600/700），本地打包，**不引外部 CDN**（弱网离线要求）。
- **所有数字、ID、时间戳、单位一律 `--font-mono` + `font-variant-numeric: tabular-nums`**。
- 字号阶梯：11 / 12 / 13（正文）/ 15（小标题）/ 19-21（页标题）/ 26-34（verdict 数字）。
- eyebrow（小节标签）：9-10px、`letter-spacing: 2px`、全大写（拉丁）或加字距（中文）、`--tx-dim`。

### 1.3 几何

```
--r-s: 5px   小件（按钮/输入/小卡）
--r-m: 8px   面板
边框一律 1px solid var(--line)；不用 box-shadow 做层次，层次靠 --bg 阶梯与 hairline。
```

### 1.4 动效（全站仅 4 处，全部 `@media (prefers-reduced-motion: reduce)` 下关闭）

1. **数字滚动**：verdict 数字在 SSE 更新时 300ms 数值缓动（requestAnimationFrame 插值）。
2. **标线扫光**：reticle 四角在数据更新时一次 200ms 淡入脉冲（opacity .5→1）。
3. **光谱带异常段呼吸**：crit 段 box-shadow 光晕 2s ease 循环。
4. **QA 抽屉**：260ms cubic-bezier(.2,.8,.2,1) 滑入。

其余一切（hover、tab 切换）瞬时完成，不加过渡。

---

## 2. 布局范式：观测台面板（替代卡片墙）

- **不使用浮动圆角卡片**。内容组织在 `panel-frame`（`--bg1` 底、1px `--line` 边、`--r-m`）内，面板内部用 **hairline 分格**（`.hline` 水平线 / `.vline` 垂直线 + `.haircell` 格子，padding 12-14px）切分信息，一格多义、密度高。
- 页面结构：左侧 52px 图标导航 rail + 主内容区（max-width 1500px 居中，padding 16-20px）。
- rail：品牌符号 ◈（青色）+ 5-6 个图标位（active 态：`--bg2` 底 + inset 1px 青 25% 边 + 青图标）+ 底部竖排 `EDGE ADVISER` 竖字。窄屏(<900px) rail 变底部 tab 栏（触摸目标 ≥44px）。
- 空状态：居中 eyebrow 风格大写标签 + 一句"该做什么"（如"暂无告警 — 全场判定正常"），不用插画。
- 加载态：面板内 hairline 骨架条（`--bg2` 色块），禁止 spinner 转圈。

---

## 3. 签名元素（仅两个，其余从简）

### 3.1 舰队光谱带（Fleet Spectrum Strip）— 总览页顶部

```
┌──────────────────────────────────────────────────────────┐
│ FLEET SPECTRUM — 点击分段直达设备              5 ASSETS    │
│ ┃ MTR-01 ┃ CHL-01 ┃ PMP-03 ↗ ┃ TWR-01·计划停机 ┃ FDR-01 ! ┃│
└──────────────────────────────────────────────────────────┘
```

- 一条 26px 高的分段条：每资产一段，`flex` 权重 = 快照样本数（无快照则等分）；段色 = 状态色；段内左上角 8.5px mono 短码标签（资产 ID 大写缩写）。
- crit 段加光晕 + `!` 后缀；warn 段 `↗` 后缀（有上升斜率时）。
- hover：段抬亮至 `--bg3` + tooltip（状态/覆盖/最新值，Reka Tooltip）。
- 点击 → `/asset/{id}`。这是总览页的主导航，**总览页不再有卡片墙**。
- 数据源：`GET /api/overview`（status + 样本数 + worst metric）。SSE snapshot/alert 事件到达时对应段即时变色。

### 3.2 判决标线（Reticle）

- 关键判定（状态字 / TRUST 值 / 深度结论标题）用四角括号框出：CSS 伪元素画 9×9px 的 L 形角标（`border-width: 1.5px`，青色或状态色，只画左上与右下两角即可）。
- 语义：像仪器目镜中的读数——"这是被系统背书的判定"。全站同一组件 `<VerdictFrame>`。
- 只允许出现在：总览 verdict 行、资产详情页头、报告结论。**禁止滥用为装饰**。

---

## 4. 信息架构：5 顶级页 + 资产详情页

### 路由表

| 路由 | 页面 | 旧页面对应 |
|---|---|---|
| `/` | 总览 Overview | Dashboard + AssetHub 一半 |
| `/asset/:assetId` | 资产详情 Asset（tabs: 实时/历史/报告/知识/设置） | AssetHub + Monitor + Reports + Knowledge + Assets(阈值) |
| `/alerts` | 告警中心 Alerts | Alerts |
| `/qa` | 问答（全屏模式，QA 抽屉的展开态） | Qa |
| `/audit` | 审计 Audit | Runlog |
| `/settings` | 设置 Settings | Settings |

- 原独立 `/reports`、`/knowledge`、`/assets`、`/feedback` 取消顶级入口：报告与知识并入资产详情 tab；阈值编辑并入资产详情"设置"tab；反馈并入两处——告警确认备注即反馈（后端已联动），资产详情页脚提供"提交反馈"入口。
- **QA 抽屉**：全局组件（rail 上 ◈ 图标 + 资产页"问 AI"按钮触发）。右侧滑入 420px 抽屉，自动携带当前资产上下文（无上下文时显示资产选择器）。"全屏"按钮跳 `/qa`。**根治旧 bug A（?asset= 丢失）**。
- 角色降级（纯前端，沿用现状）：操作工 rail 只显示 总览/资产/告警/反馈入口；QA、审计、设置隐藏。仅 UI 层。

---

## 5. 页面规格

### 5.1 总览 `/`

```
┌ rail ┬──────────────────────────────────────────────┐
│      │ FLEET OVERVIEW                       ● LIVE 14:02 │
│      │ [签名① 舰队光谱带]                              │
│      │ ┌─panel: ACTIVE VERDICTS─────────────────────┐ │
│      │ │ WARN 行: reticle(WARN)│趋势图│TRUST│操作列  │ │
│      │ │ ───────hairline─────────────────────────── │ │
│      │ │ FAULT 行（紧凑）                            │ │
│      │ └────────────────────────────────────────────┘ │
│      │ ┌─panel: 全场台账（每资产一行 hairline 分格）──┐ │
│      │ │ MTR-01  78.2° 3.1mm/s 1490rpm   ● 100%     │ │
│      │ │ CHL-01  24.8° 18.2°            ● 98%      │ │
│      │ │ TWR-01  计划停机中 · 恢复采集 06:00 · 不计告警│ │
│      │ └────────────────────────────────────────────┘ │
```

- **verdicts 面板**只列需要人看的资产：state ∈ {critical, offline, stale} 或 level ∈ {warn, crit} 或有未确认告警。每行：reticle 状态字（warn/crit 展开：主指标趋势 SVG 迷你图 + 斜率标注 + TRUST 条 + 「查详情/问 AI」操作列）；fault 类资产用紧凑行。无需要关注的资产时显示空状态"全场判定正常"。
- **全场台账**：所有资产一行一条（hairline 分隔，非卡片）：ID/名称 + 各指标最新值（mono）+ 覆盖率条 + 状态点。点击行 → 资产详情。
- LIVE 指示：SSE 连接状态（沿用 ConnStatus 逻辑），断开时变灰 `RECONNECTING`。
- 数据源：`GET /api/overview?history=8`（sparkline）、SSE `/stream/telemetry` + `/stream/alerts`。

### 5.2 资产详情 `/asset/:assetId`

```
┌ 总览 / PMP-03 ────────────────────── [立即采集] [问 AI] ┐
│ 冷却泵 PMP-03   ⌜WARN · TRUST 92%⌟(reticle)             │
│ ─ tabs: 实时 | 历史 | 报告(4) | 知识 | 设置 ───────────── │
```

- **页头**：面包屑 + 设备名 + reticle 判定（state · trust）+ 一行门禁事实（样本数/覆盖率/数据年龄，mono）+ 操作：立即采集（POST run-now，同步等待，按钮 loading）、问 AI（开抽屉）。
- **tab 实时**：左列（1.6fr）每指标一个 panel（ECharts 面积线 + norm/warn/crit markArea 色带 + markLine 阈值虚线 + dataZoom），右列（1fr）：最新 AI 解读卡（紫 eyebrow "AI 解读 — HH:mm 来源" + 正文 + 溯源行，数据来自当日报告 conclusion 或上次 QA 缓存）+ 活跃告警卡（左状态色竖条，确认按钮开 AckDialog）。历史 40 窗来自 `/api/assets/{id}/snapshots?limit=40&include_metrics=1`，SSE 增量合并（沿用旧 Monitor 逻辑）。
- **tab 历史**：`snapshots` 翻页表（TanStack Table）：时间/overall/各指标/coverage/trust，行点击展开指标明细。
- **tab 报告**：左侧日期列表（`/reports`），右侧 DATA-SCOPE 徽章（provenance：数据范围/样本/生成参数，**青色系**）+ Markdown 正文（marked + DOMPurify）+ 生成新报告（POST report → token → `/stream/task/{token}` 进度行：queued→start→done/failed，**超时必须复位按钮**，见 §9-B）+ 下载 .md/.json（downloadFile 带 token）。
- **tab 知识**：MEMORY.md 章节导航（按 `##` 分割）+ 正文 + 右列 claims/conflicts 账本（冲突行用 warn 色标 "已被取代"）+ 学习门禁状态展示 + 运行学习（同报告的任务流模式）。
- **tab 设置**（工程师角色）：只读档案（描述/采样频率/窗口/数据源）+ **阈值编辑**（每指标 MetricBand 色带 + 行内编辑表单：PATCH `/metrics/{key}/thresholds`，客户端校验：有限值、warn≤crit、重叠拒绝，错误即时提示）+ 计划停机窗（只读）+ "提交反馈"入口。

### 5.3 告警中心 `/alerts`

- 顶部：级别计数 chips（mono 数字）+ 筛选（资产/级别/已确认，Reka 下拉）。
- 列表（TanStack Table）：状态色左竖条 | 资产 | 标题 | 首次时间 | 持续时长（mono 实时计算）| 操作「确认」。
- 点击行 → **AckDialog**（Reka Dialog）：显示告警详情 + 处理备注 textarea（1-2000 字）+ 确认（POST ack，备注自动入 feedback）。批量确认（按资产/全场）按钮在筛选栏右侧，二次确认。
- 已确认历史折叠在底部区段，备注展示为引用块。
- planned_downtime/unknown/insufficient 类**不出现在本页**（后端已豁免）。

### 5.4 问答 `/qa` + 全局抽屉

- 抽屉与全屏页共用组件 `QaPanel`。结构：资产上下文条（可切换）+ 对话流 + 底部输入（Enter 发送 / Shift+Enter 换行）+ "允许旧数据"开关（`allow_stale`，开启时输入框边框变 warn 色以提示降级）。
- 气泡：用户右对齐 `--bg2`；AI 回答左对齐、**左侧 2px 紫条 + 紫色 eyebrow "AI 解读"**，回答末尾固定溯源行 mono：`依据快照 HH:mm · trust N% · provider`。
- 门禁拒绝（`ok=false, error=no_fresh_snapshot:*`）：渲染为确定性拒绝卡（灰底 + lock 图标 + 原因人话："无新鲜快照（26 分钟未更新），系统拒绝基于旧数据回答"）+ 按钮"允许旧数据重答"。模型故障（`provider_failed`）单独文案："模型服务不可用，请稍后重试"。两种拒绝视觉必须不同。
- 会话按资产分线程，线程列表在抽屉顶部下拉切换（localStorage 持久化历史）。

### 5.5 审计 `/audit`

- 资产选择器 + 结果筛选 chips（**按 `kind` 过滤：pull/analyze/alert/ack/ask/report/learn/threshold…——修复旧 bug C**，"确认"= `kind==='ack'`）+ 成功率 chip。
- TanStack Table：时间（mono）| kind | outcome（ok/warn/fail 着色）| actor | reason | elapsed | window_end。缺口日插一行"该日无记录"。
- 游标分页（`before` + loadMore）+ CSV 导出（downloadFile）。

### 5.6 设置 `/settings`

- 分区面板（hairline 分格）：服务状态（版本/DEMO 徽章(demo=true 时顶部全局红字 DEMO 横幅)/资产数/心跳/健康/网关）、运行观测（模型成功率/MTTA/gated_refusals/磁盘/每资产快照数，来自 `/api/stats`）、运行参数（cron/PIN/工作区，只读）。全部只读展示。

### 5.7 全局顶情

- rail 底部：LIVE 点（SSE 状态）+ 未确认告警计数徽标（crit 红色）。
- demo 模式：rail 顶部红色 `DEMO` 竖标。

---

## 6. 组件清单（`src/components/`，全部新写）

| 组件 | 职责 |
|---|---|
| `ObsPanel.vue` | panel-frame 容器（标题 eyebrow 插槽 + 右侧 meta 插槽） |
| `HairGrid.vue` | hairline 分格布局（props: cols 模板） |
| `VerdictFrame.vue` | reticle 判决标线 |
| `FleetSpectrum.vue` | 舰队光谱带（签名①） |
| `StateDot.vue` | 状态点（可选光晕） |
| `TrustBar.vue` | trust 数字 + 细条 |
| `CoverageBar.vue` | 覆盖率条（≥80 青 / ≥50 warn / 其余 crit） |
| `Sparkline.vue` | 迷你趋势 SVG（无 ECharts，手绘 polyline 即可，减少 bundle） |
| `MetricChart.vue` | ECharts 完整曲线（阈值色带/dataZoom；主题在 `main.ts` **显式注册**——修复旧 bug D） |
| `MetricBand.vue` | 横向阈值色带 + 当前值 marker（设置 tab 用） |
| `LiveNumber.vue` | 数字滚动（动效①） |
| `QaDrawer.vue` / `QaPanel.vue` | 全局问答抽屉 / 复用面板 |
| `AckDialog.vue` | 告警确认对话框 |
| `TaskProgress.vue` | token 任务流进度（报告/学习共用；统一处理 done/failed/**timeout** 三态复位——修复旧 bug B） |
| `ScopeBadge.vue` | DATA-SCOPE provenance 徽章 |
| `MarkdownView.vue` | marked + DOMPurify 安全渲染 |
| `EmptyState.vue` | 空状态 |
| `ConnStatus.vue` | SSE 连接状态 |
| `UiDialog/UiDropdown/UiTooltip/UiInput/UiTextarea/UiSwitch/UiTabs` | Reka UI 薄封装（统一样式+焦点环：2px `--cyan-dim` outline） |

**表格**：告警、历史快照、审计三处用 TanStack Table（样式自绘：hairline 行、mono 时间列、状态色左竖条）。

**图标**：细线几何符号集。用 `lucide-vue-next`（细线、几何、非卡通），stroke-width 1.5，色随上下文。禁止 emoji。

## 7. 状态与数据层（沿用旧逻辑，重组到 `src/stores/`）

- `runtime.ts`（唯一全局 store）：overviewCards / live / alerts / conn / demo / dataFreshnessS / nowSec 秒表；`startStreams()` 双 SSE + 重连 500ms 去抖 resync + visibilitychange 恢复 resync——逻辑照搬旧实现（这部分旧代码质量高）。
- `api/http.ts`：信封解包 / PIN→token / 401 重入 / downloadFile——照搬旧实现，仅移除 Element 依赖（ElMessage → 自绘 toast：右下 hairline 小条，用 Reka Toast）。
- 会话存储沿用 sessionStorage `ea_token`、角色 `ea_role`。
- QA 会话历史：`stores/qa.ts` 新增，localStorage 按资产分线程。

## 8. 工程约束

- 保留 `vite.config.ts`（base `/dist/`、代理）、路由挂载方式、构建产物路径——后端托管零改动。
- `npm run typecheck` 必须过；无 lint 配置则不新增。
- 移除 `element-plus`、`@element-plus/icons-vue` 依赖；新增 `reka-ui`、`@tanstack/vue-table`、`lucide-vue-next`、`@fontsource/ibm-plex-sans`、`@fontsource/ibm-plex-mono`。
- 触摸目标 ≥44px（rail 底部 tab 模式）/ ≥32px（桌面）；键盘焦点可见；`prefers-reduced-motion` 全覆盖。
- demo `config.demo.json` 跑通全部页面（feeder1 的 stale 拒绝路径必须可见）。

## 9. 修复清单（实现时逐项验证）

- **A 跨页上下文**：架构级根治（QA 抽屉带上下文；报告/知识并入资产页）。
- **B** `TaskProgress.vue` 统一 timeout 分支复位 loading。
- **C** 审计筛选按 `kind` 过滤（"确认"= `kind==='ack'`）。
- **D** `main.ts` 显式 `import './theme/echarts'` 注册 `ea-dark`（重命名为 `ea-obs`）。
- **E** pulse/光晕条件改判 `overall_level`，删除 `state==='warn'` 死代码。
- **F** 设置 tab 文案如实描述可编辑性。
- **G** 角色切换保留纯前端降级，UI 上注明"界面视图切换，非权限控制"（诚实原则）。

## 10. 验收清单

1. demo 模式全功能走查：总览光谱带→资产详情（5 tab）→告警确认（备注入 feedback）→QA（正常回答 / 门禁拒绝 / provider 失败三态）→报告生成（任务流三态）→学习→阈值编辑→审计（kind 筛选含 ack）→设置。
2. SSE 断连重连、平板休眠恢复 resync 正常。
3. 操作工角色只见允许入口；PIN 登录/401 重入正常。
4. typecheck 通过；bundle 不含 element-plus。
5. 视觉抽查：无蓝紫渐变、无玻璃卡片、无圆角卡片墙、AI 内容全部带紫标+溯源。
