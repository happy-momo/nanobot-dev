<script setup lang="ts">
// VerdictFrame — the "判决标线" reticle (spec §3.2): four-corner L brackets that
// frame a system-endorsed verdict (state word / TRUST value / deep conclusion).
// Usage is restricted by spec to overview verdict rows, asset detail headers and
// report conclusions. Color: pass a state/trust color or leave unset for cyan.
//
// The bracket color rides a --vf-c custom property so the CSS below stays free
// of hardcoded values and inherits --cyan as the default accent.
withDefaults(defineProps<{ color?: string }>(), { color: '' })
</script>

<template>
  <span class="vf" :style="color ? { '--vf-c': color } : undefined">
    <slot />
  </span>
</template>

<style scoped>
.vf { position: relative; display: inline-block; padding: 6px 10px; }
.vf::before, .vf::after { content: ''; position: absolute; width: 9px; height: 9px; }
.vf::before { left: 0; top: 0; border-style: solid; border-width: 1.5px 0 0 1.5px; border-color: var(--vf-c, var(--cyan)); }
.vf::after  { right: 0; bottom: 0; border-style: solid; border-width: 0 1.5px 1.5px 0; border-color: var(--vf-c, var(--cyan)); }
</style>
