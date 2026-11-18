<script setup lang="ts">
// Live monitor for one asset: realtime metric curves over the snapshot history,
// with normal/warn/crit markArea bands, current values, coverage, trust and a
// freshness countdown. History is fetched WITH metrics (include_metrics=1) so
// charts render from persisted data, and SSE ticks merge in (deduped by
// window_end). "立即采集" triggers run-now with a busy state.
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { connectSSE } from '../api/http'
import { useRuntime, type SnapshotRow, type MetricLive } from '../stores/runtime'
import { fmtNum, relativeTime, levelMeta } from '../theme'
import StateBadge from '../components/StateBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'
import CoverageBar from '../components/CoverageBar.vue'
import MetricChart from '../components/MetricChart.vue'
import PageHeader from '../components/PageHeader.vue'

const route = useRoute()
const router = useRouter()
const rt = useRuntime()

const assetId = ref<string>(String(route.params.assetId || ''))
const history = ref<SnapshotRow[]>([])
const liveTick = ref<MetricLive[] | null>(null)
const loading = ref(false)
const running = ref(false)
const liveTs = ref<string>('')

const card = computed(() => rt.assetById(assetId.value))
const live = computed(() => rt.liveFor(assetId.value))

// freshness: window_end age vs the configured data_freshness_s
const freshness = computed(() => {
  const we = live.value?.window_end || card.value?.runtime.window_end
  if (!we || !rt.dataFreshnessS) return null
  const age = Date.now() - new Date(we).getTime()
  return { age, ratio: Math.min(1, age / (rt.dataFreshnessS * 1000)) }
})

// metric specs from the card's config (the single source for bands/units)
const specs = computed(() => card.value?.config?.metrics || card.value?.metrics || [])

// metricKeys — the one authoritative computed (was referenced undefined before):
// live tick metrics → most recent history row with metrics → config spec order.
const metricKeys = computed<string[]>(() => {
  if (liveTick.value?.length) return liveTick.value.map((m) => m.key)
  const withMetrics = [...history.value].reverse().find((r) => r.metrics?.length)
  if (withMetrics?.metrics?.length) return withMetrics.metrics.map((m) => m.key)
  if (specs.value.length) return specs.value.map((m) => m.key)
  return []
})

function specOf(key: string) {
  return specs.value.find((m) => m.key === key) || null
}
function unitOf(key: string) {
  return specOf(key)?.unit || ''
}

// per-metric series: history points + the live tick appended (deduped by ts)
function buildSeries(metricKey: string) {
  const seen = new Set<string>()
  const rows: { window_end: string; value: number | null; level?: string }[] = []
  for (const h of history.value) {
    const m = h.metrics?.find((x) => x.key === metricKey)
    if (!m) continue
    const ts = h.window_end
    if (seen.has(ts)) continue
    seen.add(ts)
    rows.push({ window_end: ts, value: m.max ?? m.mean ?? null, level: m.level })
  }
  const lm = liveTick.value?.find((m) => m.key === metricKey)
  if (lm && !seen.has(liveTs.value)) {
    rows.push({ window_end: liveTs.value || '最新', value: lm.max ?? lm.mean ?? null, level: lm.level })
  }
  return rows
}

function currentOf(key: string): MetricLive | null {
  return liveTick.value?.find((m) => m.key === key)
    || live.value?.metrics?.find((m) => m.key === key) || null
}

let closeSSE: (() => void) | null = null

async function loadHistory() {
  if (!assetId.value) return
  loading.value = true
  try {
    history.value = await rt.loadHistory(assetId.value, 40, true)
  } finally {
    loading.value = false
  }
}

async function runNow() {
  if (!assetId.value || running.value) return
  running.value = true
  try {
    const snap = await rt.runNow(assetId.value)
    if (snap && !snap.error) {
      ElMessage.success('采集完成')
      liveTick.value = snap.metrics || null
      liveTs.value = snap.window_end || ''
      await loadHistory()
    } else {
      ElMessage.warning('采集未成功：' + (snap?.error || 'unknown'))
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '采集失败')
  } finally {
    running.value = false
  }
}

function watchAsset() {
  if (closeSSE) { closeSSE(); closeSSE = null }
  liveTick.value = null
  if (!assetId.value) return
  loadHistory()
  // subscribe to live telemetry; the bus broadcasts all assets, filter here
  closeSSE = connectSSE('/stream/telemetry', (kind, data) => {
    if (kind === 'snapshot' && data?.asset_id === assetId.value) {
      liveTick.value = data.metrics || []
      liveTs.value = data.window_end || ''
      rt.live[assetId.value] = { ...rt.live[assetId.value], ...data }
      // prepend so the chart moves immediately (dedup handled in buildSeries)
      if (data.window_end) {
        const existing = history.value.findIndex((r) => r.window_end === data.window_end)
        if (existing >= 0) history.value.splice(existing, 1)
        history.value.unshift({ ...data, filename: '' } as SnapshotRow)
        history.value = history.value.slice(0, 60)
      }
    }
  })
}

