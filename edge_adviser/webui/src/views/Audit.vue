<script setup lang="ts">
// Audit — the 审计页 (spec §5.5): asset selector + KIND filter chips (fix bug
// C — the old page filtered by OUTCOME, so "确认" was unreachable; the ledger
// is keyed by what happened: run_once/monitor/deep_report/learn/qa/ack/
// run_now/threshold_edit/serve_start) + outcome-colored table (时间/kind/
// outcome/actor/reason/elapsed/window_end) + gap-day rows + `before`-cursor
// loadMore + CSV export (authenticated downloadFile).
//
// The backend enumerates kinds via log_run_event callers; the chip list is the
// vocabulary the ledger actually contains (verified against the demo ledger:
// run_once/ack/qa), with unknown kinds appearing under 其他 rather than being
// hidden — an auditor must see everything the ledger holds.
//
// Gap days: every calendar day from the earliest loaded row to today with no
// rows at all is marked 该日无记录 (traceability: a missing report day must be
// visible, not silently absent — carried over from the old Runlog.vue).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Download, RefreshCw } from '@lucide/vue'
import { downloadRunlog, fetchRunlog } from '../api'
import { useRuntime } from '../stores/runtime'
import { toast } from '../stores/toast'
import ObsPanel from '../components/ObsPanel.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()

// ------------------------------------------------------------------ state --
interface RunlogRow {
  asset_id?: string
  date?: string
  kind: string
  outcome: string
  reason?: string
  detail?: string
  timestamp?: string
  actor?: string
  elapsed_s?: number | null
  window_end?: string
}

const assetId = ref('')
const rows = ref<RunlogRow[]>([])
const loading = ref(false)
const loadingMore = ref(false)
const loadError = ref('')
const nextBefore = ref<string | null>(null)
const total = ref<number | null>(null)
const fKind = ref(localStorage.getItem('ea_audit_kind') || '')
watch(fKind, (v) => localStorage.setItem('ea_audit_kind', v || ''))

const KINDS: Array<{ value: string; label: string }> = [
  { value: 'run_once', label: '采集' },
  { value: 'monitor', label: '巡检' },
  { value: 'deep_report', label: '报告' },
  { value: 'learn', label: '学习' },
  { value: 'qa', label: '问答' },
  { value: 'ack', label: '确认' },
  { value: 'run_now', label: '手动采集' },
  { value: 'threshold_edit', label: '阈值' },
  { value: 'serve_start', label: '启动' },
]

/** kind chips WITH counts over the loaded rows (mono); 其他 catches unknowns */
const kindCounts = computed<Record<string, number>>(() => {
  const out: Record<string, number> = {}
  for (const k of KINDS) out[k.value] = 0
  out._other = 0
  for (const r of rows.value) {
    if (r.kind in out) out[r.kind]++
    else out._other++
  }
  return out
})

const filtered = computed<RunlogRow[]>(() => {
  if (!fKind.value) return rows.value
  if (fKind.value === '_other')
    return rows.value.filter((r) => !KINDS.some((k) => k.value === r.kind))
  return rows.value.filter((r) => r.kind === fKind.value)
})

/** success-rate chip over the filtered set (spec: 成功率 chip) */
const okRate = computed<number | null>(() => {
  const n = filtered.value.length
  if (!n) return null
  return Math.round((filtered.value.filter((r) => r.outcome === 'ok').length / n) * 100)
})

// ------------------------------------------------------------------ loads --
async function load(): Promise<void> {
  if (!assetId.value) return
  loading.value = true
  loadError.value = ''
  rows.value = []
  nextBefore.value = null
  total.value = null
  try {
    const d = await fetchRunlog(assetId.value, undefined, 300)
    rows.value = (d?.rows || []) as RunlogRow[]
    nextBefore.value = d?.next_before ?? null
    total.value = typeof d?.total === 'number' ? d.total : null
  } catch (e) {
    // failed READ ≠ empty ledger: "no rows" and "can't read rows" mean
    // opposite things to an auditor (carried over from the old Runlog.vue)
    loadError.value = e instanceof Error ? e.message : '审计记录加载失败'
  } finally {
    loading.value = false
  }
}

async function loadMore(): Promise<void> {
  if (!assetId.value || !nextBefore.value || loadingMore.value) return
  loadingMore.value = true
  try {
    const d = await fetchRunlog(assetId.value, Number(nextBefore.value), 300)
    const older = (d?.rows || []) as RunlogRow[]
    rows.value = rows.value.concat(older)
    nextBefore.value = d?.next_before ?? null
  } catch (e) {
    toast(e instanceof Error ? `加载更早记录失败：${e.message}` : '加载更早记录失败', 'crit')
  } finally {
    loadingMore.value = false
  }
}

