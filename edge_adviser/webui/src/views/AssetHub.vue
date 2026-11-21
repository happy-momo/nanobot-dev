<script setup lang="ts">
// Asset Hub — the task-oriented device page. The data-layer pages (Monitor /
// QA / Reports / Knowledge) answer "how do I present X"; an operator standing
// in front of a machine asks ONE question: "这台设备现在怎么样,我该做什么?"
// This page answers it in one screen: state verdict + live metrics + open
// alerts + latest report conclusion + learned knowledge, with the deep pages
// one click away.
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import { fmtNum, relativeTime, levelMeta, trustMeta } from '../theme'
import StateBadge from '../components/StateBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'
import CoverageBar from '../components/CoverageBar.vue'
import Sparkline from '../components/Sparkline.vue'
import MarkdownView from '../components/MarkdownView.vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()
const route = useRoute()
const router = useRouter()

const assetId = ref(String(route.params.assetId || ''))
const knowledge = ref<any>(null)
const reports = ref<any[]>([])
const loadingSide = ref(false)
let seq = 0

const card = computed(() => rt.assetById(assetId.value))
const live = computed(() => rt.liveFor(assetId.value))
const liveAlerts = computed(() =>
  rt.alerts.filter((a) => !a.acked && a.asset_id === assetId.value))

