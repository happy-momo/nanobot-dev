<script setup lang="ts">
// Alerts — the 告警中心 (spec §5.3): severity count chips (mono) + filters
// (asset / acked state, Reka Select) + TanStack Table (状态色左竖条 | 资产 |
// 标题 | 首次时间 mono | 持续时长 live-ticking mono | 确认 button). Row click
// → AckDialog; bulk ack (per-asset / fleet) sits right of the filter bar and
// goes through the same AckDialog (Reka AlertDialog second-confirms the sweep).
//
// Data: rt.alerts (runtime store; SSE alert stream + bootstrap loadAlerts).
// planned_downtime/unknown/insufficient never appear (backend exempt). The
// acked history folds into the same table behind the 确认状态 filter — the
// operator's note rides the row as a quoted block (spec: 备注展示为引用块).
//
// 持续时长: now − ts, re-ticked every 30s (live duration without a per-second
// render storm). Filters persist in localStorage (shift handover).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  createColumnHelper, FlexRender, getCoreRowModel, useVueTable,
  type ColumnDef, type RowData,
} from '@tanstack/vue-table'
import {
  SelectContent, SelectItem, SelectItemText, SelectRoot, SelectTrigger,
  SelectValue,
} from 'reka-ui'
import { BellRing } from '@lucide/vue'
import { useRuntime, type AlertRec } from '../stores/runtime'
import { toast } from '../stores/toast'
import { stateMeta } from '../theme/meta'
import ObsPanel from '../components/ObsPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import AckDialog from '../components/AckDialog.vue'

// column meta: mono-column flag (timestamps/durations render --font-mono).
// TS augmentation on the module where ColumnMeta is DECLARED (table-core) —
// identical type params to TabHistory's `num` augmentation or they merge-fail.
declare module '@tanstack/table-core' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    mono?: boolean
  }
}

const rt = useRuntime()

// --------------------------------------------------------------- filters --
function readFilter(key: string, fallback = ''): string {
  try {
    return localStorage.getItem(key) || fallback
  } catch {
    return fallback
  }
}
function saveFilter(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // private/storage-disabled mode keeps filters only for this session
  }
}

const fAsset = ref(readFilter('ea_alert_asset'))
const fSeverity = ref(readFilter('ea_alert_sev'))
const fAcked = ref(readFilter('ea_alert_acked', 'open'))
watch(fAsset, (v) => saveFilter('ea_alert_asset', v || ''))
watch(fSeverity, (v) => saveFilter('ea_alert_sev', v || ''))
watch(fAcked, (v) => saveFilter('ea_alert_acked', v))

const severities = [
  { value: 'warn', label: '警告' },
  { value: 'critical', label: '严重' },
  { value: 'offline', label: '断连' },
  { value: 'stale', label: '数据过时' },
]

const ACKED_OPTS = [
  { value: 'open', label: '未确认' },
  { value: 'acked', label: '已确认' },
  { value: 'all', label: '全部' },
]

// Reka Select forbids empty-string item values, so the "all" option carries a
// sentinel and a computed accessor maps it to the filter's '' (open) state.
const ALL_ASSETS = 'all-assets'
const fAssetSel = computed<string>({
  get: () => fAsset.value || ALL_ASSETS,
  set: (v: string) => { fAsset.value = v === ALL_ASSETS ? '' : v },
})

/** severity count chips over the UNACKED set (mono numbers) */
const counts = computed<Record<string, number>>(() => {
  const out: Record<string, number> = { warn: 0, critical: 0, offline: 0, stale: 0 }
  for (const a of rt.unackedAlerts) {
    const s = normSubject(a)
    if (s in out) out[s]++
    else if (a.overall_level === 'warn') out.warn++
    else if (a.overall_level === 'critical') out.critical++
  }
  return out
})

/** subject is the actionable vocabulary; 'crit' → 'critical' (stateMeta key) */
function normSubject(a: AlertRec): string {
  const s = a.subject || a.state || ''
  return s === 'crit' ? 'critical' : s
}
function levelColor(a: AlertRec): string {
  const m = stateMeta as Record<string, { color: string; label: string }>
  return m[normSubject(a)]?.color || m[a.overall_level || '']?.color || m.unknown.color
}
function levelLabel(a: AlertRec): string {
  const m = stateMeta as Record<string, { color: string; label: string }>
  return m[normSubject(a)]?.label || normSubject(a) || '告警'
}

