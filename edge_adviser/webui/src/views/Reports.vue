<script setup lang="ts">
// Deep-report browser: pick an asset + a date → render the archived Markdown with
// the machine-generated DATA-SCOPE badge, and download md/json. The badge is the
// deterministic provenance; the report body is the LLM's interpretation.
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'

const rt = useRuntime()

const assetId = ref('')
const list = ref<any[]>([])
const loadingList = ref(false)
const current = ref<any>(null)
const loadingDetail = ref(false)
const autoTrigger = ref(false)

async function loadList() {
  if (!assetId.value) return
  loadingList.value = true
  try {
    list.value = (await api.get<any>(`/api/assets/${assetId.value}/reports`)) || []
    if (list.value.length && !current.value) {
      await open(list.value[0].date)
    } else if (list.value.length) {
      await open(current.value?.date || list.value[0].date)
    } else {
      current.value = null
    }
  } finally {
    loadingList.value = false
  }
}

async function open(date: string) {
  loadingDetail.value = true
  try {
    current.value = await api.get<any>(`/api/assets/${assetId.value}/reports/${date}`)
  } finally {
    loadingDetail.value = false
  }
}

async function trigger() {
  const r = await rt.triggerReport(assetId.value)
  if (r?.report_dir) {
    await loadList()
  } else if (r?.error) {
    alert('报告被门禁拒绝：' + r.error)
  }
}

function download(url: string) {
  window.open(url)
}

const meta = computed(() => current.value?.meta || {})

onMounted(async () => {
  if (!assetId.value && rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
  await loadList()
})
watch(() => assetId.value, () => { current.value = null; loadList() })
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <el-select v-model="current.date" v-if="current" style="width:140px" @change="open">
        <el-option v-for="r in list" :key="r.date" :value="r.date" :label="r.date" />
      </el-select>
      <div class="spacer" />
      <el-button v-if="current" size="small" @click="download(`/api/assets/${assetId}/reports/${current.date}/download.md`)">下载 .md</el-button>
      <el-button v-if="current" size="small" @click="download(`/api/assets/${assetId}/reports/${current.date}/download.json`)">下载 .json</el-button>
      <el-button size="small" type="primary" @click="trigger">生成报告</el-button>
    </div>

    <div v-if="!current" class="muted" style="padding:40px;text-align:center">
      {{ loadingList ? '加载中…' : '暂无归档报告。点击「生成报告」或等待每日调度。' }}
    </div>

    <template v-else>
      <!-- DATA-SCOPE badge: machine-generated provenance, fixed & prominent -->
      <div class="badge" style="margin-bottom:16px">
        <div class="badge-title">DATA-SCOPE · 数据范围（机器生成）</div>
        <div class="badge-grid">
          <div class="b"><span class="muted">状态</span> <b>{{ meta.status || '—' }}</b></div>
          <div class="b"><span class="muted">可信度</span> <b>{{ meta.trust || '—' }}</b></div>
          <div class="b"><span class="muted">样本数</span> <b class="num">{{ meta.sample_count }}</b></div>
          <div class="b"><span class="muted">覆盖率</span> <b class="num">{{ Math.round((meta.coverage || 0) * 100) }}%</b></div>
          <div class="b"><span class="muted">时间窗</span> <b>{{ meta.window_start?.slice(0,19) }} → {{ meta.window_end?.slice(0,19) }}</b></div>
          <div class="b"><span class="muted">Provider</span> <b>{{ meta.provider || '—' }}</b></div>
          <div class="b"><span class="muted">降级</span>
            <el-tag :type="meta.degraded ? 'warning' : 'success'" size="small">{{ meta.degraded ? '已降级' : '未降级' }}</el-tag>
          </div>
          <div class="b"><span class="muted">生成时间</span> <b>{{ (meta.generated_at || '').slice(0,19) }}</b></div>
        </div>
        <div v-if="(meta.status || '') !== 'healthy'" class="badge-note" :style="{ color: '#f5b14c' }">
          注意：本报告基于低可信/不完整数据，结论需谨慎，建议先核查数据采集。
        </div>
      </div>

      <el-card shadow="never" v-loading="loadingDetail">
        <div class="report" v-html="renderMd(current.report_md || '')" />
      </el-card>
    </template>
  </div>
</template>

<script lang="ts">
// minimal markdown → html (headings, lists, bold, tables) — no heavy md dep
function renderMd(src: string): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const lines = esc(src).split('\n')
  let html = ''
  let inTable = false
  for (let i = 0; i < lines.length; i++) {
    let l = lines[i]
    // bold
    l = l.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    if (l.startsWith('### ')) { html += `<h3>${l.slice(4)}</h3>`; inTable = false; continue }
    if (l.startsWith('## ')) { html += `<h2>${l.slice(3)}</h2>`; inTable = false; continue }
    if (l.startsWith('# ')) { html += `<h1>${l.slice(2)}</h1>`; inTable = false; continue }
    if (l.startsWith('|')) {
      if (!inTable) { html += '<table><thead><tr>'; inTable = true }
      const cells = l.replace(/^\||\|$/g, '').split('|').map(c => c.trim())
      const tag = inTable === 'thead' ? 'th' : 'td'
      // header row = first row
      html += `<tr>${cells.map(c => `<${inTable === 'thead' ? 'th' : 'td'}>${c}</${inTable === 'thead' ? 'th' : 'td'}>`).join('')}</tr>`
      if (inTable === 'thead') inTable = 'first'
      continue
    }
    if (inTable) { html += '</tbody></table>'; inTable = false }
    if (l.startsWith('- ')) { html += `<li>${l.slice(2)}</li>`; continue }
    if (l === '') { html += '<br/>'; continue }
    html += `<p>${l}</p>`
  }
  if (inTable) html += '</tbody></table>'
  return `<div class="md">${html}</div>`
}
</script>

<style scoped>
.spacer { flex: 1; }
.badge { background: var(--ea-panel); border: 1px solid var(--ea-border); border-radius: 12px; padding: 14px 16px; }
.badge-title { font-weight: 700; margin-bottom: 10px; color: var(--ea-accent); }
.badge-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px,1fr)); gap: 8px 18px; }
.b { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.badge-note { margin-top: 10px; font-size: 13px; }
.report :deep(.md) { line-height: 1.7; }
.report :deep(h1) { font-size: 20px; }
.report :deep(h2) { font-size: 17px; margin-top: 18px; border-bottom: 1px solid var(--ea-border); padding-bottom: 6px; }
.report :deep(h3) { font-size: 15px; }
.report :deep(table) { border-collapse: collapse; margin: 10px 0; width: 100%; }
.report :deep(th), .report :deep(td) { border: 1px solid var(--ea-border); padding: 6px 10px; text-align: left; }
.report :deep(th) { background: #12283a; }
</style>