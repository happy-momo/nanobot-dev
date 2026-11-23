<script setup lang="ts">
// Observatory panel container (spec §2/§6): panel-frame shell + eyebrow title
// row. The body is intentionally full-bleed — inner content is cut into
// hairline cells (.haircell from tokens.css) separated by .hline/.vline, per
// the "hairline 分格" design language (no floating card padding).
defineProps<{ title: string; meta?: string }>()
</script>

<template>
  <section class="panel-frame op">
    <header class="op-head">
      <span class="eyebrow op-title">{{ title }}</span>
      <div class="op-side">
        <span v-if="meta" class="eyebrow op-meta">{{ meta }}</span>
        <!-- right-side actions (buttons / links) -->
        <slot name="actions" />
      </div>
    </header>
    <div class="op-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.op { display: flex; flex-direction: column; min-width: 0; }
.op-head { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 14px; border-bottom: 1px solid var(--line); }
.op-side { display: flex; align-items: center; gap: 10px; min-width: 0; }
.op-meta { font-family: var(--font-mono); }
.op-body { min-width: 0; }
</style>