async function exportCsv(): Promise<void> {
  if (!assetId.value) return
  try {
    await downloadRunlog(assetId.value)
    toast('已开始下载 CSV')
  } catch (e) {
    toast(e instanceof Error ? `导出失败：${e.message}` : '导出失败', 'crit')
  }
}

onMounted(() => {
  if (!assetId.value && rt.assets.length) assetId.value = rt.assets[0].asset_id
  load()
})
watch(assetId, () => { load() })

// -------------------------------------------------------------- appearance --
function fmtTime(r: RunlogRow): string {
  const iso = r.timestamp || ''
  const t = iso ? new Date(iso).getTime() : NaN
  if (!Number.isFinite(t)) return iso || '—'
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const OUTCOME_META: Record<string, { label: string; color: string }> = {
  ok: { label: '成功', color: 'var(--ok)' },
  no_data: { label: '无数据', color: 'var(--warn)' },
  gated: { label: '门禁拦截', color: 'var(--stale)' },
  provider_failed: { label: '模型失败', color: 'var(--crit)' },
  exception: { label: '异常', color: 'var(--crit)' },
}
function outcomeOf(o: string): { label: string; color: string } {
  return OUTCOME_META[o] || { label: o || '—', color: 'var(--tx-dim)' }
}

function kindLabel(k: string): string {
  return KINDS.find((x) => x.value === k)?.label || k || '—'
}

function fmtElapsed(v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  if (v < 1) return `${Math.round(v * 1000)}ms`
  if (v < 60) return `${v.toFixed(1)}s`
  return `${Math.floor(v / 60)}m${Math.round(v % 60)}s`
}

function fmtWindow(w: string): string {
  if (!w) return '—'
  const t = new Date(w).getTime()
  if (!Number.isFinite(t)) return w
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** gap days: calendar days from the earliest loaded row to today with no rows */
const gapDays = computed<string[]>(() => {
  if (!filtered.value.length) return []
  const dates = new Set(filtered.value.map((r) => r.date).filter(Boolean))
  const parsed = [...dates]
    .map((d) => new Date(`${d}T00:00:00`))
    .filter((d) => !Number.isNaN(d.getTime()))
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
</script>

<template>
  <div class="page au-page">
    <!-- ─── selector + kind chips + actions ──────────────────────────────── -->
    <div class="au-top">
      <div class="au-chips">
        <span
          v-for="k in KINDS"
          :key="k.value"
          class="au-chip"
          :class="{ 'au-chip-on': fKind === k.value, 'au-chip-zero': !kindCounts[k.value] }"
          role="button"
          tabindex="0"
          :aria-pressed="fKind === k.value"
          @click="fKind = fKind === k.value ? '' : k.value"
          @keydown.enter.prevent="fKind = fKind === k.value ? '' : k.value"
        >
          <span class="au-chip-label">{{ k.label }}</span>
          <span class="au-chip-n mono">{{ kindCounts[k.value] || 0 }}</span>
        </span>
        <span
          v-if="kindCounts._other"
          class="au-chip"
          :class="{ 'au-chip-on': fKind === '_other' }"
          role="button"
          tabindex="0"
          @click="fKind = fKind === '_other' ? '' : '_other'"
        >
          <span class="au-chip-label">其他</span>
          <span class="au-chip-n mono">{{ kindCounts._other }}</span>
        </span>
      </div>

      <div class="au-actions">
        <span v-if="okRate !== null" class="au-rate mono">成功率 {{ okRate }}%</span>
        <button
          type="button"
          class="au-btn"
          :disabled="!assetId || loading"
          aria-label="刷新审计"
          @click="load"
        >
          <RefreshCw :size="13" :stroke-width="1.5" :class="{ 'au-spin': loading }" />
        </button>
        <button
          type="button"
          class="au-btn au-btn-labeled"
          :disabled="!assetId"
          @click="exportCsv"
        >
          <Download :size="13" :stroke-width="1.5" />
          <span>导出 CSV</span>
        </button>
      </div>
    </div>

    <!-- ─── table ────────────────────────────────────────────────────────── -->
    <ObsPanel
      title="AUDIT — 审计账本"
      :meta="rt.assetById(assetId)?.name || assetId"
    >
      <EmptyState
        v-if="loadError"
        label="审计记录加载失败"
        :hint="loadError"
      />

      <EmptyState
        v-else-if="!loading && !rows.length"
        label="暂无审计记录"
        hint="调度器每次采集/巡检/报告/学习都会落账；确认与阈值修改也在这里"
      />

      <template v-else>
        <table class="au-table">
          <thead>
            <tr>
              <th class="au-mono">时间</th>
              <th>kind</th>
              <th>结果</th>
              <th>actor</th>
              <th>reason</th>
              <th class="au-mono au-num">耗时</th>
              <th class="au-mono">window_end</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="r in filtered" :key="`${r.timestamp}-${r.kind}-${r.outcome}`">
              <tr class="au-row">
                <td class="au-mono">{{ fmtTime(r) }}</td>
                <td><span class="au-kind">{{ kindLabel(r.kind) }}</span></td>
                <td>
                  <span class="au-outcome" :style="{ color: outcomeOf(r.outcome).color }">
                    {{ outcomeOf(r.outcome).label }}
                  </span>
                </td>
                <td class="au-dim">{{ r.actor || '—' }}</td>
                <td class="au-reason" :title="r.detail || r.reason || ''">
                  {{ r.reason || r.detail || '—' }}
                </td>
                <td class="au-mono au-num">{{ fmtElapsed(r.elapsed_s) }}</td>
                <td class="au-mono">{{ fmtWindow(r.window_end || '') }}</td>
              </tr>
            </template>
          </tbody>
        </table>

        <!-- gap days render as their own rows (spec §5.5 缺口日行) -->
        <div v-if="gapDays.length" class="au-gaps">
          <span class="au-gap-title">缺口日（无任何审计记录，可对账）：</span>
          <span v-for="g in gapDays" :key="g" class="au-gap mono">{{ g }}</span>
        </div>

        <div class="au-foot">
          <span class="au-count mono">
            已加载 {{ filtered.length }} 条<template v-if="total !== null"> / 全部 {{ total }} 条</template>
          </span>
          <button
            type="button"
            class="au-btn au-btn-labeled"
            :disabled="!nextBefore || loadingMore"
            @click="loadMore"
          >{{ loadingMore ? '加载中…' : '加载更早' }}</button>
        </div>
      </template>
    </ObsPanel>
  </div>
</template>

<style scoped>
.au-page { display: flex; flex-direction: column; gap: 14px; padding: 16px 18px;
  min-height: 0; flex: 1; overflow: hidden auto; }

.au-top { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; }
.au-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.au-chip { display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--line); background: var(--bg1);
  border-radius: var(--r-s); padding: 5px 11px; cursor: pointer; min-height: 32px; }
.au-chip:hover { border-color: var(--line-2); }
.au-chip-on { border-color: var(--cyan-dim); background: var(--bg2); }
.au-chip-zero { opacity: .5; }
.au-chip-label { font-size: 11px; color: var(--tx-dim); }
.au-chip-n { font-size: 12px; color: var(--tx-hi); font-variant-numeric: tabular-nums; }

.au-actions { display: flex; align-items: center; gap: 8px; }
.au-rate { font-size: 11px; color: var(--cyan); font-variant-numeric: tabular-nums; }
.au-btn { display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11.5px; padding: 6px 12px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; white-space: nowrap; }
.au-btn:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.au-btn:disabled { opacity: .45; cursor: default; }
.au-spin { animation: au-rot 1s linear infinite; }
@keyframes au-rot { to { transform: rotate(360deg); } }

/* table */
.au-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
.au-table th { text-align: left; padding: 7px 10px;
  border-bottom: 1px solid var(--line-2); color: var(--tx-dim); font-size: 10px;
  font-weight: 600; letter-spacing: .08em; white-space: nowrap; }
.au-mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.au-num { text-align: right; }
.au-row { border-bottom: 1px solid var(--line); }
.au-row:hover { background: var(--bg2); }
.au-row td { padding: 6px 10px; color: var(--tx); vertical-align: top; }
.au-kind { font-size: 11px; color: var(--tx-hi); }
.au-outcome { font-size: 11px; letter-spacing: .04em; }
.au-dim { color: var(--tx-dim); font-size: 11px; }
.au-reason { max-width: 280px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; font-size: 11px; color: var(--tx-dim); }

/* gap days */
.au-gaps { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 10px 12px; border-top: 1px solid var(--line); }
.au-gap-title { font-size: 11px; color: var(--warn); }
.au-gap { font-size: 10.5px; color: var(--tx-dim); border: 1px solid var(--line-2);
  border-radius: 2px; padding: 1px 6px; }

.au-foot { display: flex; align-items: center; justify-content: space-between;
  gap: 10px; padding: 8px 12px; border-top: 1px solid var(--line-2); }
.au-count { font-size: 10.5px; color: var(--tx-dim); }

@media (max-width: 899.98px) {
  .au-table th, .au-row td { padding: 5px 7px; font-size: 10.5px; }
  .au-reason { max-width: 140px; }
}
</style>
