<script setup lang="ts">
// Sparkline — hand-drawn SVG polyline trend thumbnail (spec §6: 无 ECharts，
// 手绘 polyline 即可，减少 bundle). Props per the agreed interface:
// { points: number[]; color: string } — a plain array of numbers (the view maps
// card.runtime.series → p.value) and a stroke color from state/level meta.
//
// Rendering notes:
//  * the polyline normalizes itself to its own min/max, so a flat or
//    single-point series still paints a readable line instead of dividing by 0
//  * the last point gets a small end-dot so the newest reading is locatable
//  * a non-scaling stroke keeps the 1.5px line crisp at the viewBox scaling
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ points: number[]; color?: string }>(),
  { color: '' },
)

const W = 100
const H = 28
const PAD = 3

// finite values only — a null/NaN hole would otherwise collapse the path
const vals = computed(() =>
  (props.points || []).filter((v) => typeof v === 'number' && Number.isFinite(v)))
const hasLine = computed(() => vals.value.length >= 2)
// single finite point → end-dot only, no line (no direction to draw)
const single = computed(() => vals.value.length === 1 ? vals.value[0] : null)

const pts = computed(() => {
  const a = vals.value
  if (a.length < 2) return ''
  const min = Math.min(...a)
  const max = Math.max(...a)
  const span = max - min
  // span===0 → park the line mid-height (flat series must not divide by zero)
  const y = (v: number) => span === 0
    ? PAD + (H - PAD * 2) / 2
    : PAD + (1 - (v - min) / span) * (H - PAD * 2)
  const x = (i: number) => (i / (a.length - 1)) * (W - PAD * 2) + PAD
  return a.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
})

const end = computed(() => {
  const a = vals.value
  if (!a.length) return null
  const min = Math.min(...a)
  const max = Math.max(...a)
  const span = max - min
  const y = span === 0 ? PAD + (H - PAD * 2) / 2
    : PAD + (1 - (a[a.length - 1] - min) / span) * (H - PAD * 2)
  return { x: (W - PAD * 2) + PAD, y }
})
</script>

<template>
  <svg
    class="spark"
    :viewBox="`0 0 ${W} ${H}`"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <polyline
      v-if="hasLine"
      :points="pts"
      fill="none"
      :stroke="color || 'var(--cyan)'"
      stroke-width="1.5"
      stroke-linejoin="round"
      stroke-linecap="round"
      vector-effect="non-scaling-stroke"
    />
    <circle
      v-if="hasLine && end"
      :cx="end.x"
      :cy="end.y"
      r="1.8"
      :fill="color || 'var(--cyan)'"
    />
    <circle
      v-else-if="single !== null"
      :cx="W / 2"
      :cy="H / 2"
      r="1.8"
      :fill="color || 'var(--cyan)'"
    />
  </svg>
</template>

<style scoped>
.spark { display: block; width: 100%; height: 28px; }
</style>
