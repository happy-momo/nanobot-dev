<template>
  <div class="kpi-card" :style="{ '--tone': tone }">
    <div class="label muted">{{ label }}</div>
    <div class="value num">{{ value }}</div>
    <div class="sub muted" v-if="sub">{{ sub }}</div>
  </div>
</template>

<script setup lang="ts">
// KPI stat card for the Dashboard strip.
withDefaults(defineProps<{
  label: string
  value: string | number
  sub?: string
  tone?: string
}>(), { tone: 'var(--ea-accent)' })
</script>

<style scoped>
.kpi-card {
  background: var(--ea-panel);
  border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-md);
  padding: 14px 18px;
  display: flex; flex-direction: column; gap: 4px;
  position: relative; overflow: hidden;
}
.kpi-card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--tone); opacity: 0.85;
}
.label { font-size: var(--ea-fs-12); letter-spacing: 0.4px; }
.value { font-size: var(--ea-fs-28); font-weight: 700; color: var(--tone); line-height: 1.1; }
.sub { font-size: var(--ea-fs-12); }
</style>
