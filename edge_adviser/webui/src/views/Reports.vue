<script setup lang="ts">
// Deep-report browser: date timeline on the left, report content on the right.
// The machine-generated DATA-SCOPE badge stays fixed above the markdown body —
// deterministic provenance vs LLM interpretation remain visibly separated.
// Downloads use the authenticated downloadFile helper (window.open loses the
// bearer token under PIN mode).
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, downloadFile } from '../api/http'
import { useRuntime } from '../stores/runtime'
import { formatTs, fmtNum, metaOf, trustMeta } from '../theme'
import MarkdownView from '../components/MarkdownView.vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()

const assetId = ref('')
const list = ref<any[]>([])
const loadingList = ref(false)
const current = ref<any>(null)
const loadingDetail = ref(false)
const generating = ref(false)
const loadError = ref('')
// request-sequence guard: switching A→B quickly must never let A's slower
// response overwrite B's view (the operator would see another device's
// reports with no hint anything is wrong)
let seq = 0

async function loadList() {
  if (!assetId.value) return
  const mySeq = ++seq
  loadingList.value = true
  loadError.value = ''
  try {
    const l = (await api.get<any>(`/api/assets/${assetId.value}/reports`)) || []
    if (mySeq !== seq) return  // stale response: a newer selection superseded it
    list.value = l
    if (list.value.length && !current.value) {
      await open(list.value[0].date)
    } else if (list.value.length && current.value) {
      const still = list.value.find((r) => r.date === current.value?.date)
      await open(still?.date || list.value[0].date)
    } else {
      current.value = null
    }
  } catch (e: any) {
    if (mySeq !== seq) return
    // distinguish a failed READ from "no reports" — showing 暂无报告 here
    // invites the engineer to regenerate instead of retrying
    loadError.value = e?.message || '报告列表加载失败'
    list.value = []
    current.value = null
  } finally {
    if (mySeq === seq) loadingList.value = false
  }
}

async function open(date: string) {
  const mySeq = seq
  loadingDetail.value = true
  try {
    const d = await api.get<any>(`/api/assets/${assetId.value}/reports/${date}`)
    if (mySeq !== seq) return  // asset switched mid-flight
    current.value = d
  } catch (e: any) {
    if (mySeq !== seq) return
    ElMessage.error(e?.message || '报告详情加载失败')
    current.value = null
  } finally {
    if (mySeq === seq) loadingDetail.value = false
  }
}

