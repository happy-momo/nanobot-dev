<script setup lang="ts">
// TabHistory — the 历史 tab (spec §5.2): a paginated snapshots archive table
// (TanStack Table) with columns 时间(mono) / 级别 / one column per metric /
// 覆盖率 / 可信度, and a row click expanding the per-metric detail (MetricBand
// with that row's actual value + level).
//
// Data: fetchSnapshots(assetId, 100, true) → oldest-first rows; the table
// shows NEWEST-first (an archive browser reads newest→oldest), so the rows are
// reversed once at fetch. No SSE merge here — this tab is the archive view,
// the live curve lives in 实时 (TabLive).
//
// Reactivity: the vue-table adapter only tracks REFS for `data` — plain
// arrays are captured once, so `rows` must stay a ref that the table reads
// through a getter. Column defs ride a computed (the per-metric columns are
// derived from the fetched rows, which land AFTER the table is created).
import { computed, onMounted, ref, watch } from 'vue'
import {
  createColumnHelper, FlexRender, getCoreRowModel, getPaginationRowModel,
  useVueTable, type ColumnDef, type PaginationState,
} from '@tanstack/vue-table'
import { ChevronDown, ChevronRight, RefreshCw } from '@lucide/vue'
import { fetchSnapshots } from '../../api'
import { stateMeta, trustMeta,
  type AlertLevel, type AssetState, type TrustLevel } from '../../theme/meta'
import { toast } from '../../stores/toast'
import ObsPanel from '../../components/ObsPanel.vue'
import EmptyState from '../../components/EmptyState.vue'
import MetricBand from '../../components/MetricBand.vue'
import type { MetricSpecMeta } from '../../theme/meta'
import { useRuntime } from '../../stores/runtime'
import { localizeConditionText } from '../../copy'
import type { RowData } from '@tanstack/table-core'

// column meta carries the numeric-alignment flag — TS augmentation on the
// module where ColumnMeta is DECLARED (table-core; vue-table only re-exports)
declare module '@tanstack/table-core' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    num?: boolean
  }
}

const props = defineProps<{ assetId: string }>()

const rt = useRuntime()

// ------------------------------------------------------------- row shapes --
interface HistMetric {
  key: string
  unit?: string
  mean?: number | null
  max?: number | null
  level?: string
}
interface HistRow {
  filename?: string
  window_end: string
  status?: string
  trust?: string
  coverage: number
  overall_level: string
  sample_count: number
  headline?: string
  metrics?: HistMetric[]
}

const loading = ref(false)
const loadError = ref('')
const rows = ref<HistRow[]>([])
let seq = 0   // stale-response guard: asset switches must not interleave

const PAGE_SIZE = 20
const pagination = ref<PaginationState>({ pageIndex: 0, pageSize: PAGE_SIZE })

// ------------------------------------------------------- metric vocabularies --
/** metric spec source for the MetricBand strips (card config) */
const specs = computed<MetricSpecMeta[]>(() =>
  (rt.assetById(props.assetId)?.config?.metrics || []) as MetricSpecMeta[])
const specOf = (key: string): MetricSpecMeta =>
  specs.value.find((m) => m.key === key) || { key, unit: '' }

/** overall_level → (label, tone). info is a real verdict with no alarm —
 * labeled 正常; insufficient/unknown are data-coverage verdicts, gray. */
function overallOf(level: string): { label: string; color: string } {
  if (level === 'warn') return { label: '警告', color: 'var(--warn)' }
  if (level === 'critical' || level === 'crit') return { label: '严重', color: 'var(--crit)' }
  if (level === 'info') return { label: '正常', color: 'var(--ok)' }
  if (level === 'insufficient') return { label: '数据不足', color: 'var(--unknown)' }
  return { label: '未知', color: 'var(--unknown)' }
}

const TRUST_KEY: Record<string, TrustLevel> = {
  full: 'high', partial: 'medium', low: 'low', none: 'low',
}
function trustOf(trust: string): { label: string; color: string } {
  const t = TRUST_KEY[trust] || 'low'
  return { label: trustMeta[t].label, color: trustMeta[t].color }
}

