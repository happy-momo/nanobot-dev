<script setup lang="ts">
import { computed } from 'vue'
import { levelTone } from '../theme'

const props = defineProps<{ coverage?: number; samples?: number; expected?: number }>()

const pct = computed(() => Math.round((props.coverage || 0) * 100))
const tone = computed(() => {
  const c = props.coverage || 0
  if (c >= 0.8) return levelTone.info
  if (c >= 0.5) return levelTone.warn
  return levelTone.critical
})
</script>

<template>
  <div class="cov" title="覆盖率 = 实际采样 / 期望采样">
    <div class="row">
      <span class="muted">覆盖率</span>
      <span class="num" :style="{ color: tone }">{{ pct }}%</span>
    </div>
    <div class="track"><div class="fill" :style="{ width: pct + '%', background: tone }" /></div>
    <div class="row">
      <span class="muted">{{ samples ?? 0 }}</span>
      <span class="muted">样本</span>
    </div>
  </div>
</template>

<style scoped>
.cov { width: 100%; }
.row { display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin: 2px 0; }
.track { height: 6px; background: #12283a; border-radius: 3px; overflow: hidden; }
.fill { height: 100%; border-radius: 3px; transition: width .4s ease; }
</style>