<script setup lang="ts">
// QaPanel — the ONE QA surface shared by the drawer (App shell, 420px) and the
// fullscreen /qa route (spec §5.4). Owns: asset context bar (Reka Select when
// no assetId prop), the per-asset thread, the input row (Enter send /
// Shift+Enter newline) and the allow_stale override switch (input border turns
// warn while degraded mode is on).
//
// Three message states (spec §5.4 — the two refusals MUST look different):
//  * answer        → violet left bar + violet eyebrow "AI 解读" + mono
//                    provenance line `依据快照 HH:mm · trust N% · provider`
//                    (trust/% come from the snapshot the answer was BUILT
//                    from — stores/qa.ts already extracts it).
//  * gate_refused  → gray deterministic card + lock icon + human reason
//                    ("无新鲜快照（N 分钟未更新）…") + 允许旧数据重答 button.
//  * provider_failed → warn-bordered card, distinct copy "模型服务不可用…".
// The store (stores/qa.ts) classifies kinds from the backend error codes;
// this component only renders them. Threads persist per asset (localStorage).
import { computed, nextTick, ref, watch } from 'vue'
import { Lock, RefreshCw, SendHorizonal } from '@lucide/vue'
import {
  SelectContent, SelectItem, SelectItemText, SelectRoot, SelectTrigger,
  SelectValue,
} from 'reka-ui'
import { useRuntime, type AssetCard } from '../stores/runtime'
import { useQaStore, type QaMsg } from '../stores/qa'
import { trustMeta } from '../theme/meta'
import MarkdownView from './MarkdownView.vue'
import EmptyState from './EmptyState.vue'

const props = withDefaults(defineProps<{
  assetId?: string
  fullscreen?: boolean
}>(), { fullscreen: false })

const rt = useRuntime()
const qa = useQaStore()

// ----------------------------------------------------------- asset context --
// The drawer passes the current asset (fixed context); fullscreen offers the
// Reka Select. Both paths read the same store thread per asset id.
const assets = computed<AssetCard[]>(() => rt.assets)
const internalAssetId = ref(props.assetId || assets.value[0]?.asset_id || '')
const assetId = computed<string>(() => props.assetId || internalAssetId.value)
watch(() => props.assetId, (v) => { if (v) internalAssetId.value = v })

const card = computed<AssetCard | null>(() => rt.assetById(assetId.value) || null)
const assetName = computed(() => card.value?.name || assetId.value)

// ------------------------------------------------------------------ thread --
const messages = computed<QaMsg[]>(() =>
  assetId.value ? qa.messagesFor(assetId.value) : [])
const busy = computed(() => !!assetId.value && qa.isBusy(assetId.value))
const gateRefusal = computed(() => !!assetId.value && !!qa.lastGateRefusal[assetId.value])

const listEl = ref<HTMLElement | null>(null)
function scrollToEnd(): void {
  nextTick(() => listEl.value?.scrollTo({ top: listEl.value.scrollHeight }))
}
watch([messages, () => messages.value.length], scrollToEnd, { deep: true })

// -------------------------------------------------------------- allow_stale --
const allowStale = ref(false)

// ------------------------------------------------------------------ input --
const input = ref('')

function onEnter(e: KeyboardEvent): void {
  if (e.shiftKey) return            // Shift+Enter → newline (textarea default)
  e.preventDefault()
  send()
}

function send(q?: string): void {
  const question = (q ?? input.value).trim()
  if (!assetId.value || !question || busy.value) return
  input.value = ''
  qa.ask(assetId.value, question, allowStale.value)
}

/** gate refusal "允许旧数据重答": retry the user's last unanswered question
 * with allow_stale forced on (the deterministic refusal already identified it) */
function retryAllowStale(): void {
  const t = messages.value
  for (let i = t.length - 1; i >= 0; i--) {
    if (t[i].role === 'user') {
      allowStale.value = true
      send(t[i].text)
      return
    }
  }
}