/** coverage %, tone per CoverageBar (≥80 cyan / ≥50 warn / else crit) */
function coveragePct(c: number): number {
  const v = Number(c) || 0
  const p = v <= 1 ? v * 100 : v
  return Math.round(Math.min(Math.max(p, 0), 100))
}
function coverageColor(c: number): string {
  const p = coveragePct(c)
  return p >= 80 ? 'var(--cyan)' : p >= 50 ? 'var(--warn)' : 'var(--crit)'
}

function fmtTime(iso: string): string {
  const t = iso ? new Date(iso).getTime() : NaN
  if (!Number.isFinite(t)) return iso || '—'
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function fmtVal(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)
}

const metricVal = (r: HistRow, key: string): number | null => {
  const m = r.metrics?.find((x) => x.key === key)
  if (!m) return null
  return typeof m.max === 'number' ? m.max : m.mean ?? null
}
const metricLevel = (r: HistRow, key: string): AlertLevel => {
  const lv = r.metrics?.find((x) => x.key === key)?.level || ''
  if (lv === 'warn') return 'warn'
  if (lv === 'critical' || lv === 'crit') return 'crit'
  return 'ok'
}
function levelColor(lv: AlertLevel): string {
  return lv === 'warn' ? 'var(--warn)' : lv === 'crit' ? 'var(--crit)' : 'var(--tx-hi)'
}
const metricColor = (r: HistRow, key: string): string =>
  metricVal(r, key) === null ? 'var(--tx-dim)' : levelColor(metricLevel(r, key))

/** metric keys in stable first-seen order (newest rows carry the live order) */
const metricKeys = computed<string[]>(() => {
  const out: string[] = []
  for (const r of rows.value) {
    for (const m of r.metrics || []) {
      if (m.key && !out.includes(m.key)) out.push(m.key)
    }
  }
  return out
})

// ------------------------------------------------------------------ columns --
// level/coverage/trust + per-metric columns render CUSTOM bodies (state tone
// needs the row, not just the value), so their defs are accessor-only and the
// <td> switches on column id; only 时间 uses the standard flexRender cell.
const col = createColumnHelper<HistRow>()

const columns = computed<ColumnDef<HistRow, any>[]>(() => {
  const defs: ColumnDef<HistRow, any>[] = [
    col.accessor('window_end', {
      header: '时间',
      cell: (info) => fmtTime(info.getValue()),
    }),
    col.accessor('overall_level', { header: '级别' }),
  ]
  for (const key of metricKeys.value) {
    defs.push(col.accessor((r) => metricVal(r, key),
      { id: `m_${key}`, header: key, meta: { num: true } }))
  }
  defs.push(
    col.accessor('coverage', { header: '覆盖率', meta: { num: true } }),
    col.accessor((r) => r.trust || '', { id: 'trust', header: '可信度' }),
  )
  return defs
})

const table = useVueTable<HistRow>({
  get data() { return rows.value },
  get columns() { return columns.value },
  state: {
    get pagination() { return pagination.value },
  },
  onPaginationChange: (updater) => {
    pagination.value = typeof updater === 'function'
      ? updater(pagination.value)
      : updater
  },
  getCoreRowModel: getCoreRowModel(),
  getPaginationRowModel: getPaginationRowModel(),
  autoResetPageIndex: false,
})

// ------------------------------------------------- expansion (row click / enter) --
const expanded = ref<Set<string>>(new Set())
function rowKey(r: HistRow): string {
  return r.window_end || r.filename || ''
}
function toggleRow(r: HistRow): void {
  const k = rowKey(r)
  if (!k) return
  const next = new Set(expanded.value)
  if (next.has(k)) next.delete(k)
  else next.add(k)
  expanded.value = next
}

