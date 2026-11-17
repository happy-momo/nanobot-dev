<script setup lang="ts">
// Asset registry: read-only detail of each equipment's spec — metrics with normal/
// warn/crit bands, planned-downtime windows, source kind. (Read-only for the HMI;
// editing spans config+ASSET.md regeneration, out of the read-side HMI scope.)
import { ref, computed } from 'vue'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import StateBadge from '../components/StateBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'

const rt = useRuntime()

const detail = ref<Record<string, any>>({})
const loading = ref<string | null>(null)

const selectedId = ref('')
const selected = computed(() => rt.assetById(selectedId.value))

async function open(id: string) {
  selectedId.value = id
  loading.value = id
  try {
    detail.value[id] = await api.get<any>(`/api/assets/${id}`)
  } finally {
    loading.value = null
  }
}

function bandLabel(m: any) {
  const bits: string[] = []
  if (m.norm_min != null || m.norm_max != null)
    bits.push(`normal [${m.norm_min ?? '−∞'}, ${m.norm_max ?? '∞'}]`)
  if (m.warn_min != null || m.warn_max != null)
    bits.push(`warn [${m.warn_min ?? '−∞'}, ${m.warn_max ?? '∞'}]`)
  if (m.crit_min != null || m.crit_max != null)
    bits.push(`crit [${m.crit_min ?? '−∞'}, ${m.crit_max ?? '∞'}]`)
  return bits.join('  ')
}
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <el-select v-model="selectedId" style="width:220px" placeholder="选择设备" @change="open">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <template v-if="selected">
        <StateBadge :state="selected.runtime.state" />
        <TrustBadge :trust="selected.runtime.trust" />
      </template>
    </div>

    <div v-if="!selected" class="muted" style="padding:40px;text-align:center">选择设备查看档案</div>

    <div v-else-if="detail[selected.asset_id]" v-loading="loading === selected.asset_id" class="grid">
      <el-card shadow="never">
        <template #header><b>{{ detail[selected.asset_id].name }}</b></template>
        <div class="meta">
          <div><span class="muted">ID</span> {{ detail[selected.asset_id].asset_id }}</div>
          <div><span class="muted">类型</span> {{ detail[selected.asset_id].kind }}</div>
          <div><span class="muted">采样周期</span> {{ detail[selected.asset_id].sample_freq_s }}s</div>
          <div><span class="muted">分析窗口</span> {{ detail[selected.asset_id].window_s }}s</div>
          <div v-if="detail[selected.asset_id].description" class="muted">{{ detail[selected.asset_id].description }}</div>
        </div>
      </el-card>

      <el-card shadow="never">
        <template #header><b>参数区间（normal / warn / crit）</b></template>
        <div v-for="m in detail[selected.asset_id].metrics" :key="m.key" class="metric-row">
          <div><b>{{ m.key }}</b> <span class="muted">[{{ m.unit }}]</span></div>
          <div class="muted small">{{ m.description }}</div>
          <div class="small" style="color:var(--ea-accent)">{{ bandLabel(m) }}</div>
          <div class="muted small">持久化阈值 {{ m.persist }} 采样 · 最少样本 {{ m.min_samples }}</div>
        </div>
      </el-card>

      <el-card shadow="never" class="wide">
        <template #header><b>计划停机窗口</b></template>
        <div v-if="!(detail[selected.asset_id].downtime_windows || []).length" class="muted">无配置</div>
        <div v-for="(w, i) in detail[selected.asset_id].downtime_windows" :key="i" class="muted">
          {{ (w.start || '—').slice(0,19) }} → {{ (w.end || '—').slice(0,19) }}
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.wide { grid-column: 1 / -1; }
.meta { display: flex; flex-direction: column; gap: 8px; }
.metric-row { padding: 10px 0; border-bottom: 1px solid var(--ea-border); display: flex; flex-direction: column; gap: 3px; }
.small { font-size: 12px; }
</style>