<script setup lang="ts">
import { computed } from 'vue'
import { stateOf, metaOf } from '../theme'

const props = defineProps<{ state?: string; pulse?: boolean }>()
const meta = computed(() => metaOf(stateOf(props.state)))
</script>

<template>
  <span
    class="state-badge"
    :class="{ pulse: pulse }"
    :style="{ color: meta.fg, background: meta.bg, borderColor: meta.fg + '55' }"
  >
    <span class="dot" :style="{ background: meta.fg }" />
    {{ meta.label }}
  </span>
</template>

<style scoped>
.state-badge {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 10px; border-radius: 20px; border: 1px solid;
  font-size: 12px; font-weight: 600; white-space: nowrap;
}
.dot { width: 8px; height: 8px; border-radius: 50%; }
.pulse .dot { animation: eaPulse 1.2s infinite; }
@keyframes eaPulse { 0%,100%{opacity:1} 50%{opacity:.25} }
</style>