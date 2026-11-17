<script setup lang="ts">
// Live monitor for one asset: realtime metric curves over the snapshot history,
// with normal/warn/crit bands, current values, coverage, trust and freshness.
// The page is driven by a) the bundle of persisted snapshots (history) and
// b) the /stream/telemetry SSE for the live "tick".
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { api, connectSSE } from '../api/http'
import { useRuntime } from '../stores/runtime'
import StateBadge from '../components/StateBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'
import CoverageBar from '../components/CoverageBar.vue'
import MetricChart from '../components/MetricChart.vue'
import { metaOf } from '../theme'

const route = useRoute()
const rt = useRuntime()

const assetId = ref<string>(String(route.params.assetId || rt.overviewCards[0]?.asset_id || ''))
const history = ref<any[]>([])
const liveTick = ref<any>(null)
const loading = ref(false)

const card = computed(() => rt.assetById(assetId.value))
const meta = computed(() => metaOf(rt.liveFor(assetId.value)?.status || card.value?.runtime.state))

// build per-metric series from snapshot history + latest live
function buildSeries(metricKey: string) {
  const rows: { window_end: string; value: number | null; level?: string }[] = []
  for (const h of history.value) {
    const m = h.metrics?.find((x: any) => x.key === metricKey)
    rows.push({ window_end: h.window_end, value: m ? (m.max ?? m.mean ?? null) : null, level: m?.level })
  }
  const live = liveForMetric(metricKey)
  if (live) rows.push({ window_end: '最新', value: live.max ?? live.mean ?? null, level: live.level })
  return rows
}

function liveForMetric(key: string) {
  return liveTick.value?.metrics?.find((m: any) => m.key === key)
}

function metaOfLevel(level?: string) {
  const toneMap: Record<string, string> = { info: '#3f8ff7', warn: '#f5b14c', critical: '#f4554f', insufficient: '#e8c16b', unknown: '#8ea0b3' }
  return toneMap[level || 'info'] || '#3f8ff7'
}

let closeSSE: (() => void) | null = null

async function loadHistory() {
  if (!assetId.value) return
  loading.value = true
  try {
    const rows = await api.get<any>(`/api/assets/${assetId.value}/snapshots?limit=40`)
    history.value = rows || []
  } finally {
    loading.value = false
  }
}

function watchAsset() {
  if (closeSSE) { closeSSE(); closeSSE = null }
  if (!assetId.value) return
  loadHistory()
  // subscribe to live telemetry; filtered server-side isn't available, filter here
  closeSSE = connectSSE('/stream/telemetry', (kind, data) => {
    if (kind === 'snapshot' && data?.asset_id === assetId.value) {
      liveTick.value = data
      rt.live[assetId.value] = { ...rt.live[assetId.value], ...data }
      // prepend to history so the chart moves immediately
      history.value = [{ ...data, metrics: data.metrics }, ...history.value].slice(0, 60)
    }
  })
}

onMounted(async () => {
  // if no asset selected but cards exist, pick the first
  if (!assetId.value && rt.overviewCards.length) {
    assetId.value = rt.overviewCards[0].asset_id
  }
  watchAsset()
})

onBeforeUnmount(() => { closeSSE?.() })

watch(() => route.params.assetId, (v) => {
  if (v && v !== assetId.value) { assetId.value = String(v); watchAsset() }
})
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <el-select v-model="assetId" style="width:200px" @change="watchAsset">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <template v-if="card">
        <StateBadge :state="rt.liveFor(assetId)?.status || card.runtime.state" :pulse="meta.fg === '#f4554f' || meta.fg === '#f5b14c'" />
        <TrustBadge :trust="rt.liveFor(assetId)?.trust || card.runtime.trust" />
      </template>
      <div class="spacer" />
      <el-button size="small" @click="loadHistory">刷新</el-button>
    </div>

    <div v-if="!card" class="muted" style="padding:40px;text-align:center">请选择设备</div>

    <template v-else>
      <!-- top snapshot strip: coverage + headline + freshness note -->
      <div class="strip">
        <div class="strip-item" style="width:200px">
          <CoverageBar :coverage="rt.liveFor(assetId)?.coverage ?? card.runtime.coverage" :samples="rt.liveFor(assetId)?.sample_count ?? card.runtime.sample_count" />
        </div>
        <div class="strip-item flex1">
          <div class="muted">状态判读</div>
          <div class="headline">{{ (rt.liveFor(assetId)?.headline || card.runtime.headline) }}</div>
          <div class="muted small" v-if="card.runtime.state_reason">{{ card.runtime.state_reason }}</div>
        </div>
        <div class="strip-item">
          <div class="muted">样本数</div>
          <div class="num big-value" style="font-size:22px">{{ rt.liveFor(assetId)?.sample_count ?? card.runtime.sample_count }}</div>
        </div>
        <div class="strip-item">
          <div class="muted">总体水平</div>
          <div class="num" style="font-size:18px;color:var(--ea-accent)">{{ rt.liveFor(assetId)?.overall_level ?? card.runtime.overall_level }}</div>
        </div>
      </div>

      <div class="charts">
        <el-card v-for="mk in metricKeys" :key="mk" shadow="never">
          <template #header>
            <div class="chart-head">
              <span class="name">{{ mk }}</span>
              <span class="muted small">{{ card?.metrics?.find((m:any)=>m.key===mk)?.unit }}</span>
              <span v-if="liveForMetric(mk)" class="level-pill num" :style="{ color: metaOfLevel(liveForMetric(mk).level) }">
                {{ liveForMetric(mk).level }} · {{ (liveForMetric(mk).max||liveForMetric(mk).mean)?.toFixed(1) }}
              </span>
            </div>
          </template>
          <MetricChart
            :metric-key="mk"
            :metric-label="mk"
            :unit="card?.metrics?.find((m:any)=>m.key===mk)?.unit || ''"
            :series="buildSeries(mk)"
            :spec="card?.metrics?.find((m:any)=>m.key===mk) || null"
          />
        </el-card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.spacer { flex: 1; }
.strip { display: flex; gap: 20px; background: var(--ea-panel); border: 1px solid var(--ea-border); border-radius: 12px; padding: 14px 18px; margin-bottom: 16px; flex-wrap: wrap; }
.strip-item { display: flex; flex-direction: column; gap: 4px; justify-content: center; }
.flex1 { flex: 1; min-width: 220px; }
.headline { font-size: 14px; color: var(--ea-text); margin: 2px 0; }
.charts { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; }
.chart-head { display: flex; align-items: center; gap: 8px; }
.name { font-weight: 700; }
.level-pill { font-size: 13px; font-weight: 600; margin-left: auto; }
</style>