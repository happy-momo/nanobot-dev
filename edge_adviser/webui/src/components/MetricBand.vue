<script setup lang="ts">
// MetricBand — horizontal threshold band + current-value marker (spec §6:
// 设置 tab 用; the deliverable Task 6 deferred here). The [lo, hi] axis
// brackets all finite thresholds plus norm edges; each contiguous span is
// colored by the band its midpoint falls in (ok / warn / crit) — the same
// "band = alarm zone" visual language as MetricChart's markArea and the
// backend's derive_level (band exceedance ⇒ warn/crit).
//
// Canvas-free, CSS-var driven (no hex in .vue CSS); the rgba tints below use
// color-mix on the state tokens so they stay in sync with meta.ts.
import { computed } from 'vue'
import { levelMeta, type AlertLevel, type MetricSpecMeta } from '../theme/meta'

const props = withDefaults(defineProps<{
  metric: MetricSpecMeta
  value?: number | null
  level?: string
}>(), { value: null, level: '' })

const lo = computed(() => {
  const m = props.metric
  const cands = [m.crit_min, m.warn_min, m.norm_min]
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  return cands.length ? Math.min(...cands) : null
})
const hi = computed(() => {
  const m = props.metric
  const cands = [m.crit_max, m.warn_max, m.norm_max]
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  return cands.length ? Math.max(...cands) : null
})

const hasAxis = computed(() =>
  lo.value !== null && hi.value !== null && hi.value > lo.value)

// threshold edges that must be marked on the axis (only those inside it)
const edges = computed(() => {
  if (!hasAxis.value) return []
  const m = props.metric
  const raw = [m.warn_min, m.warn_max, m.crit_min, m.crit_max]
  const out: Array<{ pos: number; tone: string; label: string }> = []
  const seen = new Set<number>()
  for (const v of raw) {
    if (typeof v !== 'number' || !Number.isFinite(v)) continue
    const clamped = Math.min(Math.max(v, lo.value!), Math.max(hi.value!, v))
    const pos = ((v - lo.value!) / (hi.value! - lo.value!)) * 100
    if (pos < 0 || pos > 100 || seen.has(Math.round(pos * 100))) continue
    seen.add(Math.round(pos * 100))
    const tone = v === m.crit_min || v === m.crit_max ? 'crit'
      : (v === m.warn_min || v === m.warn_max ? 'warn' : 'dim')
    const label = v === m.crit_min ? 'crit_min' : v === m.crit_max ? 'crit_max'
      : v === m.warn_min ? 'warn_min' : v === m.warn_max ? 'warn_max' : ''
    out.push({ pos, tone, label })
    void clamped
  }
  return out.sort((a, b) => a.pos - b.pos)
})

// contiguous spans colored by the band their midpoint sits in
const spans = computed(() => {
  if (!hasAxis.value) return []
  const m = props.metric
  const stops = [lo.value!, m.warn_min, m.warn_max, m.crit_min, m.crit_max, hi.value!]
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  const uniq = [...new Set(stops)].sort((a, b) => a - b)
  const out: Array<{ width: number; tone: 'ok' | 'warn' | 'crit' }> = []
  for (let i = 0; i < uniq.length - 1; i++) {
    const mid = (uniq[i] + uniq[i + 1]) / 2
    let tone: 'ok' | 'warn' | 'crit' = 'ok'
    if (m.crit_min != null && mid < m.crit_min) tone = 'crit'
    else if (m.crit_max != null && mid > m.crit_max) tone = 'crit'
    else if ((m.warn_min != null && mid < m.warn_min) ||
             (m.warn_max != null && mid > m.warn_max)) tone = 'warn'
    out.push({
      width: ((uniq[i + 1] - uniq[i]) / (hi.value! - lo.value!)) * 100,
      tone,
    })
  }
  return out
})

/** current value marker position (clamped into the axis) */
const marker = computed(() => {
  const v = props.value
  if (typeof v !== 'number' || !Number.isFinite(v) || !hasAxis.value) return null
  const p = ((v - lo.value!) / (hi.value! - lo.value!)) * 100
  return Math.min(100, Math.max(0, p))
})

/** level tone for the readout — level is the backend's AlertLevel spelling */
const level = computed<AlertLevel>(() => {
  const lv = props.level
  if (lv === 'warn') return 'warn'
  if (lv === 'critical' || lv === 'crit') return 'crit'
  return 'ok'
})

const fmt = (v: number | null | undefined): string => {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)
}
</script>

<template>
  <div class="mb">
    <div class="mb-head">
      <span class="mb-key mono">{{ metric.key }}</span>
      <span v-if="metric.unit" class="mb-unit mono">{{ metric.unit }}</span>
      <span v-if="marker !== null" class="mb-val mono" :style="{ color: levelMeta[level].color }">
        {{ fmt(value) }}
      </span>
      <span class="mb-lv" :style="{ color: levelMeta[level].color }">
        {{ levelMeta[level].label }}
      </span>
    </div>

    <div class="mb-track">
      <template v-if="hasAxis">
        <span
          v-for="(s, i) in spans"
          :key="i"
          class="mb-span"
          :class="`mb-${s.tone}`"
          :style="{ width: s.width + '%' }"
        />
        <span
          v-for="(e, i) in edges"
          :key="`e${i}`"
          class="mb-edge"
          :class="`mb-edge-${e.tone}`"
          :style="{ left: e.pos + '%' }"
          :title="e.label"
        />
        <span
          v-if="marker !== null"
          class="mb-marker"
          :style="{ left: marker + '%' }"
        />
      </template>
      <span v-else class="mb-flat" title="无可用阈值">—</span>
    </div>

    <div class="mb-scale mono">
      <span>{{ fmt(lo) }}</span>
      <span>{{ fmt(hi) }}</span>
    </div>
  </div>
</template>

<style scoped>
.mb { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.mb-head { display: flex; align-items: baseline; gap: 8px; }
.mb-key { font-size: 12px; font-weight: 600; color: var(--tx-hi); letter-spacing: .04em; }
.mb-unit { font-size: 10px; color: var(--tx-dim); }
.mb-val { margin-left: auto; font-size: 13px; font-weight: 600;
  font-variant-numeric: tabular-nums; }
.mb-lv { font-size: 10px; letter-spacing: .08em; }

.mb-track { position: relative; height: 10px; border-radius: 5px;
  background: var(--bg3); overflow: visible; display: flex; min-width: 0; }
.mb-span { height: 100%; }
.mb-ok { background: color-mix(in srgb, var(--ok) 22%, transparent); }
.mb-warn { background: color-mix(in srgb, var(--warn) 30%, transparent); }
.mb-crit { background: color-mix(in srgb, var(--crit) 34%, transparent); }

/* threshold edges: 1.5px ticks in the band tone (dashed-line twin of the chart) */
.mb-edge { position: absolute; top: -3px; bottom: -3px; width: 1.5px;
  transform: translateX(-50%); border-radius: 1px; }
.mb-edge-crit { background: var(--crit); }
.mb-edge-warn { background: var(--warn); }
.mb-edge-dim { background: var(--tx-dim); }

/* current-value marker: cyan-white needle (facts are cyan) */
.mb-marker { position: absolute; top: -4px; bottom: -4px; width: 3px;
  border-radius: 2px; background: var(--cyan); transform: translateX(-50%);
  box-shadow: 0 0 6px var(--cyan-dim); }
.mb-flat { align-self: center; margin: 0 auto; font-size: 10px; color: var(--tx-dim); }

.mb-scale { display: flex; justify-content: space-between; font-size: 10px;
  color: var(--tx-dim); font-variant-numeric: tabular-nums; }
</style>
