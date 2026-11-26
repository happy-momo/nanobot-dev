<script setup lang="ts">
// Overview `/` (spec §5.1): the observation-deck home page.
//   设备总览 heading + LIVE indicator
//   signature① FleetSpectrum strip (primary navigation, no card wall)
//   ACTIVE VERDICTS panel — only assets needing a human:
//     state ∈ {critical, offline, stale} || overall_level ∈ {warn, crit}
//     || unacked alerts. warn/crit rows expand (reticle verdict word +
//     sparkline + slope annotation + trust + 查详情/问 AI); fault-type assets
//     (offline/stale) render as a compact row with 确认告警.
//   全场台账 panel — one hairline row per asset (ID/name + mono metric values
//     + coverage bar + state dot), row click → /asset/{id}.
//
// Data: runtime store only — loadOverview/history filled it, SSE mutations
// keep segments, rows and numbers moving without any local polling here.
import { computed, inject, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useRuntime, type AssetCard } from '../stores/runtime'
import { stateMeta, levelMeta, type AssetState, type AlertLevel } from '../theme/meta'
import { localizeConditionText } from '../copy'
import { toast } from '../stores/toast'
import ObsPanel from '../components/ObsPanel.vue'
import FleetSpectrum from '../components/FleetSpectrum.vue'
import VerdictFrame from '../components/VerdictFrame.vue'
import StateDot from '../components/StateDot.vue'
import TrustBar from '../components/TrustBar.vue'
import CoverageBar from '../components/CoverageBar.vue'
import EmptyState from '../components/EmptyState.vue'
import ConnStatus from '../components/ConnStatus.vue'
import Sparkline from '../components/Sparkline.vue'

const rt = useRuntime()
const router = useRouter()
const openQa = inject<(assetId?: string) => void>('openQa', () => {})

// ------------------------------------------------------------- conn display --
// same truth-telling rule as the App shell: an open stream whose last event is
// older than the freshness gate reads RECONNECTING (rt.nowSec keeps it ticking).
// ConnStatus has no 'offline' member — map it onto reconnecting (same gray).
const connDisplay = computed<'online' | 'reconnecting' | 'connecting'>(() => {
  if (rt.conn !== 'online') return rt.conn === 'offline' ? 'reconnecting' : rt.conn
  const staleMs = rt.nowSec * 1000 + 500 - rt.lastEventAt
  return staleMs > 60_000 ? 'reconnecting' : 'online'
})

// -------------------------------------------------------------- state utils --
function stateOf(c: AssetCard): AssetState {
  const s = (c.runtime.state || '') as AssetState
  return stateMeta[s] ? s : 'unknown'
}

/** backend overall_level ∈ info|warn|critical (+unknown when gated) → AlertLevel */
function levelOf(c: AssetCard): AlertLevel {
  const lv = c.runtime.overall_level || ''
  return lv === 'warn' || lv === 'critical' || lv === 'crit'
    ? (lv === 'critical' ? 'crit' : 'warn')
    : 'ok'
}

/** the read gate blocks interpretation on fault states — never fake a trend */
function isFaultState(c: AssetCard): boolean {
  const st = stateOf(c)
  return st === 'offline' || st === 'stale'
}

const unackedOf = (id: string) => rt.alertCountFor(id)

// ------------------------------------------------------------- verdict rows --
// warn/crit assets get the expanded verdict row; offline/stale (with no alarm
// level of their own) get the compact fault row. Everything else — healthy,
// planned downtime, insufficient, unknown — never needs a human here.
const verdictAssets = computed(() =>
  rt.overviewCards.filter((c) => {
    const st = stateOf(c)
    if (st === 'critical' || st === 'offline' || st === 'stale') return true
    const lv = c.runtime.overall_level || ''
    if (lv === 'warn' || lv === 'critical' || lv === 'crit') return true
    return unackedOf(c.asset_id) > 0
  }))

const expandedAssets = computed(() =>
  verdictAssets.value.filter((c) => levelOf(c) !== 'ok'))

/** offline/stale rows whose verdict is already shown by an expanded row */
const compactFaults = computed(() => {
  const ids = new Set(expandedAssets.value.map((c) => c.asset_id))
  return verdictAssets.value.filter((c) => isFaultState(c) && !ids.has(c.asset_id))
})