function clearThread(): void {
  if (assetId.value) qa.clearThread(assetId.value)
}

// ------------------------------------------------------------- provenance --
/** "HH:mm" from the snapshot the answer was built from (epoch ms ref) */
function snapClock(ts?: number): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

/** trust vocabulary full|partial|low|none → design-system % (meta.ts labels) */
function trustLabel(trust: string): string {
  const key = trust === 'full' ? 'high' : trust === 'partial' ? 'medium' : 'low'
  return trustMeta[key].label
}
function trustColor(trust: string): string {
  const key = trust === 'full' ? 'high' : trust === 'partial' ? 'medium' : 'low'
  return trustMeta[key].color
}

/** message wall-clock "x 分钟前" — mono suffix on every bubble */
function ago(ts?: number): string {
  if (!ts) return ''
  // consume the store's 1s reactive clock so ages tick (bare Date.now() would
  // freeze until some other reactive dep happens to re-render this row)
  void rt.nowSec
  const s = Math.max(0, Math.round((rt.nowSec * 1000 - ts) / 1000))
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  return `${Math.round(s / 3600)} 小时前`
}

function kindOf(m: QaMsg): string {
  return m.kind || 'answer'
}
</script>

<template>
  <div class="qp" :class="{ 'qp-fs': fullscreen }">
    <!-- ─── asset context bar ────────────────────────────────────────────── -->
    <div class="qp-ctx">
      <span class="qp-ctx-k">资产</span>
      <!-- drawer: fixed asset passed by the shell; fullscreen: selectable -->
      <span v-if="props.assetId" class="qp-ctx-name mono">{{ assetName }}</span>
      <SelectRoot v-else v-model="internalAssetId">
        <SelectTrigger class="qp-select" aria-label="选择资产">
          <SelectValue :placeholder="assetId || '选择资产'" />
        </SelectTrigger>
        <SelectContent class="qp-select-pop" position="popper" :side-offset="4">
          <SelectItem
            v-for="a in assets" :key="a.asset_id"
            :value="a.asset_id" class="qp-select-item"
          >
            <SelectItemText>{{ a.name }}</SelectItemText>
          </SelectItem>
        </SelectContent>
      </SelectRoot>

      <button
        v-if="messages.length"
        type="button"
        class="qp-clear"
        aria-label="清空当前会话"
        @click="clearThread"
      >清空</button>
    </div>

    <!-- ─── thread ───────────────────────────────────────────────────────── -->
    <div ref="listEl" class="qp-thread">
      <div v-if="!messages.length" class="qp-empty">
        <EmptyState
          label="设备问答"
          hint="回答依据设备数据；数据过旧时会提示你确认后再继续"
        />
      </div>

      <div
        v-for="(m, i) in messages"
        :key="i"
        class="qp-msg"
        :class="`qp-msg-${m.role}`"
      >
        <!-- user: right-aligned plain bubble -->
        <div v-if="m.role === 'user'" class="qp-bubble-user">
          <span class="qp-text">{{ m.text }}</span>
        </div>

        <!-- AI: three-state rendering -->
        <div v-else class="qp-ai" :class="`qp-ai-${kindOf(m)}`">
          <!-- answer: violet bar + eyebrow + markdown + mono provenance -->
          <template v-if="kindOf(m) === 'answer'">
            <div class="qp-eyebrow">模型回答</div>
            <div class="qp-answer-body">
              <MarkdownView :source="m.text" />
            </div>
            <div v-if="m.ref" class="qp-prov mono">
              <span>依据快照 {{ snapClock(m.ref.ts) }}</span>
              <span :style="{ color: trustColor(m.ref.trust) }">可信度 {{ trustLabel(m.ref.trust) }}</span>
              <span>{{ m.ref.provider || '—' }}</span>
            </div>
          </template>

          <!-- gate_refused: GRAY deterministic card + lock + human reason -->
          <template v-else-if="kindOf(m) === 'gate_refused'">
            <div class="qp-refuse-head">
              <Lock :size="14" :stroke-width="1.5" />
              <span>门禁拒绝</span>
            </div>
            <p class="qp-refuse-text">{{ m.text }}</p>
            <button
              type="button"
              class="qp-retry"
              :disabled="busy"
              @click="retryAllowStale"
            >确认并使用较旧数据</button>
          </template>

          <!-- provider_failed: WARN-bordered card, distinct copy -->
          <template v-else>
            <div class="qp-refuse-head qp-refuse-head-warn">
              <RefreshCw :size="14" :stroke-width="1.5" />
              <span>回答服务暂不可用</span>
            </div>
            <p class="qp-refuse-text">{{ m.text }}</p>
          </template>

          <span v-if="m.ts" class="qp-when mono">{{ ago(m.ts) }}</span>
        </div>
      </div>

      <div v-if="busy" class="qp-msg qp-msg-ai">
        <div class="qp-ai qp-ai-thinking">
          <span class="qp-eyebrow">模型回答</span>
          <span class="qp-thinking-text">正在生成回答…</span>
        </div>
      </div>
    </div>

    <!-- ─── input row ────────────────────────────────────────────────────── -->
    <div class="qp-input-row" :class="{ 'qp-input-warn': allowStale }">
      <textarea
        v-model="input"
        class="qp-input"
        rows="2"
        :placeholder="allowStale
          ? '将使用较旧数据回答，结果可能不准确'
          : '输入问题，Enter 发送 / Shift+Enter 换行'"
        :disabled="busy || !assetId"
        @keydown.enter="onEnter"
      />
      <button
        type="button"
        class="qp-send"
        :disabled="busy || !input.trim() || !assetId"
        aria-label="发送问题"
        @click="send()"
      >
        <SendHorizonal :size="15" :stroke-width="1.5" />
      </button>
    </div>

    <div class="qp-opts">
      <label class="qp-stale">
        <input v-model="allowStale" type="checkbox" class="qp-check">
        <span>允许使用较旧数据回答</span>
      </label>
      <span v-if="gateRefusal" class="qp-gate-hint">上次问题被门禁拒绝</span>
    </div>
  </div>
