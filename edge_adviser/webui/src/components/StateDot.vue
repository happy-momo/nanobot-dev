<script setup lang="ts">
// StateDot — status dot colored by AssetState (spec §1.1 mapping). Optional
// pulse halo: spec §1.1 allows a glow ONLY for critical ("唯一允许光晕的状态"),
// so an explicit glow=true is ignored for every other state.
import { computed } from 'vue'
import { stateMeta, type AssetState } from '../theme/meta'

const props = defineProps<{ state: AssetState; glow?: boolean }>()

const color = computed(() => stateMeta[props.state]?.color || stateMeta.unknown.color)
const halo = computed(() => !!props.glow && !!stateMeta[props.state]?.glow)
</script>

<template>
  <span class="sd" role="img" :aria-label="stateMeta[props.state]?.label || state">
    <span
      class="sd-dot"
      :class="{ 'sd-halo': halo }"
      :style="{ background: color }"
    />
  </span>
</template>

<style scoped>
.sd { position: relative; display: inline-flex; align-items: center; justify-content: center;
  width: 12px; height: 12px; line-height: 1; flex-shrink: 0; }
.sd-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  position: relative; z-index: 1; }
/* crit halo breathes (动效③, 2s loop) — killed globally by tokens.css
   prefers-reduced-motion. A blurred ::before ring tinted with the state color
   replaces the dot's own color while pulsing opacity (no hardcoded rgba). */
.sd-halo::before { content: ''; position: absolute; inset: 0; border-radius: 50%;
  background: var(--crit); opacity: .32; filter: blur(2.5px);
  animation: sd-pulse 2s ease-in-out infinite; }
@keyframes sd-pulse { 50% { opacity: .6; } }
</style>