// ------------------------------------------------------------ trend & slope --
// WHAT THE SERIES ACTUALLY IS: `/api/overview` runtime.series is built by the
// backend `_sparkline_series` from the asset's FIRST configured metric (same
// for the telemetry SSE path, which appends data.metrics[0]), and the point
// carries no metric key. So the trend thumbnail's label/slope/unit must
// describe that first metric — never the worst-band one, or pump1 would read
// "↘ 0.1mm/s" over a temperature line. Worst-band identity stays where it
// belongs: the verdict state word + ledger values.
/** the card's first configured metric key = the plotted series' actual key
 * (mirrors backend `next(m.key for m in metrics if m)`) */
function firstKeyOf(c: AssetCard): string {
  const cfg = c.config?.metrics || c.metrics || []
  for (const m of cfg) { if (m && m.key) return m.key }
  return ''
}

function unitOf(c: AssetCard, key: string): string {
  const spec = (c.config?.metrics || c.metrics || []).find((m) => m.key === key)
  return spec?.unit || ''
}

interface Trend { pts: number[]; slope: number }

/** sparkline of the FIRST metric's history, oldest → newest (spec: history=8) */
function trendOf(c: AssetCard): Trend {
  const pts = (c.runtime.series || [])
    .map((p) => p.value)
    .filter((v): v is number => v !== null)
  if (!firstKeyOf(c) || pts.length < 2) return { pts: [], slope: 0 }
  return { pts, slope: pts[pts.length - 1] - pts[pts.length - 2] }
}

function slopeText(t: Trend, unit: string): string {
  if (!t.pts.length) return ''
  // arrow direction uses the ROUNDED delta (a -0.04 jitter is not a trend),
  // but the magnitude prints the RAW |delta| so a -0.04 drift reads "↘ 0.0"
  // instead of a bare arrow with no number
  const r = Math.round(t.slope * 10) / 10
  const arrow = r > 0 ? '↗' : r < 0 ? '↘' : '→'
  return `${arrow} ${Math.abs(t.slope).toFixed(1)}${unit}/窗口`
}

/** rising series on a warn verdict gets the warm tone (visual "↗" cue) */
function slopeColor(t: Trend): string {
  return Math.round(t.slope * 10) / 10 > 0 ? 'var(--warn)' : 'var(--tx-dim)'
}

// ------------------------------------------------------------------- trust --
// Backend trust is a vocabulary (full|partial|low|none), the bar needs a
// number. Presentation mapping (client-side): full→100 / partial→60 /
// low→30 / none→0; the level prop drives the color so the bar never looks
// healthier than the verdict it came from.
const TRUST_PCT: Record<string, number> = { full: 100, partial: 60, low: 30, none: 0 }
const TRUST_LEVEL: Record<string, 'high' | 'medium' | 'low'> = {
  full: 'high', partial: 'medium', low: 'low', none: 'low',
}
function trustPct(c: AssetCard): number {
  return TRUST_PCT[c.runtime.trust || 'none'] ?? 0
}
function trustLevelOf(c: AssetCard): 'high' | 'medium' | 'low' {
  return TRUST_LEVEL[c.runtime.trust || 'none'] ?? 'low'
}

// -------------------------------------------------------------- ledger rows --
/** mono readout per metric: key + value + unit (spec: 各指标最新值 mono) */
function ledgerCells(c: AssetCard): Array<{ k: string; v: string; unit: string }> {
  const cfg = c.config?.metrics || c.metrics || []
  const vals = c.runtime.latest_values || {}
  if (cfg.length) {
    return cfg.map((m) => ({ k: m.key, v: fmtVal(vals[m.key]), unit: m.unit || '' }))
  }
  return Object.keys(vals).map((k) => ({ k, v: fmtVal(vals[k]), unit: '' }))
}

function fmtVal(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)
}

