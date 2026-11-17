<script setup lang="ts">
// Knowledge base: render the asset's MEMORY.md (chapters) plus the claims/conflicts
// ledger. Conflict/stale claims are shown with a distinct tag so a trend, a
// superseded threshold or an expired fact is never silently trusted.
import { ref, watch, onMounted, computed } from 'vue'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'

const rt = useRuntime()
const assetId = ref('')
const data = ref<any>(null)

async function load() {
  if (!assetId.value) return
  data.value = await api.get<any>(`/api/assets/${assetId.value}/knowledge`)
}

const claims = computed(() => data.value?.claims || [])
const conflicts = computed(() => data.value?.conflicts || [])
const sampleIndices = computed(() => {
  const md = data.value?.memory_md || ''
  const secs = md.split(/^## /m).slice(1)
  return secs.length
})

function renderChapters() {
  const md = data.value?.memory_md || ''
  // naive: replace ## headings with styled blocks + anchors
  return md
    .replace(/^### /gm, '<h4>')
    .replace(/^## /gm, '<h3>')
    .replace(/^# /gm, '<h2>')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\n/g, '<br/>')
}

onMounted(async () => {
  if (!assetId.value && rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
  await load()
})
watch(() => assetId.value, load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <div class="muted" style="font-size:12px">learn_gate：{{ data?.learn_gate || '—' }} · {{ sampleIndices }} 个章节</div>
    </div>

    <div v-if="!data" class="muted" style="padding:40px;text-align:center">加载中…</div>

    <div v-else class="grid">
      <el-card shadow="never" class="md-card">
        <template #header><b>MEMORY.md · 沉淀知识</b></template>
        <div class="md" v-html="renderChapters()" />
      </el-card>

      <div class="side">
        <el-card v-if="conflicts.length" shadow="never" class="section">
          <template #header><b>冲突 / 演变记账</b></template>
          <div v-for="(c, i) in conflicts" :key="i" class="conflict">
            <el-tag type="warning" size="small">冲突/作废</el-tag>
            <div class="muted small">{{ c.heading }}</div>
            <div class="muted small">{{ c.old_val }} → {{ c.new_val }}</div>
            <div class="muted small">@{{ c.created_at }}</div>
          </div>
        </el-card>
        <el-card v-if="claims.length" shadow="never" class="section">
          <template #header><b>知识声明 {{ claims.length }}</b></template>
          <div v-for="(c, i) in claims" :key="i" class="claim">
            <el-tag
              :type="c.status === 'superseded' ? 'danger' : c.status === 'stale' ? 'warning' : 'success'"
              size="small"
            >{{ c.status || 'active' }}</el-tag>
            <span class="muted small">{{ c.heading }}</span>
            <div class="muted small">{{ (c.raw || '').slice(0, 120) }}</div>
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 320px; gap: 16px; }
.md-card .md { line-height: 1.8; }
.md :deep(h3) { color: var(--ea-accent); margin: 10px 0 4px; }
.md :deep(h2) { font-size: 17px; border-bottom: 1px solid var(--ea-border); padding-bottom: 4px; }
.md :deep(h4) { margin: 6px 0 2px; }
.side { display: flex; flex-direction: column; gap: 16px; }
.section { flex: 0 0 auto; }
.conflict, .claim { display: flex; flex-direction: column; gap: 3px; padding: 8px 0; border-bottom: 1px solid var(--ea-border); }
.small { font-size: 12px; }
</style>