const latestReport = computed(() => reports.value[0] || null)
const knowledgeChapters = computed(() => {
  const md: string = knowledge.value?.memory_md || ''
  if (!md.trim()) return []
  const parts = md.split(/^## /m).filter((p) => p.trim())
  return parts.slice(0, 4).map((p) => {
    const nl = p.indexOf('\n')
    return (nl >= 0 ? p.slice(0, nl) : p).trim().replace(/^#\s*/, '')
  })
})

async function loadSide() {
  if (!assetId.value) return
  const mySeq = ++seq
  loadingSide.value = true
  try {
    const [k, r] = await Promise.allSettled([
      api.get<any>(`/api/assets/${assetId.value}/knowledge`),
      api.get<any>(`/api/assets/${assetId.value}/reports`),
    ])
    if (mySeq !== seq) return
    knowledge.value = k.status === 'fulfilled' ? k.value : null
    reports.value = r.status === 'fulfilled' ? (r.value || []) : []
  } finally {
    if (mySeq === seq) loadingSide.value = false
  }
}

function metricUnit(key: string): string {
  const spec = card.value?.config?.metrics?.find((m) => m.key === key)
  return spec?.unit || ''
}

function go(tab: string) {
  router.push(tab === 'monitor' ? `/monitor/${assetId.value}` : `/${tab}?asset=${assetId.value}`)
}

onMounted(() => {
  if (!assetId.value && rt.overviewCards.length) {
    assetId.value = rt.overviewCards[0].asset_id
  } else if (!rt.overviewCards.length) {
    const stop = watch(() => rt.overviewCards.length, (n) => {
      if (n && !assetId.value) assetId.value = rt.overviewCards[0].asset_id
      stop()
    })
  }
  loadSide()
})
watch(() => route.params.assetId, (v) => {
  if (v && v !== assetId.value) { assetId.value = String(v); loadSide() }
})
watch(assetId, () => loadSide())
</script>

<template>
  <div class="page">
    <PageHeader :title="card ? card.name : '设备中心'" :subtitle="card ? `${card.asset_id} · ${card.kind}` : ''">
      <el-select v-model="assetId" style="width:220px" filterable>
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
    </PageHeader>

    <div v-if="!card" style="padding:40px;text-align:center" class="muted">
      {{ rt.ready ? '请选择设备' : '加载中…' }}
    </div>

    <template v-else>
      <!-- verdict strip: the at-a-glance answer -->
      <div class="verdict panel">
        <div class="v-item">
          <StateBadge :state="live?.status || card.runtime.state"
                      :pulse="card.runtime.overall_level === 'critical'" />
          <TrustBadge :trust="live?.trust || card.runtime.trust" />
        </div>
        <div class="v-item grow">
          <div class="headline">{{ live?.headline || card.runtime.headline || '—' }}</div>
          <div class="muted small">
            {{ relativeTime(card.runtime.window_end, rt.nowSec * 1000) }} ·
            {{ card.runtime.sample_count }} 样本
            <template v-if="card.runtime.state_reason"> · {{ card.runtime.state_reason }}</template>
          </div>
        </div>
        <div class="v-item">
          <span class="chip" :style="{
            color: levelMeta[card.runtime.overall_level]?.fg,
            background: levelMeta[card.runtime.overall_level]?.bg }">
            {{ levelMeta[card.runtime.overall_level]?.label || card.runtime.overall_level }}
          </span>
        </div>
        <div class="v-item">
          <CoverageBar :coverage="live?.coverage ?? card.runtime.coverage"
                       :samples="live?.sample_count ?? card.runtime.sample_count" />
        </div>
        <div class="v-item">
          <el-button size="small" type="primary" @click="go('monitor')">实时曲线</el-button>
          <el-button size="small" @click="go('qa')">问一问</el-button>
        </div>
      </div>

      <div class="hub-grid">
        <!-- live values + trend -->
        <el-card shadow="never" class="span2">
          <template #header><b>实时指标</b></template>
          <Sparkline v-if="card.runtime.series?.length" :points="card.runtime.series" />
          <div class="mvs">
            <template v-if="Object.keys(card.runtime.latest_values || {}).length">
              <div v-for="(v, k) in card.runtime.latest_values" :key="k" class="mv">
                <span class="muted small">{{ k }}</span>
                <span class="num v" :style="{ color: levelMeta[card.runtime.overall_level]?.fg }">
                  {{ fmtNum(v, 1) }}<span class="unit muted">{{ metricUnit(String(k)) }}</span>
                </span>
              </div>
            </template>
            <div v-else class="muted small">无最新值 — 到实时监控页立即采集</div>
          </div>
        </el-card>

        <!-- open alerts for THIS asset -->
        <el-card shadow="never">
          <template #header><b>活跃告警</b>
            <el-badge v-if="liveAlerts.length" :value="liveAlerts.length" class="alert-badge" />
          </template>
          <div v-if="liveAlerts.length" class="mini-alerts">
            <div v-for="a in liveAlerts.slice(0, 4)" :key="`${a.asset_id}:${a.ts}`" class="mini-alert"
                 :style="{ borderLeftColor: levelMeta[a.subject || a.overall_level || 'unknown']?.fg }">
              <span class="sev" :style="{ color: levelMeta[a.subject || a.overall_level || 'unknown']?.fg }">
                {{ levelMeta[a.subject || a.overall_level || 'unknown']?.label || a.subject }}
              </span>
              <span class="small">{{ a.headline || a.reason }}</span>
            </div>
            <el-button size="small" link @click="router.push('/alerts')">全部 →</el-button>
          </div>
          <EmptyState v-else icon="✅" text="无活跃告警" />
        </el-card>

        <!-- latest report -->
        <el-card shadow="never">
          <template #header><b>最近报告</b></template>
          <template v-if="latestReport">
            <div class="rep-date mono">{{ latestReport.date }}</div>
            <div class="muted small">
              {{ latestReport.provider || '—' }}
              <el-tag v-if="latestReport.degraded" type="warning" size="small">已降级</el-tag>
            </div>
            <div class="muted small rep-meta">
              状态 {{ levelMeta[latestReport.meta?.status || 'unknown']?.label || latestReport.meta?.status || '—' }} ·
              覆盖 {{ Math.round((latestReport.meta?.coverage || 0) * 100) }}%
            </div>
            <el-button size="small" link style="margin-top:8px" @click="go('reports')">查看全文 →</el-button>
          </template>
          <div v-else class="muted small">暂无归档报告</div>
        </el-card>

        <!-- learned knowledge digest -->
        <el-card shadow="never">
          <template #header><b>已学习的知识</b></template>
          <div v-if="knowledgeChapters.length" class="k-chapters">
            <div v-for="c in knowledgeChapters" :key="c" class="k-chapter small">{{ c }}</div>
            <el-button size="small" link style="margin-top:8px" @click="go('knowledge')">知识库 →</el-button>
          </div>
          <EmptyState v-else icon="📚" text="尚无学习沉淀" hint="等待学习调度或手动触发">
            <el-button size="small" @click="go('knowledge')">去知识库</el-button>
          </EmptyState>
        </el-card>

        <!-- quick facts -->
        <el-card shadow="never">
          <template #header><b>设备档案</b></template>
          <div class="kv"><span class="muted small">描述</span>
            <span class="small">{{ card.description || '—' }}</span></div>
          <div class="kv"><span class="muted small">采样频率</span>
            <span class="num">{{ card.sample_freq_s ?? '—' }}s</span></div>
          <div class="kv"><span class="muted small">分析窗口</span>
            <span class="num">{{ card.window_s ?? '—' }}s</span></div>
          <div class="kv"><span class="muted small">指标数</span>
            <span class="num">{{ card.config?.metrics?.length ?? 0 }}</span></div>
        </el-card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.verdict {
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 14px 18px; margin-bottom: 16px;
}
.v-item { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.v-item.grow { flex: 1; min-width: 220px; flex-direction: column; align-items: flex-start; gap: 4px; }
.headline { font-size: 14px; font-weight: 600; }
.hub-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}
.span2 { grid-column: span 2; }
.mvs { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 10px; margin-top: 10px; }
.mv { display: flex; flex-direction: column; gap: 2px; }
.v { font-size: 20px; font-weight: 700; }
.unit { font-size: 11px; margin-left: 3px; font-weight: 400; }
.alert-badge { margin-left: 8px; }
.mini-alerts { display: flex; flex-direction: column; gap: 8px; }
.mini-alert {
  border-left: 3px solid var(--ea-border); padding: 4px 10px;
  display: flex; flex-direction: column; gap: 2px;
  background: var(--ea-panel-soft); border-radius: 0 var(--ea-r-sm) var(--ea-r-sm) 0;
}
.sev { font-size: 12px; font-weight: 700; }
.rep-date { font-size: 15px; font-weight: 700; margin-bottom: 4px; }
.rep-meta { margin-top: 4px; }
.k-chapters { display: flex; flex-direction: column; gap: 6px; }
.k-chapter { padding: 6px 10px; background: var(--ea-panel-soft); border-radius: var(--ea-r-sm); }
.kv { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }

@media (max-width: 800px) {
  .span2 { grid-column: span 1; }
}
</style>
