<!-- AssetDetail `/asset/:assetId` (spec §5.2) — the asset shell:
       breadcrumb (总览 / ID) + 设备名 + VerdictFrame(state · trust) +
       one mono gate-fact row (样本数 / 覆盖率 / 数据年龄) +
       [立即采集] (POST run-now, sync await + loading + toast + refresh) +
       [问 AI] (opens the global QaDrawer with THIS asset id — root fix A) +
       the tab bar 实时 | 历史 | 报告(计数徽标) | 知识 | 设置.

     Tab bodies: TabLive is fully wired here; 历史/报告/知识/设置 are stubs for
     Task 7. The no-param route `/asset` picks the first known asset client-side
     (the router deliberately does not bake a fake :assetId into the URL). -->
<script setup lang="ts">
import { computed, inject, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MessageSquare, RefreshCw } from '@lucide/vue'
import { useRuntime, type SnapshotPayload } from '../stores/runtime'
import { fetchReports, runNow } from '../api'
import { levelMeta, stateMeta, trustMeta, type AlertLevel, type AssetState, type TrustLevel } from '../theme/meta'
import { localizeConditionText } from '../copy'
import { toast } from '../stores/toast'
import VerdictFrame from '../components/VerdictFrame.vue'
import StateDot from '../components/StateDot.vue'
import ObsPanel from '../components/ObsPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import { createRequestSequence } from '../utils/requestSequence'
import TabLive from './asset/TabLive.vue'
import TabHistory from './asset/TabHistory.vue'
import TabReports from './asset/TabReports.vue'
import TabKnowledge from './asset/TabKnowledge.vue'
import TabSettings from './asset/TabSettings.vue'

const route = useRoute()
const router = useRouter()
const rt = useRuntime()

// --------------------------------------------------------------- asset id --
// route param wins; `/asset` (no param) resolves the first known card and
// replaces the URL so refresh/deep-link keeps working.
const assetId = ref<string>(String(route.params.assetId || ''))
const resolving = ref(false)   // `/asset` with no cards yet — wait for loadOverview

watch(() => route.params.assetId, (v) => {
  // a param change on the SAME route component must reload the page data
  if (v && v !== assetId.value) assetId.value = String(v)
})

// `/asset` → first card once the registry arrives (also covers the direct
// deep-link race where the view mounts before loadOverview resolves)
watch([() => rt.overviewCards.length, () => route.params.assetId], () => {
  const p = route.params.assetId
  if (p) return
  if (!rt.overviewCards.length) return
  const first = rt.overviewCards[0].asset_id
  if (first && first !== assetId.value) {
    assetId.value = first
    router.replace({ path: `/asset/${first}` })
  }
}, { immediate: true })

const known = computed(() => !!rt.assetById(assetId.value))

// ----------------------------------------------------------- verdict header --
const card = computed(() => rt.assetById(assetId.value))
const live = computed<SnapshotPayload | null>(() => rt.liveFor(assetId.value) || null)

const displayName = computed(() => card.value?.name || assetId.value)
const kind = computed(() => card.value?.kind || '')
const kindLabel = computed(() => ({
  motor: '电机',
  pump: '泵',
  chiller: '冷水机组',
  cooling_tower: '冷却塔',
  feeder: '给料机',
  simulator: '模拟设备',
}[kind.value] || kind.value))

const stateOf = computed<AssetState>(() => {
  const s = (live.value?.status || card.value?.runtime.state) || ''
  return stateMeta[s as AssetState] ? (s as AssetState) : 'unknown'
})

const levelOf = computed<AlertLevel>(() => {
  const lv = (live.value?.overall_level || card.value?.runtime.overall_level) || ''
  if (lv === 'warn') return 'warn'
  if (lv === 'critical' || lv === 'crit') return 'crit'
  return 'ok'
})

// trust vocabulary full|partial|low|none → TrustLevel high|medium|low
const TRUST_LEVEL: Record<string, TrustLevel> = {
  full: 'high', partial: 'medium', low: 'low', none: 'low',
}
const trustOf = computed<TrustLevel>(() =>
  TRUST_LEVEL[live.value?.trust || card.value?.runtime.trust || 'none'] || 'low')

