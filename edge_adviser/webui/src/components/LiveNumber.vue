<script setup lang="ts">
// LiveNumber — animated numeric readout (动效①, spec §1.4): on `value` change
// the displayed number eases from the old to the new one over 300ms with an
// ease-out cubic (requestAnimationFrame). `prefers-reduced-motion: reduce`
// short-circuits straight to the target value. Numbers are always mono with
// tabular numerals so digits don't jitter while easing.
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{ value: number; digits?: number; glow?: boolean; color?: string }>(),
  { digits: 0, glow: false, color: '' },
)

const displayed = ref(props.value)
const DURATION = 300

let rafId = 0
let reduced: MediaQueryList | null = null
const prefersReducedMotion = (): boolean => {
  if (reduced === null) {
    reduced = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null
  }
  return reduced ? reduced.matches : false
}

const stop = () => {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0 }
}

const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3)

watch(
  () => props.value,
  (to) => {
    const from = displayed.value
    stop()
    if (!Number.isFinite(to) || prefersReducedMotion() || to === from) {
      displayed.value = Number.isFinite(to) ? to : 0
      return
    }
    const start = performance.now()
    const step = (now: number) => {
      const t = Math.min((now - start) / DURATION, 1)
      displayed.value = from + (to - from) * easeOutCubic(t)
      if (t < 1) rafId = requestAnimationFrame(step)
      else rafId = 0
    }
    rafId = requestAnimationFrame(step)
  },
  { immediate: true },
)

onBeforeUnmount(stop)

const text = computed(() => displayed.value.toFixed(props.digits))
</script>

<template>
  <span class="ln mono" :class="{ 'num-glow': glow }" :style="color ? { color } : undefined">{{ text }}</span>
</template>

<style scoped>
.ln { font-variant-numeric: tabular-nums; font-feature-settings: 'tnum' 1; line-height: 1; }
</style>
