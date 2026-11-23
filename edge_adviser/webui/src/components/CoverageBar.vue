<script setup lang="ts">
// CoverageBar — sample coverage readout: mono % + 4px fill bar + optional
// sample count. Tone per the brief: >=80 cyan / >=50 warn / else crit.
//
// `coverage` accepts a 0–1 fraction (backend snapshot coverage is a fraction)
// or a 0–100 percentage — the thresholds apply to the percentage form.
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ coverage: number; samples?: number }>(),
  { samples: undefined },
)

const pct = computed(() => {
  const v = Number(props.coverage) || 0
  const p = v <= 1 ? v * 100 : v
  return Math.round(Math.min(Math.max(p, 0), 100))
})
const color = computed(() =>
  pct.value >= 80 ? 'var(--cyan)' : pct.value >= 50 ? 'var(--warn)' : 'var(--crit)')
</script>

<template>
  <span class="cb mono" role="img" :aria-label="`覆盖率 ${pct}%`" title="覆盖率 = 实际采样 / 期望采样">
    <span class="cb-track"><span class="cb-fill" :style="{ width: pct + '%', background: color }" /></span>
    <span class="cb-num" :style="{ color }">{{ pct }}<span class="cb-pct">%</span></span>
    <span v-if="samples !== undefined" class="cb-samples" :title="`${samples} 个样本`">{{ samples }}<span class="cb-unit"> 样本</span></span>
  </span>
</template>

<style scoped>
.cb { display: inline-flex; align-items: center; gap: 7px; }
.cb-track { width: 44px; height: 4px; background: var(--bg3); border-radius: 2px; overflow: hidden; flex-shrink: 0; }
.cb-fill { display: block; height: 100%; border-radius: 2px; }
.cb-num { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.cb-pct { font-size: 9px; opacity: .75; }
.cb-samples { font-size: 10px; color: var(--tx-dim); font-variant-numeric: tabular-nums; white-space: nowrap; }
.cb-unit { opacity: .8; }
</style>