/** fault/gated assets show the gate fact instead of metric numbers (spec 5.1) */
function ledgerNote(c: AssetCard): string {
  const st = stateOf(c)
  if (st === 'planned_downtime') return '计划停机中 · 不计告警'
  if (st === 'stale') return '数据过期 · 判定已冻结'
  if (st === 'offline') return '数据断连 · 判定已冻结'
  if (st === 'insufficient') return '数据不足 · 待积累样本'
  if (st === 'unknown') return '无数据 · 状态未知'
  return ''
}

// ------------------------------------------------------------------ actions --
function openAsset(id: string) { router.push({ path: `/asset/${id}` }) }

function askAi(id: string) { openQa(id) }

const acking = ref<Record<string, boolean>>({})

/** 确认告警 — ack the asset's newest unacked alert via the store action
 * (which POSTs and updates the local record in one step). */
async function ackAsset(c: AssetCard) {
  const a = rt.alerts.find((x) => x.asset_id === c.asset_id && !x.acked)
  if (!a) return
  acking.value[c.asset_id] = true
  try {
    await rt.acknowledge(c.asset_id, a.ts)
    toast(`已确认 ${c.name} 的告警`)
  } catch (e) {
    toast(e instanceof Error ? `确认失败：${e.message}` : '确认失败', 'crit')
  } finally {
    acking.value[c.asset_id] = false
  }
}

// ------------------------------------------------------------------- mount --
onMounted(() => {
  // history=8 sparks the verdict trend thumbnails; App.vue pre-loads it too,
  // this covers a cold deep-link straight to `/`
  if (!rt.overviewCards[0]?.runtime?.series) rt.loadOverview(true)
})
</script>

