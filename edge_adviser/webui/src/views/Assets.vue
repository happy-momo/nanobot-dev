<script setup lang="ts">
// Asset registry: left list + right detail. Live state/trust come from the
// store's live records (not the static card); each metric gets a MetricBand
// visualization with the current value marker; source kind + downtime windows
// surfaced. (Editing spans config+ASSET.md regeneration — out of read-side scope.)
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import { fmtNum } from '../theme'
import StateBadge from '../components/StateBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'
import MetricBand from '../components/MetricBand.vue'
import PageHeader from '../components/PageHeader.vue'

const rt = useRuntime()
const selectedId = ref('')
const detail = ref<any>(null)
const loading = ref(false)
const cache = ref<Record<string, any>>({})

const cards = computed(() => rt.overviewCards)
const selected = computed(() => rt.assetById(selectedId.value))
const liveState = computed(() => rt.liveFor(selectedId.value)?.status || selected.value?.runtime.state)
const liveTrust = computed(() => rt.liveFor(selectedId.value)?.trust || selected.value?.runtime.trust)

// metric specs (detail payload preferred; falls back to the card's config)
const specs = computed(() => detail.value?.metrics || selected.value?.config?.metrics || [])
const sourceKind = computed(() => detail.value?.source_kind)

async function select(id: string) {
  selectedId.value = id
  if (cache.value[id]) { detail.value = cache.value[id]; return }
  loading.value = true
  try {
    const d = await api.get<any>(`/api/assets/${id}`)
    detail.value = d
    cache.value[id] = d
  } catch (e: any) {
    ElMessage.error(e?.message || '资产详情加载失败')
    detail.value = null
  } finally {
    loading.value = false
  }
}

function currentValue(key: string): number | null | undefined {
  const lv = rt.live[selectedId.value]?.metrics?.find((m) => m.key === key)
  if (lv) return lv.max ?? lv.mean ?? null
  return selected.value?.runtime.latest_values?.[key]
}
function currentLevel(key: string): string | undefined {
  const lv = rt.live[selectedId.value]?.metrics?.find((m) => m.key === key)
  return lv?.level
}

onMounted(() => {
  if (cards.value.length && !selectedId.value) select(cards.value[0].asset_id)
})
watch(cards, (n) => {
  if (n.length && !selectedId.value) select(n[0].asset_id)
})
watch(selectedId, () => { detail.value = cache.value[selectedId.value] || null })
</script>

<template>
  <div class="page">
    <PageHeader title="设备管理" subtitle="在册资产 · 参数规格 · 计划停机窗（只读）" />

    <div class="layout">
      <!-- left: asset list -->
      <aside class="alist">
        <div v-for="c in cards" :key="c.asset_id" class="a-item"
             :class="{ active: selectedId === c.asset_id }" @click="select(c.asset_id)">
          <div class="a-main">
            <div class="a-name">{{ c.name }}</div>
            <div class="muted small mono">{{ c.asset_id }}</div>
          </div>
          <StateBadge :state="rt.liveFor(c.asset_id)?.status || c.runtime.state" />
        </div>
      </aside>

      <!-- right: detail -->
      <section class="adetail" v-loading="loading">
        <div v-if="!selected" style="padding:40px;text-align:center" class="muted">
          {{ rt.ready ? '请选择设备' : '加载中…' }}
        </div>

        <template v-else>
          <div class="head panel">
            <div>
              <div class="d-name">{{ selected.name }}</div>
              <div class="muted small mono">{{ selected.asset_id }} · {{ selected.kind }}
                <template v-if="sourceKind"> · 数据源 {{ sourceKind }}</template>
              </div>
            </div>
            <div class="head-badges">
              <StateBadge :state="liveState" />
              <TrustBadge :trust="liveTrust" />
            </div>
          </div>

          <div v-if="selected.description" class="desc muted">{{ selected.description }}</div>

          <div class="meta-grid">
            <div class="meta"><span class="muted small">采样频率</span>
              <span class="num">{{ selected.sample_freq_s ?? '—' }}s</span></div>
            <div class="meta"><span class="muted small">分析窗口</span>
              <span class="num">{{ selected.window_s ?? '—' }}s</span></div>
            <div class="meta"><span class="muted small">数据源</span>
              <span>{{ sourceKind || '—' }}</span></div>
          </div>

          <!-- metrics with band visualization -->
          <el-card shadow="never" class="sec">
            <template #header><b>指标规格与当前值</b></template>
            <div class="bands">
              <MetricBand v-for="m in specs" :key="m.key" :metric="m"
                          :value="currentValue(m.key)" :level="currentLevel(m.key)" />
            </div>
          </el-card>

          <!-- downtime windows -->
          <el-card shadow="never" class="sec">
            <template #header><b>计划停机窗</b></template>
            <el-table v-if="selected.downtime_windows?.length" :data="selected.downtime_windows" size="small">
              <el-table-column prop="start" label="开始" min-width="180" />
              <el-table-column prop="end" label="结束" min-width="180" />
            </el-table>
            <div v-else class="muted small">无配置</div>
          </el-card>

          <!-- asset profile md -->
          <el-card shadow="never" class="sec" v-if="detail?.asset_md">
            <template #header><b>资产档案（ASSET.md）</b></template>
            <pre class="asset-md mono">{{ detail.asset_md }}</pre>
          </el-card>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 240px 1fr; gap: 16px; align-items: start; }
.alist {
  background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-md); padding: 8px;
  display: flex; flex-direction: column; gap: 4px;
}
.a-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 12px; border-radius: var(--ea-r-sm); cursor: pointer;
}
.a-item:hover { background: var(--ea-panel-soft); }
.a-item.active { background: var(--ea-accent-soft); }
.a-name { font-weight: 600; font-size: 13px; }
.adetail { min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 18px; flex-wrap: wrap; gap: 10px;
}
.d-name { font-size: 18px; font-weight: 700; }
.head-badges { display: flex; gap: 10px; align-items: center; }
.desc { padding: 0 4px; line-height: 1.6; }
.meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.meta {
  background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-sm); padding: 10px 14px;
  display: flex; flex-direction: column; gap: 2px;
}
.sec { width: 100%; }
.bands { display: flex; flex-direction: column; gap: 16px; }
.asset-md {
  white-space: pre-wrap; line-height: 1.7; margin: 0;
  max-height: 320px; overflow-y: auto;
}

@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .alist { flex-direction: row; overflow-x: auto; }
  .a-item { flex-shrink: 0; }
}
</style>
