<script setup lang="ts">
// TabReports — the 报告 tab (spec §5.2): left date list (fetchReports) + right
// DATA-SCOPE badge (deterministic provenance, cyan) + MarkdownView body
// (marked + DOMPurify) + TaskProgress generation (run: triggerReport → token →
// /stream/task/{token}; bug B fixed by TaskProgress) + authenticated
// .md/.json download (downloadFile rides the bearer token; window.open would
// 401 under PIN mode).
//
// The report body is AI content → its frame is violet; the DATA-SCOPE badge
// and the action row stay cyan (facts + actions). Asset switch mid-flight is
// guarded by a request sequence so A's slower response never paints B's view.
import { computed, onMounted, ref, watch } from 'vue'
import { Download, FileText, RefreshCw } from '@lucide/vue'
import { downloadReport, fetchReport, fetchReports, triggerReport } from '../../api'
import { toast } from '../../stores/toast'
import ObsPanel from '../../components/ObsPanel.vue'
import EmptyState from '../../components/EmptyState.vue'
import ScopeBadge from '../../components/ScopeBadge.vue'
import MarkdownView from '../../components/MarkdownView.vue'
import TaskProgress from '../../components/TaskProgress.vue'

const props = defineProps<{ assetId: string }>()

// --------------------------------------------------------------- data state --
interface ReportListRow {
  date: string
  degraded?: boolean
  provider?: string
  meta?: Record<string, any> | null
  has_report?: boolean
}
interface ReportDoc {
  date?: string
  report_md?: string | null
  meta?: Record<string, any> | null
  degraded?: boolean
  provider?: string
}

const list = ref<ReportListRow[]>([])
const current = ref<ReportDoc | null>(null)
const selectedDate = ref('')
const loadingList = ref(false)
const loadingDetail = ref(false)
const listError = ref('')
let seq = 0   // stale-response guard across asset switches

const displayName = (row: ReportListRow): string =>
  row.provider || row.meta?.provider || '—'

// ------------------------------------------------------------------- loads --
async function loadList(): Promise<void> {
  if (!props.assetId) return
  const mySeq = ++seq
  loadingList.value = true
  listError.value = ''
  try {
    const rows = await fetchReports(props.assetId)
    if (mySeq !== seq) return
    list.value = Array.isArray(rows) ? (rows as ReportListRow[]) : []
    // clear the list spinner BEFORE awaiting open(): open() takes its own seq
    // ticket, which would strand this guard and leave the reload button
    // disabled forever (the detail pane has its own loadingDetail state)
    loadingList.value = false
    // open the newest day by default; keep the current selection if it survives
    const dates = list.value.map((r) => r.date)
    const target = dates.includes(selectedDate.value)
      ? selectedDate.value
      : (dates[0] || '')
    if (target) await open(target)
    else current.value = null
  } catch (e) {
    if (mySeq !== seq) return
    // failed READ ≠ "no reports": showing 暂无报告 here invites the engineer
    // to regenerate instead of retrying (carried over from the old Reports.vue)
    listError.value = e instanceof Error ? e.message : '报告列表加载失败'
    list.value = []
    current.value = null
  } finally {
    if (mySeq === seq) loadingList.value = false
  }
}

async function open(date: string): Promise<void> {
  const mySeq = ++seq   // own ticket: a click during loadList's auto-open must
                        // not race it (selectedDate B with current A)
  selectedDate.value = date
  loadingDetail.value = true
  try {
    const doc = await fetchReport(props.assetId, date)
    if (mySeq !== seq) return   // asset switched mid-flight
    current.value = doc
  } catch (e) {
    if (mySeq !== seq) return
    toast(e instanceof Error ? `报告详情加载失败：${e.message}` : '报告详情加载失败', 'crit')
    current.value = null
  } finally {
    if (mySeq === seq) loadingDetail.value = false
  }
}

// ---------------------------------------------------------------- downloads --
const downloading = ref<'md' | 'json' | null>(null)

