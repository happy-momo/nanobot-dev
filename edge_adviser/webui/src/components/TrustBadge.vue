<script setup lang="ts">
import { computed } from 'vue'
import { trustMeta } from '../theme'

const props = defineProps<{ trust?: string; showRaw?: boolean }>()
const meta = computed(() => trustMeta[props.trust || 'none'] || trustMeta.none)
</script>

<template>
  <span class="trust-badge" title="数据可信度（确定性判定）">
    <svg class="shield" viewBox="0 0 24 24" width="14" height="14" :style="{ color: meta.tone }">
      <path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3z"
            opacity="0.25" />
      <path fill="none" stroke="currentColor" stroke-width="2"
            d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3z" />
      <path v-if="trust === 'full'" fill="none" stroke="currentColor" stroke-width="2.4"
            stroke-linecap="round" stroke-linejoin="round" d="m8.5 11.5 2.6 2.6 4.6-4.8" />
    </svg>
    <span :style="{ color: meta.tone }">{{ meta.label }}</span>
    <span v-if="showRaw" class="v">{{ trust }}</span>
  </span>
</template>

<style scoped>
.trust-badge {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; font-weight: 600; white-space: nowrap;
}
.shield { flex-shrink: 0; }
.v { opacity: .65; font-weight: 500; }
</style>
