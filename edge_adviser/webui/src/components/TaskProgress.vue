<script setup lang="ts">
// TaskProgress — the ONE task-stream component every background action shares
// (报告生成 / 运行学习; spec §6 fixes old bug B). Flow:
//   run() → { token } → connectSSE(`/stream/task/${token}`) → phase events
//   queued → start → done | failed, with a 600s watchdog → 'timeout'.
// ALL terminal states (done / failed / timeout) clear the button's loading
// state and close the stream — the old implementation left a button spinning
// forever whenever the phase event was lost (a drop between SSE reconnects, a
// failed close, a server restart). No terminal state is ever skipped here.
//
// One subtlety handled: the backend's event NAME is `task` (bus KIND_TASK),
// but a same-token replay on reconnect can re-deliver an EARLIER phase —
// phase monotonicity is enforced so a replayed `queued` cannot rewind a
// visible `start`.
//
// Event contract (server/router_actions.py): KIND_TASK publishes
//   {token, kind, phase: 'queued' | 'start' | 'done' | 'failed', error?}
// The SSE event NAME is `task` (kind), the data is that payload; connectSSE
// delivers it as onEvent('task', payload). The watchdog covers the stream
// never opening AND the phase event never landing, and 'done' fires even when
// the component unmounts mid-task (loading state dies with the instance).
import { computed, onBeforeUnmount, ref } from 'vue'
import { LoaderCircle } from '@lucide/vue'
import { connectSSE } from '../api/http'

const props = defineProps<{
  /** starts the action and resolves the task token (already unwrapped by api/) */
  run: () => Promise<{ token: string }>
  /** the button copy, e.g. 生成报告 / 运行学习 */
  label: string
}>()

const emit = defineEmits<{ done: [] }>()

type Phase = 'idle' | 'queued' | 'start' | 'done' | 'failed' | 'timeout'

// 600s watchdog — mirrors the backend's worst-case LLM timeout chain
const TIMEOUT_MS = 600_000

const phase = ref<Phase>('idle')
const phaseAt = ref('')           // wall clock the current phase landed at
const running = ref(false)
const errorText = ref('')

// the live SSE handle + watchdog; `let` (not refs) — never rendered directly
let closeStream: (() => void) | null = null
let watchdog: number | null = null

const PHASE_LABEL: Record<Phase, string> = {
  idle: '待机',
  queued: '已排队',
  start: '运行中',
  done: '完成',
  failed: '失败',
  timeout: '超时',
}

// phase monotonicity for SSE replays (a reconnect re-delivers the last event)
const PHASE_RANK: Record<string, number> = {
  idle: 0, queued: 1, start: 2, done: 3, failed: 3, timeout: 3,
}

const phaseColor = computed(() => {
  if (phase.value === 'failed') return 'var(--crit)'
  if (phase.value === 'timeout') return 'var(--warn)'
  if (phase.value === 'done') return 'var(--cyan)'
  if (phase.value === 'queued' || phase.value === 'start') return 'var(--cyan)'
  return 'var(--tx-dim)'
})

function clearWatch() {
  if (watchdog !== null) {
    window.clearTimeout(watchdog)
    watchdog = null
  }
}

/** close stream + watchdog. Safe to call twice; used on every exit path. */
function closeAll() {
  clearWatch()
  try {
    closeStream?.()
  } catch { /* already closed */ }
  closeStream = null
}

/**
 * Terminal handler — the ONLY path that clears `running` (the button's
 * loading). Called for done / failed / timeout / stream-death / unmount, so
 * "button stuck on 生成中…" is structurally impossible (bug B).
 */
function finishTerminal(): void {
  running.value = false
  closeAll()
}

/** reset the stream-watchdog on every progress event (phase or data update) */
function bumpWatch(): void {
  clearWatch()
  watchdog = window.setTimeout(() => {
    phase.value = 'timeout'
    phaseAt.value = now()
    finishTerminal()
  }, TIMEOUT_MS)
}

/** marks a terminal phase and emits `done` exactly once */
function settle(p: 'done' | 'failed' | 'timeout'): void {
  phase.value = p
  phaseAt.value = now()
  finishTerminal()
  emit('done')
}

