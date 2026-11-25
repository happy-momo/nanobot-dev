<script setup lang="ts">
// FleetSpectrum — signature element ① (spec §3.1): one segmented strip
// where each asset is a segment flex-weighted by its snapshot sample count
// (equal when no snapshot), colored by stateMeta, labeled with an uppercase
// truncated asset-id short-code. crit segments glow + "!"; warn with a positive
// slope gets "↗". Hover lightens to --bg3 with a Reka tooltip (状态/覆盖/最新值);
// click → /asset/{id}. This is the overview's primary navigation — no card wall.
//
// Data contract: cards = runtime.overviewCards. The strip re-colors live from
// the store's SSE-driven card mutations (no local copy of the data).
import { TooltipContent, TooltipPortal, TooltipProvider, TooltipRoot, TooltipTrigger } from 'reka-ui'
import { stateMeta, type AssetState } from '../theme/meta'
import type { AssetCard } from '../stores/runtime'

const props = defineProps<{ cards: AssetCard[] }>()
const emit = defineEmits<{ (e: 'select', assetId: string): void }>()

const NORM = 180  // a full 60s @ 1Hz window; weights cap here so a single
                  // healthy asset cannot flatten the others to slivers

function stateOf(card: AssetCard): AssetState {
  const s = (card.runtime.state || '') as AssetState
  return stateMeta[s] ? s : 'unknown'
}

/** dark-bg states (unknown family): label flips to a readable light gray */
function isDimState(st: AssetState): boolean {
  return st === 'planned_downtime' || st === 'insufficient' || st === 'unknown'
}

/**
 * crit-grade segments glow: critical itself plus offline (meta.ts treats data
 * faults as crit — same red, so a dead asset must pulse too, not sit still).
 */
function isCritGrade(st: AssetState): boolean {
  return st === 'critical' || st === 'offline'
}

function colorOf(card: AssetCard): string {
  return stateMeta[stateOf(card)].color
}

/** flex-grow weight: sample-count based, equal when nothing has data */
function weightOf(card: AssetCard): number {
  const n = card.runtime.sample_count || 0
  return n > 0 ? Math.min(n / NORM, 1) : 0.12
}

/** short-code: uppercase asset id, truncated to 6 chars per the brief */
function shortCode(id: string): string {
  const u = (id || '').toUpperCase()
  return u.length > 6 ? u.slice(0, 6) : u
}

/** planned downtime / insufficient / unknown carry an explanatory suffix */
function stateSuffix(card: AssetCard): string {
  const st = stateOf(card)
  if (st === 'planned_downtime') return ' · 计划停机'
  if (st === 'insufficient') return ' · 数据不足'
  if (st === 'unknown') return ' · 未知'
  return ''
}

function suffix(card: AssetCard): string {
  const st = stateOf(card)
  if (st === 'critical' || st === 'offline') return ' !'
  if (st === 'warn') return slopeOf(card) > 0 ? ' ↗' : ''
  return ''
}

/**
 * WHAT THE SERIES ACTUALLY IS: runtime.series is the asset's FIRST configured
 * metric (backend `_sparkline_series` / telemetry `metrics[0]`; the point
 * carries no key). So the ↗ may only fire when a first metric exists at all —
 * mirroring the backend's `if not first_key: return []` gate — and with the
 * same rounded-delta rule as the Overview verdict slope, so the strip and the
 * verdict row can never advertise a rise the other one doesn't.
 */
function firstKeyOf(card: AssetCard): string {
  const cfg = card.config?.metrics || card.metrics || []
  for (const m of cfg) { if (m && m.key) return m.key }
  return ''
}

/** rounded positive slope of the first-metric series, oldest → newest */
function slopeOf(card: AssetCard): number {
  if (!firstKeyOf(card)) return 0
  const s = card.runtime.series || []
  if (s.length < 2) return 0
  const a = s[s.length - 2].value
  const b = s[s.length - 1].value
  if (a === null || b === null) return 0
  return Math.round((b - a) * 10) / 10
}