// the reticle renders the ALARM verdict word (warn/严重/正常) — state dot keeps
// the machine state separate so "healthy but alarm-level warn" stays legible
const verdictColor = computed(() => levelMeta[levelOf.value].color)
const verdictWord = computed(() => levelMeta[levelOf.value].label)

const stateReason = computed(() =>
  localizeConditionText(
    (card.value?.runtime as { state_reason?: string } | undefined)?.state_reason
    || live.value?.headline,
  ))

// --------------------------------------------------------- gate fact row (mono) --
const sampleCount = computed(() =>
  live.value?.sample_count ?? card.value?.runtime.sample_count ?? 0)
const coveragePct = computed(() => {
  const v = live.value?.coverage ?? card.value?.runtime.coverage ?? 0
  return Math.round(Math.min(Math.max((v || 0) * 100, 0), 100))
})
const windowEnd = computed(() =>
  live.value?.window_end || card.value?.runtime.window_end || '')
const dataAge = computed(() => {
  if (!windowEnd.value) return '—'
  const t = new Date(windowEnd.value).getTime()
  if (!Number.isFinite(t)) return '—'
  const s = Math.max(0, Math.floor(rt.nowSec * 1000 - t))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
})
/** data age past the freshness gate → the age cell reads warn (事实行不撒谎) */
const ageStale = computed(() => {
  const t = windowEnd.value ? new Date(windowEnd.value).getTime() : 0
  if (!t || !rt.dataFreshnessS) return false
  return rt.nowSec * 1000 - t > rt.dataFreshnessS * 1000
})

// ---------------------------------------------------------------- report tab badge --
// 报告(N) — the count of ARCHIVED report days, fetched once per asset.
const reportCount = ref<number | null>(null)

// seq guard: a slow report list for asset A must not become asset B's badge
const reportCountSequence = createRequestSequence()
async function loadReportCount() {
  const mySeq = reportCountSequence.next()
  const asset = assetId.value
  reportCount.value = null
  if (!asset) return
  try {
    const rows = await fetchReports(asset)
    if (!reportCountSequence.isCurrent(mySeq) || asset !== assetId.value) return
    reportCount.value = Array.isArray(rows) ? rows.length : 0
  } catch {
    if (!reportCountSequence.isCurrent(mySeq) || asset !== assetId.value) return
    reportCount.value = 0   // badge shows 0 rather than lying about the count
  }
}

// -------------------------------------------------------------------- tabs --
const TABS = [
  { key: 'live', label: '实时', icon: 'radar' },
  { key: 'history', label: '历史', icon: 'history' },
  { key: 'reports', label: '报告', icon: 'reports' },
  { key: 'knowledge', label: '知识', icon: 'knowledge' },
  { key: 'settings', label: '设置', icon: 'settings' },
] as const

type TabKey = typeof TABS[number]['key']
const activeTab = ref<TabKey>('live')

function onTabKeydown(event: KeyboardEvent): void {
  const tab = event.target instanceof HTMLElement
    ? event.target.closest<HTMLButtonElement>('[role="tab"]')
    : null
  if (!tab) return
  const current = TABS.findIndex((item) => item.key === tab.dataset.tabKey)
  if (current < 0) return
  let next = current
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (current + 1) % TABS.length
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (current - 1 + TABS.length) % TABS.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = TABS.length - 1
  else return
  const tablist = event.currentTarget
  event.preventDefault()
  activeTab.value = TABS[next].key
  void nextTick(() => {
    if (tablist instanceof HTMLElement) {
      tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
    }
  })
}

// ---------------------------------------------------------------- 立即采集 --
const running = ref(false)
// bumped after a successful manual run so TabLive reloads its history even if
// the SSE tick hasn't landed yet (prop-driven, no window event plumbing)
const runRefreshTick = ref(0)

async function onRunNow() {
  if (!assetId.value || running.value) return
  running.value = true
  try {
    const snap = await runNow(assetId.value)
    void snap
    toast(`采集完成 — ${displayName.value}`)
    runRefreshTick.value++
  } catch (e) {
    toast(e instanceof Error ? `采集失败：${e.message}` : '采集失败', 'crit')
  } finally {
    running.value = false
  }
}

// ---------------------------------------------------------------- 问 AI --
const openQa = inject<(assetId?: string) => void>('openQa', () => {})
function onAskAi() {
  openQa(assetId.value)   // drawer carries THIS asset's context (fix A)
}

