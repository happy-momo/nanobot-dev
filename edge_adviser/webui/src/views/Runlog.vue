<script setup lang="ts">
// Audit ledger: every run/report/learn outcome per asset. Missing-data days and
// provider failures are visible + highlighted so a day with no report is
// traceable, not silently absent. This is the industrial "可追溯" layer.
import { ref, onMounted, watch, computed } from 'vue'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'

const rt = useRuntime()
const assetId = ref('')
const rows = ref<any[]>([])
const includeAcked = ref(false)

async function load() {
  if (!assetId.value) return
  rows.value = (await api.get<any>(`/api/assets/${assetId.value}/runlog?limit=300`)) || []
}

onMounted(async () => {
  if (!assetId.value && rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
  await load()
})
watch(() => assetId.value, load)

function outcomeTag(outcome: string) {
  switch (outcome) {
    case 'ok': return { type: 'success', label: '成功' }
    case 'no_data': return { type: 'warning', label: '无数据' }
    case 'gated': return { type: 'info', label: '门禁拦截' }
    case 'provider_failed': return { type: 'danger', label: '模型失败' }
    case 'exception': return { type: 'danger', label: '异常' }
    default: return { type: 'info', label: outcome }
  }
}

function fmtTs(t: string) {
  const d = new Date(t)
  return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN', { hour12: false })
}

const byDate = computed(() => {
  const map: Record<string, any[]> = {}
  for (const r of rows.value) {
    const k = r.date || '?'
    ;(map[k] = map[k] || []).push(r)
  }
  return Object.entries(map).sort((a, b) => (a[0] < b[0] ? 1 : -1))
})

const staleDates = computed(() =>
  [...new Set(rows.value.map((r) => r.date))].filter((d) =>
    rows.value.some((r) => r.date === d && r.outcome !== 'ok'),
  ),
)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <el-switch v-model="includeAcked" active-text="按日分组" style="display:none" />
      <div class="spacer" />
      <el-button size="small" @click="load">刷新</el-button>
    </div>

    <div class="muted" style="font-size:12px;margin-bottom:10px">
      审计账本 runlog.jsonl —— 每次 run/报告/学习结果；<span style="color:#f5b14c">非 ok 的日子会标出</span>，缺失日可对账。
    </div>

    <el-card shadow="never">
      <el-table :data="rows" size="small" max-height="600">
        <el-table-column prop="timestamp" label="时间" width="170">
          <template #default="{ row }">{{ fmtTs(row.timestamp) }}</template>
        </el-table-column>
        <el-table-column prop="date" label="日期" width="110" />
        <el-table-column prop="kind" label="任务" width="120" />
        <el-table-column label="结果" width="110">
          <template #default="{ row }">
            <el-tag :type="outcomeTag(row.outcome).type" size="small">{{ outcomeTag(row.outcome).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" min-width="200">
          <template #default="{ row }">
            <span :style="row.outcome !== 'ok' ? { color: '#f5b14c' } : {}">{{ row.reason || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="state" label="状态" width="120" />
      </el-table>
      <el-empty v-if="!rows.length" description="暂无审计记录" :image-size="60" />
    </el-card>
  </div>
</template>

<style scoped>
.spacer { flex: 1; }
</style>