/** tooltip facts: 状态 / 覆盖 / 最新值 (spec §3.1) */
function tipLines(card: AssetCard): string[] {
  const st = stateMeta[stateOf(card)]
  const cov = Math.round((card.runtime.coverage || 0) * 100)
  const vals = card.runtime.latest_values || {}
  const keys = Object.keys(vals)
  const l: string[] = [
    `状态 ${st.label} · 覆盖 ${cov}%`,
  ]
  if (keys.length) {
    for (const k of keys) l.push(`${k} ${fmtVal(vals[k])}`)
  } else {
    l.push('无最新值')
  }
  return l
}

function fmtVal(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)
}

function onKey(e: KeyboardEvent, id: string) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    emit('select', id)
  }
}
</script>

<template>
  <TooltipProvider :delay-duration="200">
    <div
      class="spec-wrap"
      role="group"
      aria-label="设备状态：选择设备查看详情"
    >
      <TooltipRoot v-for="c in props.cards" :key="c.asset_id">
        <TooltipTrigger
          as-child
        >
          <button
            type="button"
            class="seg"
            :class="{ 'seg-crit': isCritGrade(stateOf(c)), 'seg-dim': isDimState(stateOf(c)) }"
            :style="{
              '--seg-c': colorOf(c),
              '--seg-w': String(weightOf(c)),
            }"
            :aria-label="`${c.asset_id} ${stateMeta[stateOf(c)].label}，点击进入设备详情`"
            @click="emit('select', c.asset_id)"
            @keydown="onKey($event, c.asset_id)"
          >
            <span class="seg-label mono">{{ shortCode(c.asset_id) }}{{ suffix(c) }}{{ stateSuffix(c) }}</span>
          </button>
        </TooltipTrigger>
        <TooltipPortal>
          <TooltipContent class="spec-tip" :side-offset="6">
            <span class="spec-tip-name">{{ c.name }}</span>
            <span v-for="(t, i) in tipLines(c)" :key="i" class="spec-tip-line mono">{{ t }}</span>
          </TooltipContent>
        </TooltipPortal>
      </TooltipRoot>
    </div>
  </TooltipProvider>
</template>

<style scoped>
.spec-wrap {
  display: flex;
  height: 26px;
  border: 1px solid var(--line);
  border-radius: var(--r-s);
  overflow: hidden;
  background: var(--bg1);
}

.seg {
  position: relative;
  flex: 1 1 0;
  flex-grow: var(--seg-w, 0.12);
  min-width: 34px;
  border: 0;
  padding: 0;
  margin: 0;
  background: var(--seg-c);
  color: var(--bg0);
  cursor: pointer;
  text-align: left;
}

/* hover 抬亮至 --bg3（spec §3.1）— background swap, no transition (spec §1.4) */
.seg:hover, .seg:focus-visible { background: var(--bg3); }

/* crit 段光晕 + 呼吸（动效③）；glow 属于 crit 级状态（critical/offline，见 isCritGrade） */
.seg-crit { box-shadow: inset 0 0 0 1px var(--line); }
.seg-crit::after {
  content: '';
  position: absolute;
  inset: 0;
  box-shadow: 0 0 12px 1px var(--seg-c);
  animation: fs-breathe 2s ease-in-out infinite;
  pointer-events: none;
}
@keyframes fs-breathe { 50% { opacity: .45; } }

.seg-label {
  position: absolute;
  left: 6px;
  top: 3px;
  font-size: 8.5px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--bg0);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: calc(100% - 12px);
  line-height: 1.2;
}

/* dark-bg states (unknown family) can't carry a --bg0 label */
.seg-dim .seg-label { color: var(--tx-dim); }

@media (max-width: 899.98px) {
  .spec-wrap { height: 44px; }
  .seg { min-width: 0; flex-grow: 1; }
  .seg-label { position: static; display: block; max-width: 100%; padding: 0 2px;
    overflow: hidden; text-align: center; text-overflow: ellipsis; }
}
</style>

<style>
/* Tooltip content needs to live outside the scoped boundary (ported to body by
   TooltipPortal) — global block, theme colors only. */
.spec-tip {
  z-index: 60;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  background: var(--bg2);
  border: 1px solid var(--line-2);
  border-radius: var(--r-s);
  font-size: 11px;
  color: var(--tx);
  max-width: 260px;
}
.spec-tip-name { color: var(--tx-hi); font-weight: 600; }
.spec-tip-line { font-size: 10.5px; color: var(--tx-dim); font-family: var(--font-mono); }
</style>