async function download(kind: 'md' | 'json'): Promise<void> {
  if (!selectedDate.value || downloading.value) return
  downloading.value = kind
  try {
    await downloadReport(props.assetId, selectedDate.value, kind)
    toast(`已开始下载 ${selectedDate.value}.${kind}`)
  } catch (e) {
    toast(e instanceof Error ? `下载失败：${e.message}` : '下载失败', 'crit')
  } finally {
    downloading.value = null
  }
}

// --------------------------------------------------------------- generation --
/** TaskProgress `done` fires on EVERY terminal phase (done/failed/timeout) —
 * the list re-fetch picks up new archives on success and stays honest on
 * failure (nothing cached to invalidate, so a plain reload is the refresh). */
function onReportDone(): void {
  if (!current.value && list.value.length) return   // nothing to refresh into
  loadList()
}

onMounted(() => { loadList() })
watch(() => props.assetId, (v, o) => {
  if (!v || v === o) return
  list.value = []
  current.value = null
  selectedDate.value = ''
  loadList()
})
</script>

<template>
  <div class="tr">
    <!-- ─── left: date list ──────────────────────────────────────────────── -->
    <aside class="tr-list">
      <div class="tr-list-head">
        <span class="eyebrow">REPORTS · 归档</span>
        <button
          type="button"
          class="tr-reload"
          :disabled="loadingList"
          aria-label="刷新报告列表"
          @click="loadList"
        >
          <RefreshCw :size="13" :stroke-width="1.5" :class="{ 'tr-spin': loadingList }" />
        </button>
      </div>

      <div v-if="listError" class="tr-list-err">
        <p class="tr-err-msg">{{ listError }}</p>
        <button type="button" class="tr-retry" @click="loadList">重试</button>
      </div>

      <EmptyState
        v-else-if="!loadingList && !list.length"
        label="暂无归档报告"
        hint="点击「生成报告」或等待每日调度"
      />

      <div v-else class="tr-items">
        <button
          v-for="r in list"
          :key="r.date"
          type="button"
          class="tr-item"
          :class="{ 'tr-item-on': selectedDate === r.date }"
          @click="open(r.date)"
        >
          <span class="tr-dot" :class="r.degraded ? 'tr-dot-warn' : 'tr-dot-ok'" aria-hidden="true" />
          <span class="tr-item-body">
            <span class="tr-date mono">{{ r.date }}</span>
            <span class="tr-prov">{{ displayName(r) }}{{ r.degraded ? ' · 已降级' : '' }}</span>
          </span>
        </button>
      </div>
    </aside>

    <!-- ─── right: badge + body + actions ───────────────────────────────── -->
    <section class="tr-view">
      <!-- actions: generation (cyan action) + downloads. The action row sits
           OUTSIDE the `current` gate — on a fresh asset no archive exists yet,
           and the first report must be generable from this tab (the old bug:
           both empty-states pointed at a 生成报告 button that only rendered
           after a report already existed). Downloads stay disabled without a
           selected day; generation is always reachable. -->
      <div v-if="!listError" class="tr-actions">
        <TaskProgress label="生成报告" :run="() => triggerReport(props.assetId)" @done="onReportDone" />
        <div class="tr-dl">
          <button
            type="button"
            class="tr-btn"
            :disabled="!selectedDate || downloading !== null"
            @click="download('md')"
          >
            <Download :size="13" :stroke-width="1.5" />
            <span>{{ downloading === 'md' ? '下载中…' : '下载 .md' }}</span>
          </button>
          <button
            type="button"
            class="tr-btn"
            :disabled="!selectedDate || downloading !== null"
            @click="download('json')"
          >
            <Download :size="13" :stroke-width="1.5" />
            <span>{{ downloading === 'json' ? '下载中…' : '下载 .json' }}</span>
          </button>
        </div>
      </div>

      <EmptyState
        v-if="!current && !loadingDetail && !listError"
        label="未选择报告"
        hint="左侧选择日期，或点击上方「生成报告」"
      />

      <template v-else-if="current">
        <!-- deterministic provenance (cyan) -->
        <ScopeBadge :meta="current.meta" />

        <!-- AI content (violet frame): the report markdown itself -->
        <ObsPanel title="REPORT" :meta="current.date || selectedDate">
          <template #actions>
            <span class="tr-ai-marker">AI</span>
          </template>
          <div v-if="loadingDetail" class="tr-skeleton" aria-hidden="true" />
          <div v-else-if="current.report_md" class="tr-body">
            <MarkdownView :source="current.report_md" />
          </div>
          <EmptyState
            v-else
            label="该日无报告正文"
            hint="归档存在但正文缺失 — 可重新生成"
          >
            <template #icon><FileText :size="18" :stroke-width="1.5" /></template>
          </EmptyState>
        </ObsPanel>
      </template>
    </section>
  </div>
