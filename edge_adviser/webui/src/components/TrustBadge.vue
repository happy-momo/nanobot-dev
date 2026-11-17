<script setup lang="ts">
import { computed } from 'vue'
import { trustMeta } from '../theme'

const props = defineProps<{ trust?: string }>()
const meta = computed(() => trustMeta[props.trust || 'none'] || trustMeta.none)
</script>

<template>
  <span class="trust-badge" title="数据可信度（确定性判定）">
    <span class="shield" :style="{ color: meta.tone }">🛡</span>
    <span :style="{ color: meta.tone }">{{ meta.label }}</span>
    <span class="v">{{ trust }}</span>
  </span>
</template>

<style scoped>
.trust-badge {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; font-weight: 600; white-space: nowrap;
}
.shield { font-size: 13px; }
.v { opacity: .65; font-weight: 500; }
</style>