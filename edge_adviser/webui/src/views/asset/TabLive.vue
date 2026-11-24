<!-- TabLive — the 实时 tab (spec §5.2): left column (1.6fr), one metric chart per
     configured metric (threshold bands + dashed limits); right column (1fr), the
     latest AI interpretation (today's report conclusion — violet, provenance
     line, silently hidden when there is no report) + the active alerts card
     (state-colored bar + 确认).

     HISTORY + LIVE MERGE (carried over from the old Monitor.vue):
       * fetchSnapshots(id, 40, true) → oldest-first rows with per-metric arrays
       * every live `snapshot` payload (the runtime store already drains the ONE
         telemetry socket into rt.live[assetId]) is merged in CHRONOLOGICAL
         position — history is oldest→newest, so a naive push-then-unshift ran
         the chart backwards — deduped by window_end, re-sorted, capped at 60
       * each metric's series picks ONLY its own value (m.max ?? m.mean): the
         first-metric-only shape is an /api/overview sparkline artifact, not
         something this tab should inherit. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRuntime, type SnapshotPayload } from '../../stores/runtime'
import { ackAlert, fetchAlerts, fetchReport, fetchSnapshots } from '../../api'
import { levelMeta, stateMeta, type AlertLevel } from '../../theme/meta'
import { toast } from '../../stores/toast'
import ObsPanel from '../../components/ObsPanel.vue'
import MetricChart from '../../components/MetricChart.vue'
import EmptyState from '../../components/EmptyState.vue'
import type { MetricSpecMeta } from '../../theme/meta'

const props = defineProps<{ assetId: string }>()

const rt = useRuntime()

const card = computed(() => rt.assetById(props.assetId))
const live = computed<SnapshotPayload | null>(() => rt.liveFor(props.assetId) || null)
const displayName = computed(() => card.value?.name || props.assetId)

const loading = ref(false)
const history = ref<SnapshotPayload[]>([])

// -------------------------------------------------------- metric chart feed --
/** metric specs from the card's config — the band + unit source of truth */
const specs = computed<MetricSpecMeta[]>(() =>
  (card.value?.config?.metrics || card.value?.metrics || []) as MetricSpecMeta[])

/** one authoritative metric-key order: newest live metrics → newest history row
 * with metrics → config spec order (same priority ladder as old Monitor) */
const metricKeys = computed<string[]>(() => {
  const lm = live.value?.metrics
  if (lm?.length) return lm.map((m) => m.key)
  const withMetrics = [...history.value].reverse().find((r) => r.metrics?.length)
  if (withMetrics?.metrics?.length) return withMetrics.metrics.map((m) => m.key)
  return specs.value.map((m) => m.key)
})

function specOf(key: string): MetricSpecMeta {
  return specs.value.find((m) => m.key === key) || { key, unit: '' }
}

const unitOf = (key: string): string => specOf(key).unit || ''

function levelFromMetric(lv: string | undefined): AlertLevel {
  if (lv === 'warn') return 'warn'
  if (lv === 'critical' || lv === 'crit') return 'crit'
  return 'ok'
}

/** latest value + level per metric key: live tick → newest history row */
const currents = computed<Record<string, { v: number | null; level: AlertLevel }>>(() => {
  const out: Record<string, { v: number | null; level: AlertLevel }> = {}
  const read = (m: { key: string; max?: number | null; mean?: number | null; level?: string }) => {
    out[m.key] = {
      v: typeof m.max === 'number' ? m.max : m.mean ?? null,
      level: levelFromMetric(m.level),
    }
  }
  for (const m of live.value?.metrics || []) read(m)
  if (Object.keys(out).length) return out
  const row = [...history.value].reverse().find((r) => r.metrics?.length)
  for (const m of row?.metrics || []) read(m)
  return out
})

function fmtVal(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)
}

/** per-metric points, oldest → newest, from the merged history rows */
function seriesOf(key: string): Array<{ ts: number; v: number }> {
  const out: Array<{ ts: number; v: number }> = []
  const seen = new Set<string>()
  for (const r of history.value) {
    if (!r.window_end || seen.has(r.window_end)) continue
    const m = r.metrics?.find((x) => x.key === key)
    const v = m ? (typeof m.max === 'number' ? m.max : m.mean) : undefined
    if (typeof v !== 'number' || !Number.isFinite(v)) continue
    const t = new Date(r.window_end).getTime()
    if (!Number.isFinite(t)) continue
    seen.add(r.window_end)
    out.push({ ts: t / 1000, v })
  }
  return out
}

// ------------------------------------------------------- history + live merge --
async function loadHistory() {
  if (!props.assetId) return
  loading.value = true
  try {
    const rows = await fetchSnapshots(props.assetId, 40, true)
    history.value = Array.isArray(rows) ? rows : []
  } catch {
    toast('历史快照加载失败', 'warn')
  } finally {
    loading.value = false
  }
}