</template>

<style scoped>
.tr {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

/* ── left list ── */
.tr-list { border: 1px solid var(--line); background: var(--bg1);
  border-radius: var(--r-m); overflow: hidden; display: flex; flex-direction: column;
  min-width: 0; }
.tr-list-head { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--line); }
.tr-reload { display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border: 1px solid var(--line-2); background: var(--bg2);
  color: var(--tx); border-radius: var(--r-s); cursor: pointer; }
.tr-reload:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.tr-reload:disabled { opacity: .55; cursor: default; }
.tr-spin { animation: tr-rot 1s linear infinite; }
@keyframes tr-rot { to { transform: rotate(360deg); } }

.tr-list-err { padding: 14px 12px; display: flex; flex-direction: column; gap: 10px; }
.tr-err-msg { margin: 0; font-size: 11.5px; color: var(--tx-dim); line-height: 1.5; }
.tr-retry { border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11.5px; padding: 5px 14px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; }
.tr-retry:hover { border-color: var(--cyan-dim); color: var(--tx-hi); }

.tr-items { display: flex; flex-direction: column; max-height: 62vh; overflow-y: auto; }
.tr-item {
  display: flex; align-items: flex-start; gap: 10px; padding: 9px 12px;
  border: 0; border-bottom: 1px solid var(--line); background: none;
  cursor: pointer; text-align: left; min-height: 44px;
}
.tr-item:last-child { border-bottom: 0; }
.tr-item:hover { background: var(--bg2); }
.tr-item-on { background: var(--bg2); box-shadow: inset 2px 0 0 var(--cyan); }
.tr-dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
.tr-dot-ok { background: var(--ok); }
.tr-dot-warn { background: var(--warn); }
.tr-item-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.tr-date { font-size: 12px; color: var(--tx-hi); font-variant-numeric: tabular-nums; }
.tr-item-on .tr-date { color: var(--cyan); }
.tr-prov { font-size: 10px; color: var(--tx-dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── right view ── */
.tr-view { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.tr-actions { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; }
.tr-dl { display: flex; gap: 8px; }
.tr-btn {
  display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 12px; padding: 7px 14px; border-radius: var(--r-s); cursor: pointer;
  letter-spacing: .04em; min-height: 32px; white-space: nowrap;
}
.tr-btn:hover:not(:disabled) { background: var(--bg3); border-color: var(--cyan-dim); color: var(--tx-hi); }
.tr-btn:disabled { opacity: .55; cursor: default; }

.tr-ai-marker { color: var(--violet); font-weight: 700; letter-spacing: .14em;
  font-size: 10px; border: 1px solid var(--violet); border-radius: 2px; padding: 0 4px; }

/* AI content frame — violet ONLY here (spec: 紫 = AI 解读专属色) */
.tr-view :deep(.panel-frame) { border-color: color-mix(in srgb, var(--violet) 35%, var(--line)); }
.tr-body { padding: 14px 16px 16px; }
.tr-skeleton { height: 320px; background: var(--bg2);
  background-image: linear-gradient(var(--bg3) 12%, transparent 12%);
  background-size: 100% 34px; }

@media (max-width: 899.98px) {
  .tr { grid-template-columns: 1fr; }
  .tr-items { flex-direction: row; overflow-x: auto; max-height: none; }
  .tr-item { flex-shrink: 0; border-bottom: 0; border-right: 1px solid var(--line); }
}
</style>