// ------------------------------------------------------------------ loading --
async function load(): Promise<void> {
  if (!props.assetId) return
  const mySeq = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchSnapshots(props.assetId, 100, true)
    if (mySeq !== seq) return   // a newer load superseded this one
    const list = Array.isArray(data) ? (data as HistRow[]) : []
    rows.value = [...list].reverse()   // oldest-first api → newest-first table
    pagination.value = { pageIndex: 0, pageSize: PAGE_SIZE }
    expanded.value = new Set()
  } catch (e) {
    if (mySeq !== seq) return
    // failed READ ≠ empty archive: say so, or the engineer regenerates instead
    // of retrying (carried-over honest-failure rule from the old Reports.vue)
    loadError.value = e instanceof Error ? e.message : '历史快照加载失败'
    rows.value = []
    toast('历史快照加载失败', 'warn')
  } finally {
    if (mySeq === seq) loading.value = false
  }
}

onMounted(() => { load() })
watch(() => props.assetId, (v, o) => {
  if (!v || v === o) return
  rows.value = []
  expanded.value = new Set()
  load()
})
</script>

<template>
  <ObsPanel title="历史快照" :meta="`${rows.length} 条`">
    <template #actions>
      <button
        type="button"
        class="th-reload"
        :disabled="loading"
        aria-label="刷新历史"
        @click="load"
      >
        <RefreshCw :size="13" :stroke-width="1.5" :class="{ 'th-spin': loading }" />
      </button>
    </template>

    <!-- load failure: honest surface with retry (not a fake empty archive) -->
    <EmptyState v-if="loadError" label="HISTORY 加载失败" :hint="loadError" />

    <EmptyState
      v-else-if="!loading && !rows.length"
      label="暂无历史快照"
      hint="点击「立即采集」或等待调度器写入第一个窗口"
    />

    <div v-else class="th-wrap">
      <table class="th-table">
        <thead>
          <tr>
            <th
              v-for="h in table.getHeaderGroups()[0]?.headers || []"
              :key="h.id"
              :class="{ 'th-num': h.column.columnDef.meta?.num, 'th-time': h.column.id === 'window_end' }"
            >
              <FlexRender
                v-if="!h.isPlaceholder"
                :render="h.column.columnDef.header"
                :props="h.getContext()"
              />
            </th>
            <th class="th-exp-col" aria-label="展开" />
          </tr>
        </thead>
        <tbody>
          <template v-for="row in table.getRowModel().rows" :key="row.id">
            <tr
              class="th-row"
              role="button"
              tabindex="0"
              :aria-expanded="expanded.has(rowKey(row.original))"
              @click="toggleRow(row.original)"
              @keydown.enter.prevent="toggleRow(row.original)"
            >
              <td
                v-for="cell in row.getVisibleCells()"
                :key="cell.id"
                class="mono"
                :class="{ 'th-num': cell.column.columnDef.meta?.num }"
              >
                <template v-if="cell.column.id === 'overall_level'">
                  <span class="th-lv" :style="{ color: overallOf(String(cell.getValue())).color }">
                    {{ overallOf(String(cell.getValue())).label }}
                  </span>
                </template>
                <template v-else-if="cell.column.id === 'coverage'">
                  <span :style="{ color: coverageColor(Number(cell.getValue())) }">
                    {{ coveragePct(Number(cell.getValue())) }}%
                  </span>
                </template>
                <template v-else-if="cell.column.id === 'trust'">
                  <span :style="{ color: trustOf(String(cell.getValue())).color }">
                    {{ trustOf(String(cell.getValue())).label }}
                  </span>
                </template>
                <template v-else-if="cell.column.id.startsWith('m_')">
                  <span :style="{ color: metricColor(row.original, cell.column.id.slice(2)) }">
                    {{ fmtVal(metricVal(row.original, cell.column.id.slice(2))) }}
                  </span>
                </template>
                <FlexRender
                  v-else
                  :render="cell.column.columnDef.cell"
                  :props="cell.getContext()"
                />
              </td>
              <td class="th-exp-cell">
                <span
                  class="th-chev"
                  :class="{ 'th-chev-open': expanded.has(rowKey(row.original)) }"
                  aria-hidden="true"
                >
                  <ChevronDown v-if="expanded.has(rowKey(row.original))" :size="14" :stroke-width="1.5" />
                  <ChevronRight v-else :size="14" :stroke-width="1.5" />
                </span>
              </td>
            </tr>

            <!-- expanded: per-metric detail with this row's value + level -->
            <tr v-if="expanded.has(rowKey(row.original))" class="th-detail-row">
              <td :colspan="columns.length + 1">
                <div class="th-detail">
                  <p v-if="row.original.headline" class="th-headline mono">
                    {{ localizeConditionText(row.original.headline) }}
                  </p>
                  <div class="th-bands">
                    <div
                      v-for="key in (row.original.metrics || []).map((m) => m.key)"
                      :key="key"
                      class="th-band"
                    >
                      <MetricBand
                        :metric="specOf(key)"
                        :value="metricVal(row.original, key)"
                        :level="metricLevel(row.original, key)"
                      />
                    </div>
                  </div>
                  <p class="th-meta mono">
                    <span>状态 {{ stateMeta[(row.original.status || 'unknown') as AssetState]?.label || '未知' }}</span>
                    <span>样本 {{ row.original.sample_count }}</span>
                    <span>数据窗口结束 {{ fmtTime(row.original.window_end) }}</span>
                  </p>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>

      <!-- pagination: honest count + touch-floor buttons -->
      <div class="th-foot">
        <span class="th-count mono">
          第 {{ pagination.pageIndex + 1 }} / {{ Math.max(1, table.getPageCount()) }} 页 · 共 {{ rows.length }} 条
        </span>
        <div class="th-pager">
          <button
            type="button"
            class="th-page"
            :disabled="!table.getCanPreviousPage()"
            @click="table.previousPage()"
          >上一页</button>
          <button
            type="button"
            class="th-page"
            :disabled="!table.getCanNextPage()"
            @click="table.nextPage()"
          >下一页</button>
        </div>
      </div>
    </div>
  </ObsPanel>