async function trigger() {
  // Guard against an empty selection (e.g. clicked before the assets loaded)
  if (!assetId.value) {
    if (rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
    else { ElMessage.warning('尚无可用设备'); return }
  }
  generating.value = true
  try {
    // the backend returns a token immediately and runs the (minutes-long)
    // generation in the background; completion arrives on the task stream
    const r = await rt.triggerReport(assetId.value)
    const token = r?.token
    if (!token) { generating.value = false; return }
    const close = await watchTask(token, (phase) => {
      if (phase === 'done') {
        generating.value = false
        ElMessage.success('报告已生成并归档')
        loadList()
      } else if (phase === 'failed') {
        generating.value = false
        ElMessage.error('报告生成失败（详见审计页）')
      }
    })
    taskCloser = close
  } catch (e: any) {
    generating.value = false
    ElMessage.error(e?.message || '生成失败')
  }
}

// watch /stream/task/{token} until the action reports done/failed
let taskCloser: (() => void) | null = null
function watchTask(token: string, onPhase: (phase: string) => void): Promise<() => void> {
  return import('../api/http').then(({ connectSSE }) => {
    const close = connectSSE(`/stream/task/${token}`, (kind, data) => {
      if (data?.phase === 'done' || data?.phase === 'failed') {
        onPhase(data.phase)
        close()
        taskCloser = null
      }
    })
    // safety timeout: 10 min without a phase event → give up watching
    const timer = window.setTimeout(() => { onPhase('timeout'); close(); taskCloser = null }, 600_000)
    return () => { window.clearTimeout(timer); close() }
  })
}

onBeforeUnmount(() => { taskCloser?.() })

async function download(kind: 'md' | 'json') {
  if (!current.value || !assetId.value) return
  try {
    await downloadFile(
      `/api/assets/${assetId.value}/reports/${current.value.date}/download.${kind}`,
      `${assetId.value}_${current.value.date}.${kind === 'md' ? 'md' : 'json'}`)
    ElMessage.success('已开始下载')
  } catch (e: any) {
    ElMessage.error(e?.message || '下载失败')
  }
}

const meta = computed(() => current.value?.meta || {})
const statusTone = computed(() => metaOf(meta.value.status))
const trustTone = computed(() => trustMeta[meta.value.trust || 'none'])

onMounted(async () => {
  if (!assetId.value && rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
  await loadList()
})
watch(() => assetId.value, () => {
  current.value = null
  loadList()
})
</script>

<template>
  <div class="page">
    <PageHeader title="深度报告" subtitle="每日归档 · 机器生成数据范围 + 模型解读">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <el-button size="small" type="primary" :loading="generating" @click="trigger">
        生成报告
      </el-button>
    </PageHeader>

    <div v-if="loadError" class="muted" style="padding:40px;text-align:center">
      <div style="color:#f4554f;margin-bottom:8px">⚠ 报告列表加载失败</div>
      <div class="small">{{ loadError }}</div>
      <el-button size="small" style="margin-top:12px" @click="loadList">重试</el-button>
    </div>

    <div v-else-if="!list.length && !loadingList" class="muted" style="padding:40px;text-align:center">
      暂无归档报告。点击「生成报告」或等待每日调度。
    </div>

    <div v-else class="layout">
      <!-- left: archive timeline -->
      <aside class="timeline">
        <div v-for="r in list" :key="r.date" class="tl-item"
             :class="{ active: current?.date === r.date }" @click="open(r.date)">
          <div class="dot" :style="{ background: r.degraded ? '#f5b14c' : '#3ddc97' }" />
          <div class="tl-body">
            <div class="tl-date">{{ r.date }}</div>
            <div class="muted small">{{ r.provider || '—' }}{{ r.degraded ? ' · 已降级' : '' }}</div>
          </div>
        </div>
      </aside>

      <!-- right: report content -->
      <section class="content" v-loading="loadingDetail">
        <template v-if="current">
          <!-- DATA-SCOPE badge: machine-generated provenance, fixed & prominent -->
          <div class="badge">
            <div class="badge-title">DATA-SCOPE · 数据范围（机器生成）</div>
            <div class="badge-grid">
              <div class="b"><span class="muted">状态</span>
                <b :style="{ color: statusTone.fg }">{{ statusTone.label }}</b>
              </div>
              <div class="b"><span class="muted">可信度</span>
                <b :style="{ color: trustTone.tone }">{{ trustTone.label }}</b>
              </div>
              <div class="b"><span class="muted">样本数</span> <b class="num">{{ meta.sample_count }}</b></div>
              <div class="b"><span class="muted">覆盖率</span>
                <b class="num">{{ Math.round((meta.coverage || 0) * 100) }}%</b>
              </div>
              <div class="b"><span class="muted">时间窗</span>
                <b class="mono">{{ meta.window_start?.slice(0,19) }} → {{ meta.window_end?.slice(0,19) }}</b>
              </div>
              <div class="b"><span class="muted">Provider</span> <b>{{ meta.provider || '—' }}</b></div>
              <div class="b"><span class="muted">降级</span>
                <el-tag :type="meta.degraded ? 'warning' : 'success'" size="small">
                  {{ meta.degraded ? '已降级' : '未降级' }}
                </el-tag>
              </div>
              <div class="b"><span class="muted">生成时间</span>
                <b class="mono">{{ (meta.generated_at || '').slice(0,19) }}</b>
              </div>
            </div>
            <div v-if="(meta.status || '') !== 'healthy'" class="badge-note"
                 :style="{ color: '#f5b14c' }">
              注意：本报告基于低可信/不完整数据，结论需谨慎，建议先核查数据采集。
            </div>
          </div>

          <el-card shadow="never">
            <div class="head-actions">
              <el-button size="small" @click="download('md')">下载 .md</el-button>
              <el-button size="small" @click="download('json')">下载 .json</el-button>
            </div>
            <MarkdownView :source="current.report_md || ''" />
          </el-card>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 200px 1fr; gap: 16px; align-items: start; }
.timeline {
  background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-md); padding: 8px;
  display: flex; flex-direction: column; gap: 4px;
  max-height: 70vh; overflow-y: auto;
}
.tl-item {
  display: flex; gap: 10px; padding: 8px 10px; border-radius: var(--ea-r-sm);
  cursor: pointer; align-items: flex-start;
}
.tl-item:hover { background: var(--ea-panel-soft); }
.tl-item.active { background: var(--ea-accent-soft); }
.dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
.tl-date { font-weight: 600; font-size: 13px; }
.content { min-width: 0; }
.head-actions { display: flex; gap: 8px; justify-content: flex-end; margin-bottom: 10px; }
.badge {
  background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-md); padding: 14px 16px; margin-bottom: 16px;
}
.badge-title { font-weight: 700; margin-bottom: 10px; color: var(--ea-accent); }
.badge-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px 18px; }
.b { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.badge-note { margin-top: 10px; font-size: 13px; }

@media (max-width: 800px) {
  .layout { grid-template-columns: 1fr; }
  .timeline { flex-direction: row; overflow-x: auto; max-height: none; }
  .tl-item { flex-shrink: 0; }
}
</style>