onMounted(() => {
  // if no asset selected but cards exist, pick the first
  if (!assetId.value && rt.overviewCards.length) {
    assetId.value = rt.overviewCards[0].asset_id
  } else if (!rt.overviewCards.length) {
    // cards not loaded yet (direct URL open) — pick first when they arrive
    const stop = watch(() => rt.overviewCards.length, (n) => {
      if (n && !assetId.value) assetId.value = rt.overviewCards[0].asset_id
      stop()
    })
  }
  watchAsset()
})

onBeforeUnmount(() => { closeSSE?.() })

watch(() => route.params.assetId, (v) => {
  if (v && v !== assetId.value) { assetId.value = String(v); watchAsset() }
})
watch(assetId, (v, o) => {
  if (v && v !== o && !route.params.assetId) {
    // keep the URL in sync when the selector is used directly
    history.value = []
    watchAsset()
  }
})
</script>

<template>
  <div class="page">
    <PageHeader title="实时监控" :subtitle="card ? card.name : ''">
      <el-select v-model="assetId" style="width:220px" filterable>
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <el-button size="small" type="primary" :loading="running" @click="runNow">
        立即采集
      </el-button>
      <el-button size="small" @click="loadHistory" :loading="loading">刷新</el-button>
    </PageHeader>

    <div v-if="!card" style="padding:40px;text-align:center" class="muted">
      {{ rt.ready ? '请选择设备' : '加载中…' }}
    </div>

    <template v-else>
      <!-- top snapshot strip -->
      <div class="strip">
        <div class="strip-item" style="width:200px">
          <CoverageBar :coverage="live?.coverage ?? card.runtime.coverage"
                       :samples="live?.sample_count ?? card.runtime.sample_count" />
        </div>
        <div class="strip-item flex1">
          <div class="muted small">状态判读</div>
          <div class="headline">{{ live?.headline || card.runtime.headline || '—' }}</div>
          <div class="muted small" v-if="card.runtime.state_reason">{{ card.runtime.state_reason }}</div>
        </div>
        <div class="strip-item">
          <div class="muted small">样本数</div>
          <div class="kpi-value" style="font-size:22px">
            {{ live?.sample_count ?? card.runtime.sample_count }}
          </div>
        </div>
        <div class="strip-item">
          <div class="muted small">数据新鲜度</div>
          <el-progress v-if="freshness" type="line" :percentage="Math.round(freshness.ratio * 100)"
                       :stroke-width="8" :show-text="false"
                       :color="freshness.ratio > 0.8 ? '#f4554f' : freshness.ratio > 0.5 ? '#f5b14c' : '#3ddc97'"
                       style="width:140px" />
          <div class="muted small">{{ relativeTime(live?.window_end || card.runtime.window_end) }}</div>
        </div>
        <div class="strip-item">
          <div class="muted small">总体水平</div>
          <span class="chip" :style="{
            color: levelMeta[live?.overall_level || card.runtime.overall_level]?.fg,
            background: levelMeta[live?.overall_level || card.runtime.overall_level]?.bg }">
            {{ levelMeta[live?.overall_level || card.runtime.overall_level]?.label || '—' }}
          </span>
        </div>
        <div class="strip-item">
          <StateBadge :state="live?.status || card.runtime.state"
                      :pulse="card.runtime.state === 'critical' || card.runtime.state === 'warn'" />
          <TrustBadge :trust="live?.trust || card.runtime.trust" />
        </div>
      </div>

      <div v-if="!metricKeys.length && !loading" class="muted" style="padding:24px;text-align:center">
        暂无指标数据 — 点击「立即采集」获取第一个窗口
      </div>

      <div class="charts">
        <el-card v-for="mk in metricKeys" :key="mk" shadow="never">
          <template #header>
            <div class="chart-head">
              <span class="name">{{ mk }}</span>
              <span class="muted small">{{ unitOf(mk) }}</span>
              <span v-if="currentOf(mk)" class="level-pill num"
                    :style="{ color: levelMeta[currentOf(mk)!.level || 'info']?.fg }">
                {{ levelMeta[currentOf(mk)!.level || 'info']?.label }}
                · {{ fmtNum(currentOf(mk)!.max ?? currentOf(mk)!.mean, 1) }}
              </span>
            </div>
          </template>
          <MetricChart
            :metric-key="mk"
            :metric-label="mk"
            :unit="unitOf(mk)"
            :series="buildSeries(mk)"
            :spec="specOf(mk)"
          />
        </el-card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.strip {
  display: flex; gap: 20px; background: var(--ea-panel);
  border: 1px solid var(--ea-border); border-radius: var(--ea-r-md);
  padding: 14px 18px; margin-bottom: 16px; flex-wrap: wrap;
  align-items: center;
}
.strip-item { display: flex; flex-direction: column; gap: 4px; justify-content: center; }
.strip-item:last-child { gap: 8px; }
.flex1 { flex: 1; min-width: 220px; }
.headline { font-size: 14px; color: var(--ea-text); margin: 2px 0; }
.charts { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; }
.chart-head { display: flex; align-items: center; gap: 8px; }
.name { font-weight: 700; }
.level-pill { font-size: 13px; font-weight: 600; margin-left: auto; }
</style>
