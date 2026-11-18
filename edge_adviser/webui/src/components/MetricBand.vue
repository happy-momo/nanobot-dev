<template>
  <div class="band-wrap">
    <div class="band-head">
      <span class="key">{{ metric.key }}</span>
      <span class="unit muted">{{ metric.unit }}</span>
      <span class="value num" :style="{ color: tone }">{{ valueText }}</span>
      <span class="lv muted">({{ levelLabel }})</span>
    </div>
    <div class="band">
      <div v-for="(seg, i) in segments" :key="i" class="seg"
           :style="{ width: seg.width + '%', background: seg.tone }" />
      <div v-if="markerLeft !== null" class="marker" :style="{ left: markerLeft + '%' }" />
    </div>
    <div class="scale muted">
      <span>{{ lo ?? '—' }}</span>
      <span>{{ hi ?? '—' }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
// Horizontal norm/warn/crit band with a current-value marker (Assets page).
// The [lo, hi] axis brackets all defined thresholds; each contiguous span is
// colored by the band its midpoint falls into (green / amber / red).
import { computed } from 'vue'
import { fmtNum, levelMeta } from '../theme'
import type { MetricSpec } from '../stores/runtime'

const props = defineProps<{ metric: MetricSpec; value?: number | null; level?: string }>()

const tone = computed(() => levelMeta[props.level || 'unknown']?.fg || 'var(--ea-text)')
const levelLabel = computed(() => levelMeta[props.level || 'unknown']?.label || props.level)
const valueText = computed(() => fmtNum(props.value ?? null, 2))

const lo = computed(() => {
  const m = props.metric
  const cands = [m.crit_min, m.warn_min, m.norm_min].filter((v) => v != null)
  return cands.length ? Math.min(...(cands as number[])) : null
})
const hi = computed(() => {
  const m = props.metric
  const cands = [m.crit_max, m.warn_max, m.norm_max].filter((v) => v != null)
  return cands.length ? Math.max(...(cands as number[])) : null
})

const segments = computed(() => {
  const m = props.metric
  const edges = [lo.value, m.warn_min, m.warn_max, m.crit_min, m.crit_max, hi.value]
    .filter((v) => v != null) as number[]
  const uniq = [...new Set(edges)].sort((a, b) => a - b)
  const out: Array<{ width: number; tone: string }> = []
  for (let i = 0; i < uniq.length - 1; i++) {
    const mid = (uniq[i] + uniq[i + 1]) / 2
    let t = 'rgba(61,220,151,0.35)'          // norm green
    if (m.crit_min != null && mid < m.crit_min) t = 'rgba(244,85,79,0.40)'
    else if (m.crit_max != null && mid > m.crit_max) t = 'rgba(244,85,79,0.40)'
    else if ((m.warn_min != null && mid < m.warn_min) || (m.warn_max != null && mid > m.warn_max))
      t = 'rgba(245,177,76,0.40)'
    out.push({ width: ((uniq[i + 1] - uniq[i]) / (hi.value! - lo.value!)) * 100, tone: t })
  }
  return out
})

const markerLeft = computed(() => {
  const v = props.value ?? null
  if (v == null || lo.value == null || hi.value == null || hi.value === lo.value) return null
  return Math.min(100, Math.max(0, ((v - lo.value) / (hi.value - lo.value)) * 100))
})
</script>

<style scoped>
.band-wrap { display: flex; flex-direction: column; gap: 4px; }
.band-head { display: flex; align-items: baseline; gap: 6px; }
.key { font-weight: 600; font-size: 13px; }
.unit { font-size: 11px; }
.value { font-size: 15px; }
.lv { font-size: 11px; }
.band { position: relative; height: 8px; border-radius: 4px; overflow: visible;
  display: flex; background: var(--ea-panel-soft); }
.seg { height: 100%; }
.marker {
  position: absolute; top: -4px; bottom: -4px; width: 3px; border-radius: 2px;
  background: #fff; box-shadow: 0 0 6px rgba(255,255,255,0.7);
  transform: translateX(-50%);
}
.scale { display: flex; justify-content: space-between; font-size: 11px; }
</style>
