<script setup lang="ts">
// TrustBar — data-trustworthiness readout (mono % + 5px hairline bar), the
// cyan "确定性事实" cue (spec §1.1: trust 一律用青色). `level` (when the caller
// already resolved a TrustLevel) overrides the default cyan tone via trustMeta.
//
// `trust` accepts a 0–1 fraction OR a 0–100 percentage (backend snapshots
// carry fractions; some UI copy uses percent) — clamped to 0..100.
import { computed } from 'vue'
import { trustMeta, type TrustLevel } from '../theme/meta'

const props = withDefaults(
  defineProps<{ trust: number; level?: TrustLevel }>(),
  { level: undefined },
)

const pct = computed(() => {
  const v = Number(props.trust) || 0
  return Math.round(Math.min(Math.max(v <= 1 ? v * 100 : v, 0), 100))
})
const color = computed(() =>
  props.level ? trustMeta[props.level].color : 'var(--cyan)')
const label = computed(() =>
  props.level ? trustMeta[props.level].label : 'TRUST')
</script>

<template>
  <span class="tb mono" role="img" :aria-label="`可信度 ${pct}%`" :title="label">
    <span class="tb-num num-glow" :style="{ color }">{{ pct }}<span class="tb-pct">%</span></span>
    <span class="tb-track"><span class="tb-fill" :style="{ width: pct + '%', background: color }" /></span>
  </span>
</template>

<style scoped>
.tb { display: inline-flex; align-items: center; gap: 7px; }
.tb-num { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.tb-pct { font-size: 10px; opacity: .75; }
.tb-track { width: 44px; height: 5px; background: var(--bg3); border-radius: 3px; overflow: hidden; flex-shrink: 0; }
.tb-fill { display: block; height: 100%; border-radius: 3px; }
</style>