</template>

<style scoped>
.qp { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.qp-fs { max-width: 860px; margin: 0 auto; width: 100%; }

/* ── asset context bar ── */
.qp-ctx { display: flex; align-items: center; gap: 10px; padding: 0 0 10px;
  border-bottom: 1px solid var(--line); flex-shrink: 0; }
.qp-ctx-k { font-size: 9.5px; letter-spacing: .1em; color: var(--tx-dim); }
.qp-ctx-name { font-size: 12px; color: var(--tx-hi); }
.qp-select { display: inline-flex; align-items: center; gap: 8px;
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx-hi);
  font-size: 12px; padding: 6px 12px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; min-width: 160px; }
.qp-select:hover { border-color: var(--cyan-dim); }
.qp-select:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: 1px; }
.qp-select-pop { border: 1px solid var(--line-2); background: var(--bg1);
  border-radius: var(--r-s); z-index: 70; min-width: var(--reka-select-trigger-width); }
.qp-select-item { padding: 7px 12px; font-size: 12px; color: var(--tx);
  cursor: pointer; }
.qp-select-item[data-highlighted] { background: var(--bg2); color: var(--cyan); }
.qp-clear { margin-left: auto; border: 0; background: none; color: var(--tx-dim);
  font-size: 11px; cursor: pointer; padding: 4px 8px; min-height: 32px; }
.qp-clear:hover { color: var(--tx-hi); }

/* ── thread ── */
.qp-thread { flex: 1; min-height: 0; overflow-y: auto; display: flex;
  flex-direction: column; gap: 14px; padding: 12px 2px; }
.qp-empty { margin: auto 0; }