</template>

<style scoped>
.th-reload {
  display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border: 1px solid var(--line-2);
  background: var(--bg2); color: var(--tx); border-radius: var(--r-s);
  cursor: pointer;
}
.th-reload:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.th-reload:disabled { opacity: .55; cursor: default; }
.th-spin { animation: th-rot 1s linear infinite; }
@keyframes th-rot { to { transform: rotate(360deg); } }

.th-wrap { min-width: 0; }

/* table: hairline rows, no zebra — the observatory grid language */
.th-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.th-table th {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line-2);
  color: var(--tx-dim); font-size: 10px; font-weight: 600; letter-spacing: .08em;
  white-space: nowrap;
}
.th-table th.th-time { font-family: var(--font-mono); }
.th-row { border-bottom: 1px solid var(--line); cursor: pointer; }
.th-row:hover { background: var(--bg2); }
.th-row:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: -2px; }
.th-row td { padding: 7px 12px; color: var(--tx); white-space: nowrap; }
.th-num { text-align: right; }
.th-lv { font-weight: 600; letter-spacing: .05em; font-size: 11.5px; }
.th-exp-col { width: 34px; }
.th-exp-cell { text-align: center; }
.th-chev { display: inline-flex; color: var(--tx-dim); vertical-align: middle; }
.th-chev-open { color: var(--cyan); }

/* expanded detail: MetricBand grid + machine facts */
.th-detail-row td { padding: 10px 14px 14px; background: var(--bg2);
  border-bottom: 1px solid var(--line); }
.th-detail { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.th-headline { margin: 0; font-size: 11px; color: var(--tx-dim); }
.th-bands { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px 22px; }
.th-meta { margin: 0; display: flex; gap: 16px; flex-wrap: wrap;
  font-size: 10px; color: var(--tx-dim); }

/* footer: count left, pager right; touch floor 32px */
.th-foot { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 8px 12px; border-top: 1px solid var(--line-2); flex-wrap: wrap; }
.th-count { font-size: 10.5px; color: var(--tx-dim); }
.th-pager { display: flex; align-items: center; gap: 10px; }
.th-page {
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11.5px; padding: 5px 14px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; min-width: 64px;
}
.th-page:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.th-page:disabled { opacity: .45; cursor: default; }
.th-pageinfo { font-size: 11px; color: var(--tx-dim); }

@media (max-width: 699.98px) {
  .th-table th, .th-row td { padding: 6px 8px; font-size: 11px; }
}
</style>
