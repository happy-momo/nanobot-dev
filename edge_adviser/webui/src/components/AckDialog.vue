<script setup lang="ts">
// AckDialog — the acknowledge dialog (spec §5.3, Reka AlertDialog): alert
// details + 处理备注 textarea (1–2000 chars) + confirm. The backend records
// the note as feedback (router_runlog.py ack_alert: state.feedback.record
// "【告警处理】…"), so the copy says so honestly.
//
// props.alert === null closes; emit confirmed after a successful ack so the
// parent refreshes its rows. Bulk modes are hosted by the same dialog via
// props.assets (asset ids to sweep) or props.fleet.
import { computed, ref, watch } from 'vue'
import { AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogOverlay, AlertDialogPortal, AlertDialogRoot, AlertDialogTitle } from 'reka-ui'
import { ackAllAsset, ackAllFleet } from '../api'
import { useRuntime, type AlertRec } from '../stores/runtime'
import { toast } from '../stores/toast'
import { stateMeta } from '../theme/meta'
import { localizeConditionText } from '../copy'

const props = defineProps<{
  alert: AlertRec | null
  /** bulk: ack these asset ids (subject filter applies at the API) */
  assets?: string[]
  /** bulk: fleet-wide sweep */
  fleet?: boolean
  /** bulk: only acknowledge this subject (warn/critical/offline/stale) */
  subject?: string
}>()

const emit = defineEmits<{ (e: 'close'): void; (e: 'confirmed'): void }>()

const rt = useRuntime()

const open = computed(() => props.alert !== null || !!props.assets?.length || !!props.fleet)

const isBulk = computed(() => !props.alert)
const bulkName = computed(() =>
  props.fleet ? '全场' : (props.assets?.length === 1
    ? (rt.assetById(props.assets[0])?.name || props.assets[0])
    : `${props.assets?.length || 0} 台设备`))

// -------------------------------------------------------------- note input --
const note = ref('')
const sending = ref(false)
const NOTE_MAX = 2000

const noteError = computed(() => {
  const n = note.value.trim()
  if (n.length > NOTE_MAX) return `备注不能超过 ${NOTE_MAX} 字`
  if (n.length > 0 && n.length < 1) return '备注至少 1 个字符（留空则不记反馈）'
  return ''
})

// reset the note each time the target changes (dialog reopen semantics)
watch(() => props.alert, () => { note.value = '' })
watch(() => [props.assets, props.fleet], () => { note.value = '' })