<template>
  <div class="page">
    <!-- ─── header ─────────────────────────────────────────────────────────── -->
    <header class="head">
      <div>
        <span class="eyebrow">设备运行概况</span>
        <h1 class="title">设备总览</h1>
      </div>
      <ConnStatus :conn="connDisplay" :last-event-at="rt.lastEventAt" />
    </header>

    <!-- ─── signature① 舰队光谱带 ───────────────────────────────────────────── -->
    <div v-if="rt.overviewCards.length" class="spectrum-block">
      <div class="spec-meta">
        <span class="eyebrow">设备状态 · 选择设备查看详情</span>
        <span class="eyebrow mono">{{ rt.overviewCards.length }} 台设备</span>
      </div>
      <FleetSpectrum :cards="rt.overviewCards" @select="openAsset" />
    </div>

    <template v-if="rt.overviewCards.length">
      <!-- ─── ACTIVE VERDICTS ────────────────────────────────────────────────── -->
      <ObsPanel title="需关注设备" :meta="`${verdictAssets.length} 项`">
        <EmptyState
          v-if="!verdictAssets.length"
          label="全场判定正常"
          hint="无告警、无数据故障 — 各设备按预期运行"
        />
        <template v-else>
          <!-- expanded rows: warn / crit -->
          <div v-for="c in expandedAssets" :key="c.asset_id" class="vrow">
            <div class="vrow-main" @click="openAsset(c.asset_id)">
              <div class="vrow-verdict">
                <VerdictFrame :color="levelMeta[levelOf(c)].color">
                  <span class="vf-word" :style="{ color: levelMeta[levelOf(c)].color }">
                    {{ levelMeta[levelOf(c)].label }}
                  </span>
                </VerdictFrame>
              </div>
              <div class="vrow-ident">
                <span class="vrow-name">{{ c.name }}</span>
                <span class="vrow-id mono">{{ c.asset_id }}</span>
              </div>
              <div class="vrow-spark">
                <Sparkline
                  v-if="trendOf(c).pts.length >= 2"
                  :points="trendOf(c).pts"
                  :color="levelMeta[levelOf(c)].color"
                />
                <span
                  class="vrow-slope mono"
                  :style="{ color: slopeColor(trendOf(c)) }"
                >{{ slopeText(trendOf(c), unitOf(c, firstKeyOf(c))) }}</span>
              </div>
              <div class="vrow-trust">
                <span class="eyebrow">可信度</span>
                <TrustBar :trust="trustPct(c)" :level="trustLevelOf(c)" />
              </div>
              <div class="vrow-actions">
                <button type="button" class="act" @click.stop="openAsset(c.asset_id)">查详情</button>
                <button type="button" class="act act-ai" @click.stop="askAi(c.asset_id)">问 AI</button>
              </div>
              <span
                v-if="unackedOf(c.asset_id) > 0"
                class="vrow-badge mono"
                :style="{ color: levelMeta[levelOf(c)].color }"
                :title="`${unackedOf(c.asset_id)} 条未确认告警`"
              >{{ unackedOf(c.asset_id) }}</span>
            </div>
            <div class="vrow-headline">{{ localizeConditionText(c.runtime.headline || c.runtime.state_reason) }}</div>
          </div>

          <!-- compact fault rows: offline / stale (gated — no trend to show) -->
          <template v-if="compactFaults.length">
            <div class="hline" />
            <div
              v-for="c in compactFaults"
              :key="`f-${c.asset_id}`"
              class="frow"
              @click="openAsset(c.asset_id)"
            >
              <StateDot :state="stateOf(c)" :glow="stateOf(c) === 'offline'" />
              <span class="frow-name">{{ c.name }}</span>
              <span class="frow-id mono">{{ c.asset_id }}</span>
              <span class="frow-reason">{{ localizeConditionText(c.runtime.state_reason) || stateMeta[stateOf(c)].label }}</span>
              <span
                v-if="unackedOf(c.asset_id) > 0"
                class="frow-badge mono"
                :style="{ color: stateMeta[stateOf(c)].color }"
                :title="`${unackedOf(c.asset_id)} 条未确认告警`"
              >{{ unackedOf(c.asset_id) }}</span>
              <button
                v-if="unackedOf(c.asset_id) > 0"
                type="button"
                class="act"
                :disabled="!!acking[c.asset_id]"
                @click.stop="ackAsset(c)"
              >{{ acking[c.asset_id] ? '确认中…' : '确认告警' }}</button>
            </div>
          </template>
        </template>
      </ObsPanel>

      <!-- ─── 全场台账 ───────────────────────────────────────────────────────── -->
      <ObsPanel title="全部设备" :meta="`${rt.overviewCards.length} 台`">
        <div
          v-for="c in rt.overviewCards"
          :key="c.asset_id"
          class="lrow"
          role="button"
          tabindex="0"
          @click="openAsset(c.asset_id)"
          @keydown.enter="openAsset(c.asset_id)"
        >
          <span class="lrow-id mono">{{ c.asset_id }}</span>
          <span class="lrow-name">{{ c.name }}</span>
          <span v-if="ledgerNote(c)" class="lrow-note">{{ ledgerNote(c) }}</span>
          <span v-else class="lrow-vals mono">
            <span v-for="cell in ledgerCells(c)" :key="cell.k" class="lrow-val" :title="cell.k">
              <span class="lrow-key">{{ cell.k }}</span> {{ cell.v }}<span class="lrow-unit">{{ cell.unit }}</span>
            </span>
          </span>
          <span class="lrow-cov"><CoverageBar :coverage="c.runtime.coverage" :samples="c.runtime.sample_count" /></span>
          <span class="lrow-dot">
            <StateDot :state="stateOf(c)" :glow="stateOf(c) === 'critical' || stateOf(c) === 'offline'" />
          </span>
          <span
            v-if="unackedOf(c.asset_id) > 0"
            class="lrow-alert mono"
            :title="`${unackedOf(c.asset_id)} 条未确认告警`"
          >{{ unackedOf(c.asset_id) }}</span>
        </div>
      </ObsPanel>
    </template>

    <ObsPanel v-else-if="rt.ready" title="全场台账">
      <EmptyState label="暂无设备配置" hint="检查 config.json 的 assets 列表后重试" />
    </ObsPanel>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 14px; padding-bottom: 24px; }

/* ── header ── */
.head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }
.title { margin: 2px 0 0; font-size: 17px; font-weight: 600; letter-spacing: .01em; }

/* ── spectrum ── */
.spectrum-block { display: flex; flex-direction: column; gap: 6px; }
.spec-meta { display: flex; justify-content: space-between; align-items: center; }