const filtered = computed<AlertRec[]>(() =>
  rt.alerts
    .filter((a) => (fAcked.value === 'all' ? true : fAcked.value === 'acked' ? a.acked : !a.acked))
    .filter((a) => !fAsset.value || a.asset_id === fAsset.value)
    .filter((a) => !fSeverity.value || normSubject(a) === fSeverity.value)
    .sort((a, b) => b.ts - a.ts))

function assetName(id: string): string {
  return rt.assetById(id)?.name || id
}
function fmtTs(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// live-ticking 持续时长 — 30s interval; a per-second tick would re-render the
// whole table every second for a column nobody reads at that resolution
const nowTick = ref(Date.now())
let tickTimer: number | null = null
onMounted(() => {
  tickTimer = window.setInterval(() => { nowTick.value = Date.now() }, 30_000)
})
onBeforeUnmount(() => { if (tickTimer !== null) window.clearInterval(tickTimer) })

function duration(ts: number): string {
  const s = Math.max(0, Math.floor((nowTick.value - ts * 1000) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

// ------------------------------------------------------------------ table --
const col = createColumnHelper<AlertRec>()
const columns = computed<ColumnDef<AlertRec, any>[]>(() => [
  col.accessor((r) => assetName(r.asset_id), { id: 'asset', header: '资产' }),
  col.accessor((r) => r.headline || r.reason || '', { id: 'headline', header: '标题' }),
  col.accessor('ts', { header: '首次时间', meta: { mono: true } }),
  col.accessor((r) => duration(r.ts), { id: 'dur', header: '持续时长', meta: { mono: true } }),
])
const table = useVueTable<AlertRec>({
  get data() { return filtered.value },
  get columns() { return columns.value },
  getCoreRowModel: getCoreRowModel(),
})

// -------------------------------------------------------------- ack flows --
const ackTarget = ref<AlertRec | null>(null)
const bulkAssets = ref<string[] | undefined>(undefined)
const bulkFleet = ref(false)
const dialogOpen = computed(() => ackTarget.value !== null || !!bulkAssets.value || bulkFleet.value)

function askAck(a: AlertRec): void {
  ackTarget.value = a
  bulkAssets.value = undefined
  bulkFleet.value = false
}
function askBulkAsset(): void {
  const ids = [...new Set(filtered.value.filter((a) => !a.acked).map((a) => a.asset_id))]
  if (!ids.length) { toast('当前筛选下无未确认告警', 'warn'); return }
  ackTarget.value = null
  bulkAssets.value = ids
  bulkFleet.value = false
}
function askBulkFleet(): void {
  if (!rt.unackedAlerts.length) { toast('当前无未确认告警', 'warn'); return }
  ackTarget.value = null
  bulkAssets.value = undefined
  bulkFleet.value = true
}
function closeDialog(): void {
  ackTarget.value = null
  bulkAssets.value = undefined
  bulkFleet.value = false
}
</script>

<template>
  <div class="page al-page">
    <!-- ─── count chips + filters + bulk actions ─────────────────────────── -->
    <div class="al-top">
      <div class="al-chips">
        <span
          v-for="s in severities"
          :key="s.value"
          class="al-chip"
          :class="{ 'al-chip-on': fSeverity === s.value }"
          role="button"
          tabindex="0"
          :aria-pressed="fSeverity === s.value"
          @click="fSeverity = fSeverity === s.value ? '' : s.value"
          @keydown.enter.prevent="fSeverity = fSeverity === s.value ? '' : s.value"
        >
          <span class="al-chip-label">{{ s.label }}</span>
          <span class="al-chip-n mono">{{ counts[s.value] || 0 }}</span>
        </span>
      </div>

      <div class="al-filters">
        <SelectRoot v-model="fAssetSel">
          <SelectTrigger class="al-sel" aria-label="筛选资产">
            <SelectValue :placeholder="fAsset ? assetName(fAsset) : '全部设备'" />
          </SelectTrigger>
          <SelectContent class="al-pop" position="popper" :side-offset="4">
            <SelectItem :value="ALL_ASSETS" class="al-pop-item">全部设备</SelectItem>
            <SelectItem
              v-for="a in rt.assets" :key="a.asset_id"
              :value="a.asset_id" class="al-pop-item"
            >
              <SelectItemText>{{ a.name }}</SelectItemText>
            </SelectItem>
          </SelectContent>
        </SelectRoot>

        <SelectRoot v-model="fAcked">
          <SelectTrigger class="al-sel" aria-label="筛选确认状态">
            <SelectValue :placeholder="ACKED_OPTS.find((o) => o.value === fAcked)?.label || '未确认'" />
          </SelectTrigger>
          <SelectContent class="al-pop" position="popper" :side-offset="4">
            <SelectItem
              v-for="o in ACKED_OPTS" :key="o.value"
              :value="o.value" class="al-pop-item"
            >
              <SelectItemText>{{ o.label }}</SelectItemText>
            </SelectItem>
          </SelectContent>
        </SelectRoot>

        <div class="al-bulk">
          <button
            type="button"
            class="al-bulk-btn"
            :disabled="!filtered.some((a) => !a.acked)"
            @click="askBulkAsset"
          >批量确认本页</button>
          <button
            type="button"
            class="al-bulk-btn al-bulk-fleet"
            :disabled="!rt.unackedAlerts.length"
            @click="askBulkFleet"
          >全场确认</button>
        </div>
      </div>
    </div>

    <!-- ─── table ────────────────────────────────────────────────────────── -->
    <ObsPanel title="ALERTS — 告警列表" :meta="`${filtered.length} 条`">
      <EmptyState
        v-if="!filtered.length"
        label="无符合筛选的告警"
        hint="告警只对真实状态变化触发；计划停机与数据不足状态后端已豁免"
      >
        <template #icon><BellRing :size="18" :stroke-width="1.5" /></template>
      </EmptyState>

      <table v-else class="al-table">
        <thead>
          <tr>
            <th
              v-for="h in table.getHeaderGroups()[0]?.headers || []"
              :key="h.id"
              :class="{ 'al-mono-col': h.column.columnDef.meta?.mono }"
            >
              <FlexRender v-if="!h.isPlaceholder" :render="h.column.columnDef.header" :props="h.getContext()" />
            </th>
            <th class="al-act-col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in table.getRowModel().rows"
            :key="`${row.original.asset_id}:${row.original.ts}`"
            class="al-row"
            :class="{ 'al-row-acked': row.original.acked }"
            role="button"
            tabindex="0"
            :aria-label="`查看告警 ${row.original.headline || ''}`"
            @click="askAck(row.original)"
            @keydown.enter.prevent="askAck(row.original)"
          >
            <td v-for="cell in row.getVisibleCells()" :key="cell.id" :class="{ 'al-mono-col': cell.column.columnDef.meta?.mono }">
              <template v-if="cell.column.id === 'asset'">
                <span class="al-asset">
                  <span class="al-bar" :style="{ background: levelColor(row.original) }" />
                  <span>{{ cell.getValue() }}</span>
                </span>
              </template>
              <template v-else-if="cell.column.id === 'headline'">
                <span class="al-head">
                  <span class="al-headline">{{ cell.getValue() || '—' }}</span>
                  <span class="al-subject" :style="{ color: levelColor(row.original) }">
                    {{ levelLabel(row.original) }}</span>
                  <span v-if="row.original.acked" class="al-acked-tag">已确认</span>
                </span>
                <!-- the operator's handling note renders as a quoted block -->
                <span v-if="(row.original as any).ack_note" class="al-note">
                  处理备注：{{ (row.original as any).ack_note }}
                </span>
              </template>
              <FlexRender v-else :render="cell.column.columnDef.cell" :props="cell.getContext()" />
            </td>
            <td class="al-act-col">
              <button
                v-if="!row.original.acked"
                type="button"
                class="al-ack"
                :aria-label="`确认告警 ${row.original.headline || ''}`"
                @click.stop="askAck(row.original)"
              >确认</button>
              <span v-else class="al-acked-done mono">已确认</span>
            </td>
          </tr>
        </tbody>
      </table>
    </ObsPanel>

    <AckDialog
      v-if="dialogOpen"
      :alert="ackTarget"
      :assets="bulkAssets"
      :fleet="bulkFleet"
      @close="closeDialog"
    />
  </div>
</template>

<style scoped>
.al-page { display: flex; flex-direction: column; gap: 14px; padding: 16px 18px;
  min-height: 0; flex: 1; overflow: hidden auto; }

.al-top { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; }
.al-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.al-chip { display: inline-flex; align-items: center; gap: 8px;
  border: 1px solid var(--line); background: var(--bg1);
  border-radius: var(--r-s); padding: 6px 12px; cursor: pointer; min-height: 32px; }
.al-chip:hover { border-color: var(--line-2); }
.al-chip-on { border-color: var(--cyan-dim); background: var(--bg2); }
.al-chip-label { font-size: 11px; color: var(--tx-dim); }
.al-chip-n { font-size: 13px; color: var(--tx-hi); font-variant-numeric: tabular-nums; }

.al-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.al-sel { display: inline-flex; align-items: center; gap: 8px;
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx-hi);
  font-size: 11.5px; padding: 6px 12px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; }
.al-sel:hover { border-color: var(--cyan-dim); }
.al-sel:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: 1px; }
.al-pop { border: 1px solid var(--line-2); background: var(--bg1);
  border-radius: var(--r-s); z-index: 70; }
.al-pop-item { padding: 7px 12px; font-size: 11.5px; color: var(--tx); cursor: pointer; }
.al-pop-item[data-highlighted] { background: var(--bg2); color: var(--cyan); }

.al-bulk { display: flex; gap: 8px; }
.al-bulk-btn { border: 1px solid var(--line-2); background: var(--bg2);
  color: var(--tx); font-size: 11.5px; padding: 6px 14px;
  border-radius: var(--r-s); cursor: pointer; min-height: 32px; white-space: nowrap; }
.al-bulk-btn:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.al-bulk-btn:disabled { opacity: .45; cursor: default; }
.al-bulk-fleet:not(:disabled) { color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, var(--line-2)); }

/* table: hairline rows, severity bar inline with the asset cell */
.al-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.al-table th { text-align: left; padding: 8px 12px;
  border-bottom: 1px solid var(--line-2); color: var(--tx-dim); font-size: 10px;
  font-weight: 600; letter-spacing: .08em; white-space: nowrap; }
.al-mono-col { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.al-row { border-bottom: 1px solid var(--line); cursor: pointer; }
.al-row:hover { background: var(--bg2); }
.al-row:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: -2px; }
.al-row td { padding: 8px 12px; color: var(--tx); vertical-align: top; }
.al-row-acked { opacity: .62; }

.al-asset { display: inline-flex; align-items: center; gap: 8px; }
.al-bar { width: 2px; height: 16px; border-radius: 1px; flex-shrink: 0; }
.al-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.al-headline { font-size: 12px; color: var(--tx-hi); }
.al-subject { font-size: 10.5px; letter-spacing: .05em; }
.al-acked-tag { font-size: 9.5px; color: var(--tx-dim); border: 1px solid var(--line-2);
  border-radius: 2px; padding: 0 5px; }
.al-note { display: block; margin-top: 4px; font-size: 11px; color: var(--tx-dim);
  border-left: 2px solid var(--line-2); padding-left: 8px; line-height: 1.5; }

.al-act-col { width: 92px; text-align: right; white-space: nowrap; }
.al-ack { border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11px; padding: 5px 14px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; }
.al-ack:hover { border-color: var(--cyan-dim); color: var(--tx-hi); }
.al-acked-done { font-size: 10.5px; color: var(--tx-dim); }

@media (max-width: 899.98px) {
  .al-table th, .al-row td { padding: 6px 8px; font-size: 11px; }
}
</style>