// --------------------------------------------------------------- rendering --
function subjectKey(a: AlertRec): string {
  const s = a.subject || a.state || ''
  return s === 'crit' ? 'critical' : s
}
function levelColor(a: AlertRec): string {
  const s = subjectKey(a)
  return (stateMeta as Record<string, { color: string }>)[s]?.color
    || (stateMeta as Record<string, { color: string }>)[a.overall_level || '']?.color
    || (stateMeta as Record<string, { color: string }>).unknown.color
}
function levelLabel(a: AlertRec): string {
  const s = subjectKey(a)
  return (stateMeta as Record<string, { label: string }>)[s]?.label || s || '告警'
}
function displayHeadline(a: AlertRec): string {
  return localizeConditionText(a.headline || a.reason)
}
function fmtTs(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// ------------------------------------------------------------------- ack --
async function confirm(): Promise<void> {
  if (sending.value) return
  sending.value = true
  try {
    const n = note.value.trim()
    if (props.alert) {
      await rt.acknowledge(props.alert.asset_id, props.alert.ts, n)
      toast(n ? '已确认并记入反馈' : '已确认')
    } else if (props.fleet) {
      // honor the active severity filter — a fleet sweep must never silently
      // widen beyond what the operator is looking at
      const r = await ackAllFleet(n, props.subject || null)
      toast(`全场已确认 ${r?.acked ?? 0} 条${n ? '，备注已记入反馈' : ''}`)
    } else {
      let total = 0
      for (const id of props.assets || []) {
        const r = await ackAllAsset(id, props.subject || null, n)
        total += r?.acked ?? 0
      }
      toast(`已批量确认 ${total} 条${n ? '，备注已记入反馈' : ''}`)
    }
    emit('confirmed')
    emit('close')
  } catch (e) {
    toast(e instanceof Error ? `确认失败：${e.message}` : '确认失败', 'crit')
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <AlertDialogRoot :open="open" @update:open="(v: boolean) => { if (!v) emit('close') }">
    <AlertDialogPortal>
      <AlertDialogOverlay class="ad-ov" />
      <AlertDialogContent class="ad" :aria-describedby="undefined">
        <AlertDialogTitle class="ad-title">
          {{ isBulk ? `批量确认 — ${bulkName}` : '确认告警' }}
        </AlertDialogTitle>
        <AlertDialogDescription class="ad-desc">
          {{ isBulk
            ? '按当前筛选批量确认；确认后不再重复提醒。'
            : '确认后该告警不再重复提醒；处理备注将进入学习反馈。' }}
        </AlertDialogDescription>

        <!-- single-ack: the alert's own details -->
        <div v-if="alert" class="ad-detail" :style="{ borderLeftColor: levelColor(alert) }">
          <div class="ad-row">
            <span class="ad-k">级别</span>
            <span class="ad-v" :style="{ color: levelColor(alert) }">{{ levelLabel(alert) }}</span>
            <span class="ad-k">资产</span>
            <span class="ad-v mono">{{ rt.assetById(alert.asset_id)?.name || alert.asset_id }}</span>
          </div>
          <div class="ad-row">
            <span class="ad-k">首次时间</span>
            <span class="ad-v mono">{{ fmtTs(alert.ts) }}</span>
          </div>
          <p class="ad-headline">{{ displayHeadline(alert) || '—' }}</p>
          <p v-if="alert.reason && alert.headline" class="ad-reason">{{ alert.reason }}</p>
        </div>

        <label class="ad-note-label">
          <span class="ad-k">处理备注 <span class="ad-opt">（选填，≤2000 字，记入学习反馈）</span></span>
          <textarea
            v-model="note"
            class="ad-note"
            rows="3"
            :maxlength="NOTE_MAX + 50"
            placeholder="例如：振动 4.3 实为联轴器松动，已现场处理 — 不误报"
          />
          <span class="ad-note-foot mono" :class="{ 'ad-note-err': noteError }">
            {{ noteError || `${note.trim().length} / ${NOTE_MAX}` }}
          </span>
        </label>

        <div class="ad-actions">
          <AlertDialogCancel as-child>
            <button type="button" class="ad-btn" :disabled="sending" @click="emit('close')">取消</button>
          </AlertDialogCancel>
          <button
            type="button"
            class="ad-btn ad-btn-primary"
            :disabled="sending || !!noteError"
            @click="confirm"
          >{{ sending ? '确认中…' : '确认' }}</button>
        </div>
      </AlertDialogContent>
    </AlertDialogPortal>
  </AlertDialogRoot>
</template>

<style scoped>
.ad { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 480px; max-width: calc(100vw - 32px); background: var(--bg1);
  border: 1px solid var(--line-2); border-radius: var(--r-m); padding: 16px;
  z-index: 80; outline: none; display: flex; flex-direction: column; gap: 10px; }
.ad-title { margin: 0; font-size: 14px; font-weight: 600; color: var(--tx-hi); }
.ad-desc { margin: 0; font-size: 11.5px; color: var(--tx-dim); line-height: 1.5; }

.ad-detail { border-left: 2px solid var(--warn); background: var(--bg2);
  border-radius: var(--r-s); padding: 10px 12px; display: flex;
  flex-direction: column; gap: 6px; }
.ad-row { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.ad-k { font-size: 9.5px; letter-spacing: .1em; color: var(--tx-dim); }
.ad-opt { letter-spacing: 0; text-transform: none; }
.ad-v { font-size: 12px; color: var(--tx-hi); }
.ad-headline { margin: 2px 0 0; font-size: 12.5px; color: var(--tx-hi); line-height: 1.5; }
.ad-reason { margin: 0; font-size: 11px; color: var(--tx-dim); }

.ad-note-label { display: flex; flex-direction: column; gap: 5px; }
.ad-note { width: 100%; background: var(--bg2); border: 1px solid var(--line-2);
  border-radius: var(--r-s); color: var(--tx-hi); font-size: 12px;
  font-family: inherit; padding: 8px 10px; resize: vertical; min-height: 56px; }
.ad-note:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: 1px; }
.ad-note-foot { font-size: 10px; color: var(--tx-dim); }
.ad-note-err { color: var(--warn); }

.ad-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 2px; }
.ad-btn { border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 12px; padding: 7px 16px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; }
.ad-btn:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.ad-btn:disabled { opacity: .5; cursor: default; }
.ad-btn-primary { color: var(--cyan); }
.ad-btn-primary:hover:not(:disabled) { color: var(--cyan); }
</style>

<style>
/* overlay lives in the portal — flat dim, NO backdrop-filter (spec §1.1
   reserves blur for the QA drawer overlay only) */
.ad-ov { position: fixed; inset: 0; background: rgba(2, 5, 10, 0.6);
  z-index: 79; }
</style>