.qp-msg { display: flex; flex-direction: column; }
.qp-msg-user { align-items: flex-end; }
.qp-bubble-user { max-width: 82%; background: var(--bg2);
  border: 1px solid var(--line-2); border-radius: var(--r-m);
  padding: 8px 12px; }
.qp-text { font-size: 12.5px; color: var(--tx-hi); line-height: 1.6;
  white-space: pre-wrap; overflow-wrap: anywhere; }

/* AI block: LEFT 2px violet bar (violet = AI voice, spec §1.1) */
.qp-ai { position: relative; max-width: 92%; padding: 8px 12px 6px 14px;
  background: var(--bg1); border: 1px solid var(--line);
  border-left: 2px solid var(--violet); border-radius: var(--r-m); }
.qp-eyebrow { font-size: 9px; letter-spacing: 2px; text-transform: uppercase;
  color: var(--violet); font-weight: 600; margin-bottom: 6px;
  font-family: var(--font-mono); }
.qp-answer-body { min-width: 0; }
.qp-prov { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px;
  padding-top: 6px; border-top: 1px solid var(--line); font-size: 10px;
  color: var(--tx-dim); font-variant-numeric: tabular-nums; }

/* gate_refused: GRAY deterministic card — NO violet bar, no AI voice */
.qp-ai-gate_refused { border-left: 2px solid var(--line-2); background: var(--bg2); }
.qp-refuse-head { display: flex; align-items: center; gap: 7px; font-size: 10px;
  letter-spacing: .08em; color: var(--tx-dim); margin-bottom: 6px; }
.qp-refuse-text { margin: 0; font-size: 12px; color: var(--tx); line-height: 1.6; }
.qp-retry { margin-top: 9px; border: 1px solid var(--line-2);
  background: var(--bg1); color: var(--tx); font-size: 11.5px;
  padding: 5px 13px; border-radius: var(--r-s); cursor: pointer; min-height: 32px; }
.qp-retry:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.qp-retry:disabled { opacity: .5; cursor: default; }

/* provider_failed: WARN-bordered — visually distinct from the gray gate card */
.qp-ai-provider_failed { border-left: 2px solid var(--warn);
  border-color: color-mix(in srgb, var(--warn) 45%, var(--line));
  background: color-mix(in srgb, var(--warn) 6%, var(--bg1)); }
.qp-refuse-head-warn { color: var(--warn); }

/* thinking */
.qp-ai-thinking { display: flex; align-items: center; gap: 10px; }
.qp-thinking-text { font-size: 12px; color: var(--tx-dim); }

.qp-when { margin-top: 6px; font-size: 9.5px; color: var(--tx-dim); }

/* ── input row: warn border while allow_stale (degraded-mode affordance) ── */
.qp-input-row { display: flex; align-items: flex-end; gap: 8px; flex-shrink: 0;
  border: 1px solid var(--line-2); border-radius: var(--r-m); background: var(--bg2);
  padding: 8px; transition: border-color .15s; }
.qp-input-warn { border-color: var(--warn); }
.qp-input { flex: 1; border: 0; background: none; color: var(--tx-hi);
  font-size: 12.5px; font-family: inherit; resize: none; line-height: 1.5;
  min-height: 36px; outline: none; }
.qp-input::placeholder { color: var(--tx-dim); }
.qp-input:disabled { opacity: .55; }
.qp-send { display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; flex-shrink: 0; border: 1px solid var(--cyan-dim);
  background: var(--bg1); color: var(--cyan); border-radius: var(--r-s);
  cursor: pointer; }
.qp-send:hover:not(:disabled) { background: var(--bg3); }
.qp-send:disabled { opacity: .45; cursor: default; }

.qp-opts { display: flex; align-items: center; gap: 12px; padding-top: 8px;
  flex-shrink: 0; }
.qp-stale { display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
  font-size: 11px; color: var(--tx-dim); }
.qp-check { accent-color: var(--warn); width: 14px; height: 14px; }
.qp-gate-hint { font-size: 10px; color: var(--warn); }
</style>