function now(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function start(): Promise<void> {
  if (running.value) return
  running.value = true
  errorText.value = ''
  phase.value = 'queued'
  phaseAt.value = now()
  // watchdog starts BEFORE the request too: an unreachable backend (fetch
  // hangs) must still hit the timeout, not spin forever
  bumpWatch()
  try {
    const r = await props.run()
    const token = r?.token
    if (!token) {
      // backend acknowledged but produced no token → nothing to watch
      errorText.value = '任务未返回 token，无法跟踪进度'
      phase.value = 'failed'
      phaseAt.value = now()
      finishTerminal()
      return
    }
    closeStream = connectSSE(`/stream/task/${token}`, (kind, data) => {
      if (kind !== 'task' || !data || typeof data !== 'object') return
      const p = (data as { phase?: string; error?: string }).phase
      if (!p) return
      // replay guard: a later-or-equal phase only — a stale `queued` must not
      // rewind a visible `start` (SSE re-delivers the last event on reconnect)
      if ((PHASE_RANK[p] ?? 0) < (PHASE_RANK[phase.value] ?? 0)) return
      errorText.value = (data as { error?: string }).error || ''
      if (p === 'queued' || p === 'start') {
        bumpWatch()
        phase.value = p
        phaseAt.value = now()
      } else if (p === 'done') {
        settle('done')
      } else if (p === 'failed') {
        settle('failed')
      }
    }, () => {
      // status callback: SSE health. A dead stream is NOT treated as a
      // terminal failure — EventSource auto-reconnects; the 600s watchdog is
      // the honest upper bound for "the result never arrived".
      bumpWatch()
    })
  } catch (e) {
    // run() itself threw (gate refusal, network, provider down)
    errorText.value = e instanceof Error ? e.message : String(e)
    phase.value = 'failed'
    phaseAt.value = now()
    finishTerminal()
  }
}

onBeforeUnmount(() => {
  // leaving the tab mid-task: drop the stream and the watchdog. `running`
  // dies with the instance so nothing is left spinning (no leaks).
  closeAll()
})
</script>

<template>
  <div class="tp">
    <button
      type="button"
      class="btn tp-btn"
      :disabled="running"
      @click="start"
    >
      <LoaderCircle
        v-if="running"
        :size="14"
        :stroke-width="1.5"
        class="tp-spin"
        aria-hidden="true"
      />
      <span>{{ running ? `${label}中…` : label }}</span>
    </button>

    <!-- status line: machine phase in mono (facts stay mono, spec §1.2) -->
    <span class="tp-status mono" :style="{ color: phaseColor }" aria-live="polite">
      <span class="tp-dot" :class="{ 'tp-dot-live': running }" />
      <span class="tp-phase">{{ PHASE_LABEL[phase] }}</span>
      <span v-if="phaseAt" class="tp-at">{{ phaseAt }}</span>
      <span v-if="errorText && (phase === 'failed' || phase === 'timeout')" class="tp-err">
        {{ errorText }}
      </span>
    </span>
  </div>
</template>

<style scoped>
.tp { display: flex; align-items: center; gap: 10px; min-width: 0; flex-wrap: wrap; }

.btn {
  display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 12px; padding: 7px 14px; border-radius: var(--r-s); cursor: pointer;
  letter-spacing: .04em; min-height: 32px; white-space: nowrap;
}
.btn:hover:not(:disabled) { background: var(--bg3); border-color: var(--cyan-dim); color: var(--tx-hi); }
.btn:disabled { opacity: .6; cursor: default; }
.tp-spin { animation: tp-rot 1s linear infinite; }
@keyframes tp-rot { to { transform: rotate(360deg); } }

.tp-status { display: inline-flex; align-items: center; gap: 7px; font-size: 11px;
  letter-spacing: .05em; min-width: 0; }
.tp-phase { font-variant-numeric: tabular-nums; }
.tp-at { color: var(--tx-dim); font-size: 10px; }
.tp-err { color: var(--tx-dim); font-size: 10.5px; max-width: 320px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.tp-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor;
  flex-shrink: 0; opacity: .85; }
.tp-dot-live { animation: tp-pulse 1.6s ease-in-out infinite; }
@keyframes tp-pulse { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }

@media (prefers-reduced-motion: reduce) {
  .tp-spin, .tp-dot-live { animation: none !important; }
}
</style>
