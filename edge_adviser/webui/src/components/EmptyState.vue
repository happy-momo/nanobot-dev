<template>
  <div class="empty-state">
    <div class="glyph">{{ icon }}</div>
    <div class="text">{{ text }}</div>
    <div class="hint muted" v-if="hint">{{ hint }}</div>
    <div class="cta" v-if="$slots.default"><slot /></div>
  </div>
</template>

<script setup lang="ts">
// Uniform empty state with optional CTA slot.
withDefaults(defineProps<{ text: string; hint?: string; icon?: string }>(), {
  icon: '⛔',
})
</script>

<style scoped>
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 56px 20px; gap: 8px; text-align: center;
}
.glyph { font-size: 40px; opacity: 0.7; }
.text { font-size: 14px; color: var(--ea-text); }
.hint { font-size: 12px; }
.cta { margin-top: 10px; }
</style>