// ---------------------------------------------------------------- lifecycle --
onMounted(() => {
  if (!assetId.value) resolving.value = true
  loadReportCount()
})

watch(assetId, () => {
  loadReportCount()
  activeTab.value = 'live'   // a different asset starts on 实时 again
})
</script>

<template>
  <div class="ad">
    <!-- ─── breadcrumb ─────────────────────────────────────────────────────── -->
    <nav class="crumb" aria-label="面包屑">
      <button type="button" class="crumb-link" @click="router.push({ name: 'overview' })">总览</button>
      <span class="crumb-sep">/</span>
      <span class="crumb-cur mono">{{ assetId || '…' }}</span>
    </nav>

    <!-- ─── verdict header ─────────────────────────────────────────────────── -->
    <template v-if="assetId && (known || live)">
      <header class="head">
        <div class="head-main">
          <div class="head-name-row">
            <StateDot :state="stateOf" :glow="stateOf === 'critical' || stateOf === 'offline'" />
            <h1 class="head-name">{{ displayName }}</h1>
            <span v-if="kind" class="eyebrow head-kind">{{ kindLabel }}</span>
          </div>

          <div class="head-verdict">
            <VerdictFrame :color="verdictColor">
              <span class="vf-word" :style="{ color: verdictColor }">{{ verdictWord }}</span>
              <span class="vf-sep">·</span>
              <span class="vf-trust mono" :style="{ color: trustMeta[trustOf].color }">
                可信度 {{ trustMeta[trustOf].label }}
              </span>
            </VerdictFrame>
            <span v-if="stateReason" class="head-reason">{{ stateReason }}</span>
          </div>

          <!-- 门禁事实行 (mono) -->
          <div class="facts mono">
            <span class="fact"><span class="fact-k">样本数</span><span class="fact-v">{{ sampleCount }}</span></span>
            <span class="fact-sep" />
            <span class="fact"><span class="fact-k">覆盖率</span><span class="fact-v">{{ coveragePct }}%</span></span>
            <span class="fact-sep" />
            <span class="fact">
              <span class="fact-k">数据年龄</span>
              <span class="fact-v" :class="{ 'fact-stale': ageStale }">{{ dataAge }}</span>
            </span>
          </div>
        </div>

        <div class="head-actions">
          <button type="button" class="btn" :disabled="running" @click="onRunNow">
            <RefreshCw :size="14" :stroke-width="1.5" :class="{ 'spin': running }" />
            <span>{{ running ? '采集中…' : '立即采集' }}</span>
          </button>
          <button type="button" class="btn btn-ai" @click="onAskAi">
            <MessageSquare :size="14" :stroke-width="1.5" />
            <span>问 AI</span>
          </button>
        </div>
      </header>

      <!-- ─── tab bar ────────────────────────────────────────────────────────── -->
      <nav class="tabs" role="tablist" aria-label="资产详情视图" @keydown="onTabKeydown">
        <button
          v-for="t in TABS"
          :key="t.key"
          type="button"
          class="tab"
          role="tab"
          :data-tab-key="t.key"
          :id="`asset-tab-${t.key}`"
          :tabindex="activeTab === t.key ? 0 : -1"
          :aria-selected="activeTab === t.key"
          :aria-controls="activeTab === t.key ? `asset-panel-${t.key}` : undefined"
          :class="{ 'tab-on': activeTab === t.key }"
          @click="activeTab = t.key"
        >
          <span class="tab-label">{{ t.label }}</span>
          <span v-if="t.key === 'reports' && reportCount" class="tab-badge mono">{{ reportCount }}</span>
        </button>
      </nav>

      <!-- ─── tab body ───────────────────────────────────────────────────────── -->
      <div
        :id="`asset-panel-${activeTab}`"
        class="tab-body"
        role="tabpanel"
        tabindex="0"
        :aria-labelledby="`asset-tab-${activeTab}`"
      >
        <TabLive v-if="activeTab === 'live'" :asset-id="assetId" :refresh-tick="runRefreshTick" />
        <TabHistory v-else-if="activeTab === 'history'" :asset-id="assetId" />
        <TabReports v-else-if="activeTab === 'reports'" :asset-id="assetId" />
        <TabKnowledge v-else-if="activeTab === 'knowledge'" :asset-id="assetId" />
        <TabSettings v-else-if="activeTab === 'settings'" :asset-id="assetId" />
      </div>
    </template>

    <!-- ─── unknown / not-yet-resolved asset ─────────────────────────────────── -->
    <ObsPanel v-else title="设备信息">
      <EmptyState
        :label="rt.ready ? '未找到该设备' : '加载设备…'"
        :hint="rt.ready ? '检查 asset_id 或回到总览选择设备' : '设备注册表正在加载'"
      />
      <div v-if="rt.ready" class="unknown-actions">
        <button type="button" class="btn" @click="router.push({ name: 'overview' })">回到总览</button>
      </div>
    </ObsPanel>
  </div>