/* ── verdict rows (expanded) ── */
.vrow { position: relative; padding: 12px 14px 10px; border-bottom: 1px solid var(--line); }
/* last vrow when NO fault rows follow (the .hline/.frow then own the divider) */
.vrow:last-of-type:not(:last-child) { border-bottom: 0; }
.vrow-main {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(140px, 1.1fr) minmax(150px, 1.4fr) auto auto;
  gap: 14px;
  align-items: center;
  cursor: pointer;
}
.vrow-verdict { display: flex; align-items: center; }
.vf-word { font-size: 13px; font-weight: 700; letter-spacing: .14em; }
.vrow-ident { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.vrow-name { font-size: 13px; font-weight: 600; color: var(--tx-hi);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vrow-id { font-size: 10px; color: var(--tx-dim); letter-spacing: .06em; }
.vrow-spark { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.vrow-spark :deep(.spark) { height: 24px; }
.vrow-slope { font-size: 10px; color: var(--tx-dim); }
.vrow-trust { display: flex; flex-direction: column; gap: 3px; align-items: flex-start; }
.vrow-actions { display: flex; gap: 6px; }

.act {
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11px; padding: 5px 10px; border-radius: var(--r-s); cursor: pointer;
  letter-spacing: .04em; white-space: nowrap;
  /* touch-target floor (review Important): ≥32px even at the 10px type scale */
  min-height: 32px;
}
.act:hover { background: var(--bg3); border-color: var(--cyan-dim); color: var(--tx-hi); }
.act:disabled { opacity: .55; cursor: default; }
.act-ai { color: var(--violet); }
.act-ai:hover { border-color: var(--violet); background: var(--violet-bg); }

.vrow-badge, .frow-badge {
  font-size: 11px; font-weight: 700;
  border: 1px solid var(--line-2); border-radius: 999px;
  padding: 1px 7px; background: var(--bg2);
}
.vrow-badge { position: absolute; top: 8px; right: 12px; }
.vrow-headline { margin-top: 8px; font-size: 11.5px; color: var(--tx-dim); }

/* ── fault rows (compact) ── */
.frow {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 14px; cursor: pointer;
}
.frow:hover { background: var(--bg2); }
.frow-name { font-size: 12.5px; font-weight: 600; color: var(--tx-hi); }
.frow-id { font-size: 10px; color: var(--tx-dim); letter-spacing: .06em; }
.frow-reason { flex: 1; font-size: 11.5px; color: var(--tx-dim);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ── ledger rows ── */
.lrow {
  display: grid;
  grid-template-columns: 72px minmax(110px, 170px) 1fr auto auto auto;
  gap: 12px;
  align-items: center;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
}
.lrow:last-child { border-bottom: 0; }
.lrow:hover, .lrow:focus-visible { background: var(--bg2); }
.lrow-id { font-size: 11px; color: var(--tx-dim); letter-spacing: .06em; }
.lrow-name { font-size: 12.5px; color: var(--tx-hi); font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lrow-note { font-size: 11.5px; color: var(--tx-dim); }
.lrow-vals { display: flex; gap: 16px; flex-wrap: wrap; min-width: 0; }
.lrow-val { font-size: 12px; color: var(--tx-hi); white-space: nowrap; }
.lrow-key { color: var(--tx-dim); font-size: 10px; }
.lrow-unit { color: var(--tx-dim); font-size: 10px; margin-left: 2px; }
.lrow-cov, .lrow-dot { display: inline-flex; }
.lrow-alert { font-size: 11px; font-weight: 700; color: var(--crit);
  border: 1px solid var(--line-2); border-radius: 999px; padding: 1px 7px;
  background: var(--bg2); }

/* breakpoint: tablet→stack (project-wide 899.98px; phone grid = 699.98px,
   detail-tab side rail = 1099.98px; do not invent new widths) */
@media (max-width: 899.98px) {
  .vrow-main { grid-template-columns: auto 1fr; }
  .vrow-spark { grid-column: 1 / -1; }
  .vrow-trust { flex-direction: row; align-items: center; gap: 8px; }
  .vrow-actions { grid-column: 1 / -1; }
  .lrow { grid-template-columns: 64px 1fr auto auto; }
  .lrow-vals { grid-column: 1 / -1; }
}
</style>
