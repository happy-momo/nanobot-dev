<script setup lang="ts">
// Audit ledger: every run/report/learn outcome per asset, grouped by day with
// gap-day detection — days between the first snapshot and today without any
// runlog rows are highlighted as 缺口日 so a missing report is traceable, not
// silently absent. Outcome filter chips; dead code removed.
import { ref, onMounted, watch, computed } from 'vue'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import { formatTs } from '../theme'
import PageHeader from '../components/PageHeader.vue'

const rt = useRuntime()
const assetId = ref('')
const rows = ref<any[]>([])
const loading = ref(false)
const fOutcome = ref('')

const outcomes = [
  { value: 'ok', label: '成功' },
  { value: 'no_data', label: '无数据' },
  { value: 'gated', label: '门禁拦截' },
  { value: 'provider_failed', label: '模型失败' },
  { value: 'exception', label: '异常' },
]

async function load() {
  if (!assetId.value) return
  loading.value = true
  try {
    rows.value = (await api.get<any>(`/api/assets/${assetId.value}/runlog?limit=300`)) || []
  } catch (e: any) {
    rows.value = []
  } finally {
    loading.value = false
  }
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

const filtered = computed(() =>
  fOutcome.value ? rows.value.filter((r) => r.outcome === fOutcome.value) : rows.value)

// day-grouped view (newest first) with gap-day markers
const byDate = computed(() => {
  const map: Record<string, any[]> = {}
  for (const r of filtered.value) {
    const k = r.date || '?'
    ;(map[k] = map[k] || []).push(r)
  }
  return Object.entries(map).sort((a, b) => (a[0] < b[0] ? 1 : -1))
})

// gap days: every calendar day from the earliest row's date to today that has
// no runlog rows at all
const gapDays = computed(() => {
  if (!filtered.value.length) return []
  const dates = new Set(filtered.value.map((r) => r.date).filter(Boolean))
  const parsed = [...dates].map((d) => new Date(d + 'T00:00:00')).filter((d) => !isNaN(d.getTime()))
  if (!parsed.length) return []
  const min = new Date(Math.min(...parsed.map((d) => d.getTime())))
  const today = new Date()
  const gaps: string[] = []
  const cur = new Date(min)
  while (cur <= today && gaps.length < 60) {
    const iso = cur.toISOString().slice(0, 10)
    if (!dates.has(iso)) gaps.push(iso)
    cur.setDate(cur.getDate() + 1)
  }
  return gaps
})

const okRate = computed(() => {
  const n = filtered.value.length
  if (!n) return null
  return Math.round((filtered.value.filter((r) => r.outcome === 'ok').length / n) * 100)
})
</script>

<template>
  <div class="page">
    <PageHeader title="审计" subtitle="runlog.jsonl — 每次 run/报告/学习结果，缺失日可对账">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <el-select v-model="fOutcome" clearable placeholder="全部结果" style="width:130px">
        <el-option v-for="o in outcomes" :key="o.value" :value="o.value" :label="o.label" />
      </el-select>
      <span v-if="okRate !== null" class="chip">成功率 {{ okRate }}%</span>
      <el-button size="small" @click="load" :loading="loading">刷新</el-button>
    </PageHeader>

    <div v-if="gapDays.length" class="gap-box">
      <span class="gap-title">⚠ 缺口日（无任何审计记录）：</span>
      <span v-for="d in gapDays.slice(0, 14)" :key="d" class="gap-day mono">{{ d }}</span>
      <span v-if="gapDays.length > 14" class="muted small">…等 {{ gapDays.length }} 天</span>
    </div>

    <el-card shadow="never" v-loading="loading">
      <el-table v-if="filtered.length" :data="filtered" size="small" max-height="600">
        <el-table-column prop="timestamp" label="时间" width="170">
          <template #default="{ row }">{{ formatTs(row.timestamp) }}</template>
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
      <el-empty v-else description="暂无审计记录" :image-size="60" />
    </el-card>
  </div>
</template>

<style scoped>
.gap-box {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  background: rgba(245,177,76,0.08); border: 1px solid rgba(245,177,76,0.35);
  border-radius: var(--ea-r-sm); padding: 8px 12px; margin-bottom: 12px;
}
.gap-title { color: #f5b14c; font-size: 13px; font-weight: 600; }
.gap-day { font-size: 12px; background: rgba(245,177,76,0.15); border-radius: 4px; padding: 1px 6px; }
</style>