</template>

<style scoped>
.ad { display: flex; flex-direction: column; gap: 12px; padding-bottom: 24px; }

/* ── breadcrumb ── */
.crumb { display: flex; align-items: center; gap: 8px; font-size: 11px; }
.crumb-link { background: none; border: 0; padding: 0; color: var(--tx-dim);
  cursor: pointer; font-size: 11px; letter-spacing: .04em; }
.crumb-link:hover { color: var(--cyan); }
.crumb-sep { color: var(--tx-dim); opacity: .6; }
.crumb-cur { color: var(--tx); letter-spacing: .06em; }

/* ── header ── */
.head { display: flex; align-items: flex-start; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; }
.head-main { display: flex; flex-direction: column; gap: 7px; min-width: 0; }
.head-name-row { display: flex; align-items: center; gap: 9px; }
.head-name { margin: 0; font-size: 17px; font-weight: 600; letter-spacing: .01em;
  color: var(--tx-hi); }
.head-kind { border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px;
  letter-spacing: .12em; }

.head-verdict { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.vf-word { font-size: 13px; font-weight: 700; letter-spacing: .14em; }
.vf-sep { color: var(--tx-dim); margin: 0 2px; }
.vf-trust { font-size: 11px; letter-spacing: .1em; }
.head-reason { font-size: 11.5px; color: var(--tx-dim); }

/* gate-fact row — all mono, cyan values (facts are cyan, spec §0) */
.facts { display: flex; align-items: center; gap: 14px; margin-top: 2px; }
.fact { display: inline-flex; align-items: baseline; gap: 6px; }
.fact-k { font-size: 10px; color: var(--tx-dim); letter-spacing: .08em; }
.fact-v { font-size: 12px; color: var(--cyan); font-variant-numeric: tabular-nums; }
.fact-stale { color: var(--warn); }
.fact-sep { width: 1px; height: 12px; background: var(--line); }

.head-actions { display: flex; gap: 8px; align-items: center; }

.btn {
  display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 12px; padding: 7px 14px; border-radius: var(--r-s); cursor: pointer;
  letter-spacing: .04em; min-height: 32px; white-space: nowrap;
}
.btn:hover:not(:disabled) { background: var(--bg3); border-color: var(--cyan-dim); color: var(--tx-hi); }
.btn:disabled { opacity: .6; cursor: default; }
.btn-ai { color: var(--violet); }
.btn-ai:hover:not(:disabled) { border-color: var(--violet); background: var(--violet-bg); color: var(--violet); }
.spin { animation: ad-spin 1s linear infinite; }
@keyframes ad-spin { to { transform: rotate(360deg); } }

/* ── tab bar: hairline bottom border, active = cyan text + 2px cyan underline ── */
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--line); }
.tab {
  position: relative; display: inline-flex; align-items: center; gap: 7px;
  background: none; border: 0; border-bottom: 2px solid transparent;
  color: var(--tx-dim); font-size: 12.5px; letter-spacing: .06em;
  padding: 8px 14px; cursor: pointer; min-height: 36px;
}
.tab:hover { color: var(--tx); }
.tab-on { color: var(--cyan); border-bottom-color: var(--cyan); }
.tab-badge { font-size: 10px; font-weight: 700; color: var(--cyan);
  border: 1px solid var(--cyan-dim); border-radius: 999px; padding: 0 6px;
  background: var(--cyan-dim); line-height: 1.5; }

.tab-body { min-width: 0; }

.unknown-actions { display: flex; justify-content: center; padding-bottom: 16px; }

/* breakpoint: phone→single-column grid (project-wide 699.98px) */
@media (max-width: 699.98px) {
  .head-actions { width: 100%; }
  .tab { padding: 8px 10px; }
}
</style>