// the store's telemetry handler REPLACES rt.live[assetId] with a fresh object
// on every snapshot event, so this watch fires per tick and merges the payload
// (full per-metric arrays — not the overview's first-metric-only series) into
// the chart history at its chronological position.
watch(live, (v) => {
  if (!v?.window_end || v.asset_id !== props.assetId) return
  const i = history.value.findIndex((r) => r.window_end === v.window_end)
  if (i >= 0) history.value.splice(i, 1)
  history.value.push({ ...v })
  history.value.sort((a, b) => (a.window_end || '').localeCompare(b.window_end || ''))
  history.value = history.value.slice(-60)
})

// ------------------------------------------------------------------- report --
// AI 解读卡 data = today's report conclusion. The API returns {date, report_md,
// meta, …}; the conclusion is the first Executive Summary paragraph (mirrors the
// backend's _extract_conclusion for the learn loop). Any failure — no report
// today, gated, 404 — hides the card silently: "no interpretation" is a normal
// state, not an error surface.
const report = ref<{
  date: string
  report_md?: string | null
  provider?: string
  meta?: { generated_at?: string; provider?: string } | null
} | null>(null)

function todayStr(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function extractConclusion(md: string): string {
  if (!md) return ''
  const seg = md.includes('## Executive Summary')
    ? (md.split('## Executive Summary', 2)[1] || '')
    : md
  const first = seg.split('\n\n').map((s) => s.trim()).find(Boolean) || ''
  return first.replace(/\s*\n\s*/g, ' ').slice(0, 400)
}

const conclusion = computed(() =>
  report.value?.report_md ? extractConclusion(report.value.report_md) : '')

/** HH:mm 来源 — the report's generated_at (UTC iso → local wall clock) */
const reportTime = computed(() => {
  const iso = report.value?.meta?.generated_at || ''
  const t = iso ? new Date(iso).getTime() : NaN
  if (!Number.isFinite(t)) return ''
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
})

const reportSource = computed(() =>
  report.value?.meta?.provider || report.value?.provider || '')

async function loadReport() {
  report.value = null
  try {
    const doc = await fetchReport(props.assetId, todayStr())
    if (doc?.report_md) report.value = doc
  } catch {
    /* 静默隐藏 */
  }
}

// ------------------------------------------------------------------- alerts --
// asset-local unacked alerts (fetched here so the tab owns its own refresh;
// rt.alerts stays the fleet-wide mirror for badges).
interface AlertRow {
  ts: number
  subject?: string
  state?: string
  reason?: string
  headline?: string
  window_end?: string
  overall_level?: string
}

const alerts = ref<AlertRow[]>([])

async function loadAlerts() {
  try {
    const rows = await fetchAlerts(props.assetId, false)
    alerts.value = (Array.isArray(rows) ? rows : []).sort((a, b) => (b.ts || 0) - (a.ts || 0))
  } catch {
    alerts.value = []
  }
}

/** the left bar tone: subject is the actionable vocabulary (warn/critical/
 * offline/stale from server derive_alert); state is the fallback */
function alertColor(a: AlertRow): string {
  const s = a.subject || a.state || ''
  const m = (s === 'crit' ? 'critical' : s) as keyof typeof stateMeta
  return stateMeta[m]?.color || stateMeta.unknown.color
}

function alertLabel(a: AlertRow): string {
  const s = a.subject || a.state || ''
  const m = (s === 'crit' ? 'critical' : s) as keyof typeof stateMeta
  return stateMeta[m]?.label || '告警'
}

const acking = ref<Record<number, boolean>>({})

/** 确认 — a plain ackAlert call for this task (note-less). The full AckDialog
 * (备注 textarea → feedback ledger) is Task 9 and will replace this handler. */
async function ackOne(a: AlertRow) {
  if (acking.value[a.ts]) return
  acking.value[a.ts] = true
  try {
    await ackAlert(props.assetId, a.ts)
    alerts.value = alerts.value.filter((x) => x.ts !== a.ts)
    toast('告警已确认')
  } catch (e) {
    toast(e instanceof Error ? `确认失败：${e.message}` : '确认失败', 'crit')
  } finally {
    acking.value[a.ts] = false
  }
}

// ---------------------------------------------------------------- lifecycle --
async function loadAll() {
  await loadHistory()
  loadReport()
  loadAlerts()
}

onMounted(() => { loadAll() })

// asset switch (route param change → new prop value) reloads everything
watch(() => props.assetId, (v, o) => {
  if (!v || v === o) return
  report.value = null
  alerts.value = []
  loadAll()
})
</script>

<template>
  <div class="tl">
    <div class="tl-charts">
      <ObsPanel
        v-for="key in metricKeys"
        :key="key"
        :title="key"
        :meta="unitOf(key) || undefined"
      >
        <template #actions>
          <span v-if="currents[key]" class="mc-now mono">
            <span class="mc-now-val" :style="{ color: levelMeta[currents[key].level].color }">
              {{ fmtVal(currents[key].v) }}<span class="mc-now-unit">{{ unitOf(key) }}</span>
            </span>
            <span class="mc-now-level" :style="{ color: levelMeta[currents[key].level].color }">
              {{ levelMeta[currents[key].level].label }}
            </span>
          </span>
        </template>
        <div class="mc-wrap">
          <MetricChart
            v-if="seriesOf(key).length"
            :spec="specOf(key)"
            :points="seriesOf(key)"
            :unit="unitOf(key)"
          />
          <EmptyState
            v-else-if="!loading"
            label="暂无该指标样本"
            hint="点击「立即采集」获取第一个窗口"
          />
          <div v-else class="mc-skeleton" aria-hidden="true" />
        </div>
      </ObsPanel>

      <ObsPanel v-if="!loading && !metricKeys.length" title="实时">
        <EmptyState label="暂无指标数据" hint="配置指标或先采集一个窗口" />
      </ObsPanel>
    </div>

    <div class="tl-side">
      <!-- ─── AI 解读卡（紫 = AI 专属色） ─────────────────────────────────────── -->
      <ObsPanel
        v-if="conclusion"
        title="AI 解读"
        :meta="reportTime ? `${reportTime} 来源` : undefined"
      >
        <div class="ai">
          <p class="ai-text">{{ conclusion }}</p>
          <div class="ai-prov mono">
            <span class="ai-marker">AI</span>
            <span>依据报告 {{ report?.date }} {{ reportTime }}{{ reportSource ? ` · ${reportSource}` : '' }}</span>
          </div>
        </div>
      </ObsPanel>

      <!-- ─── 活跃告警卡 ──────────────────────────────────────────────────────── -->
      <ObsPanel title="活跃告警" :meta="alerts.length ? `${alerts.length} 条` : undefined">
        <template v-if="alerts.length">
          <div v-for="a in alerts" :key="a.ts" class="al" :style="{ '--al-c': alertColor(a) }">
            <div class="al-head">
              <span class="al-tag" :style="{ color: alertColor(a) }">{{ alertLabel(a) }}</span>
              <span class="al-ts mono">{{ a.window_end || '' }}</span>
              <button
                type="button"
                class="al-ack"
                :disabled="!!acking[a.ts]"
                @click="ackOne(a)"
              >{{ acking[a.ts] ? '确认中…' : '确认' }}</button>
            </div>
            <div class="al-body">{{ a.headline || a.reason || '' }}</div>
          </div>
        </template>
        <EmptyState v-else label="无活跃告警" :hint="`${displayName} 当前无需处理`" />
      </ObsPanel>
    </div>
  </div>
</template>

<style scoped>
.tl {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: 14px;
  align-items: start;
}
.tl-charts { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.tl-side { display: flex; flex-direction: column; gap: 14px; min-width: 0; }

/* chart panel head: current value readout */
.mc-now { display: inline-flex; align-items: baseline; gap: 8px; }
.mc-now-val { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
.mc-now-unit { font-size: 10px; color: var(--tx-dim); margin-left: 2px; }
.mc-now-level { font-size: 10px; letter-spacing: .08em; }
.mc-wrap { padding: 4px 8px 6px; }
.mc-skeleton { height: 230px; }

/* AI interpretation card — violet ONLY here (spec: 紫 = AI 解读专属色) */
.ai { padding: 2px 2px 4px; border-left: 2px solid var(--violet); margin: 12px 14px; }
.ai-text { margin: 0; font-size: 12.5px; color: var(--tx-hi); line-height: 1.65; }
.ai-prov { margin-top: 10px; display: flex; align-items: center; gap: 8px;
  font-size: 10px; color: var(--tx-dim); }
.ai-marker { color: var(--violet); font-weight: 700; letter-spacing: .14em;
  border: 1px solid var(--violet); border-radius: 2px; padding: 0 4px; }

/* alert card: state-colored left bar (spec §5.3 visual language) */
.al { position: relative; padding: 10px 14px 10px 16px; border-bottom: 1px solid var(--line); }
.al:last-child { border-bottom: 0; }
.al::before { content: ''; position: absolute; left: 0; top: 8px; bottom: 8px;
  width: 2px; background: var(--al-c, var(--unknown)); }
.al-head { display: flex; align-items: center; gap: 10px; }
.al-tag { font-size: 11px; font-weight: 700; letter-spacing: .1em; flex-shrink: 0; }
.al-ts { flex: 1; font-size: 10px; color: var(--tx-dim);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.al-ack { border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11px; padding: 4px 12px; border-radius: var(--r-s); cursor: pointer;
  min-height: 28px; white-space: nowrap; }
.al-ack:hover { background: var(--bg3); border-color: var(--cyan-dim); color: var(--tx-hi); }
.al-ack:disabled { opacity: .55; cursor: default; }
.al-body { margin-top: 5px; font-size: 12px; color: var(--tx); line-height: 1.5; }

@media (max-width: 1099.98px) {
  .tl { grid-template-columns: 1fr; }
}
</style>
