<script setup lang="ts">
// EmptyState — centered eyebrow-style caption + one "what to do" line (spec §2:
// 空状态不用插画，一句人话，如「暂无告警 — 全场判定正常」).
defineProps<{ label: string; hint?: string }>()
</script>

<template>
  <div class="es">
    <span class="es-label">{{ label }}</span>
    <span v-if="hint" class="es-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.es { display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 6px; padding: 30px 16px; text-align: center; }
.es-label { font-size: 10px; letter-spacing: 3px; text-transform: uppercase;
  color: var(--tx-dim); font-weight: 600; }
.es-hint { font-size: 12px; color: var(--tx-dim); }
</style>
