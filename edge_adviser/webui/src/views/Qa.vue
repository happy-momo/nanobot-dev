<script setup lang="ts">
// Chat-style Q&A. Key UX rules inherited from the backend's freshness gate:
//  * a question answered without a fresh snapshot is REFUSED (ok=false) and
//    shown as a distinct gate-refusal card — never passed to the model to guess;
//  * network/server failures show a distinct error card (not a fake refusal);
//  * the trust badge reflects the asset's real snapshot trust, not a constant;
//  * threads are per-asset so switching devices doesn't interleave answers.
import { ref, computed, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import { trustMeta, relativeTime } from '../theme'
import TrustBadge from '../components/TrustBadge.vue'
import MarkdownView from '../components/MarkdownView.vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

interface Msg {
  role: 'user' | 'assistant'
  text: string
  meta?: any
  kind?: 'refused' | 'error'
  ts?: number
}

const rt = useRuntime()

const assetId = ref('')
const threads = ref<Record<string, Msg[]>>({})
const input = ref('')
const busy = ref(false)
const listEl = ref<HTMLElement>()
const copied = ref(false)
// operator override: answer from the latest (possibly stale) snapshot instead
// of refusing — surfaced as an explicit toggle so the default stays strict
const allowStale = ref(false)

const messages = computed<Msg[]>(() => threads.value[assetId.value] || [])

// suggested questions per asset kind
const suggestions = computed(() => {
  const card = rt.assetById(assetId.value)
  const base = ['当前运行状态如何？', '有哪些指标接近报警阈值？', '总结最近的异常事件']
  if (card?.kind === 'pump') base.push('振动偏高时建议什么维护动作？')
  if (card?.kind === 'motor') base.push('温度趋势是否正常？')
  return base
})

const trustNow = computed(() =>
  rt.liveFor(assetId.value)?.trust || rt.assetById(assetId.value)?.runtime.trust || 'none')

async function send(q?: string) {
  const question = (q ?? input.value).trim()
  if (!question || busy.value) return
  if (!assetId.value) {
    if (rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
    else { ElMessage.warning('尚无可用设备'); return }
  }
  const id = assetId.value
  if (!threads.value[id]) threads.value[id] = []
  threads.value[id].push({ role: 'user', text: question, ts: Date.now() })
  if (q === undefined) input.value = ''
  busy.value = true
  scroll()
  try {
    const r = await api.post<any>(`/api/assets/${id}/ask`, { question, allow_stale: allowStale.value })
    if (r && r.content) {
      threads.value[id].push({
        role: 'assistant', text: r.content,
        // trust from the snapshot the answer was ACTUALLY built from (backend
        // echoes snapshot_meta); fall back to live telemetry only when absent
        meta: { ...r, trust: r.snapshot?.trust || rt.liveFor(id)?.trust
          || rt.assetById(id)?.runtime.trust || 'none' },
        ts: Date.now(),
      })
    } else if (r && r.error) {
      threads.value[id].push({
        role: 'assistant', text: r.error, kind: 'refused', ts: Date.now(),
      })
    } else {
      threads.value[id].push({
        role: 'assistant', text: '(空回答)', kind: 'error', ts: Date.now(),
      })
    }
  } catch (e: any) {
    // distinguish the deterministic gate refusal from a model outage
    const msg = String(e?.message || e)
    const isGate = msg.includes('no_fresh_snapshot')
    const isProvider = msg.includes('provider_failed')
    threads.value[id].push({
      role: 'assistant',
      text: isGate
        ? '无新鲜快照，已拒绝回答（未调用模型）。请先采集到新鲜数据。'
        : isProvider
          ? '模型服务不可用（已调用但全部 provider 失败）。请检查模型配置或网络。'
          : msg,
      kind: isGate ? 'refused' : 'error',
      ts: Date.now(),
    })
  } finally {
    busy.value = false
    scroll()
  }
}

function isRefused(m: Msg) { return m.kind === 'refused' }
function isError(m: Msg) { return m.kind === 'error' }

async function copyAnswer(m: Msg) {
  try {
    await navigator.clipboard.writeText(m.text)
    copied.value = true
    ElMessage.success('已复制')
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    ElMessage.warning('复制失败')
  }
}

function scroll() {
  nextTick(() => { listEl.value?.scrollTo({ top: listEl.value.scrollHeight }) })
}

watch(assetId, scroll)

function onEnter(e: KeyboardEvent) {
  if (e.shiftKey) return  // Shift+Enter → newline (default behavior)
  e.preventDefault()
  send()
}
</script>

<template>
  <div class="page qa-page">
    <PageHeader title="智能问答" subtitle="答案基于确定性快照；无新鲜数据时系统会明确拒绝而非猜测">
      <el-select v-model="assetId" style="width:220px" placeholder="选择设备">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
    </PageHeader>

    <div ref="listEl" class="thread">
      <div v-if="!messages.length" class="thread-empty">
        <EmptyState icon="💬" text="输入设备状态问题"
                    hint="例如「这台泵当前风险如何？建议什么维护动作？」" />
        <div class="sugs" v-if="assetId">
          <el-button v-for="s in suggestions" :key="s" size="small" round
                     class="sug" @click="send(s)">{{ s }}</el-button>
        </div>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble" :class="{ refused: isRefused(m), error: isError(m) }">
          <!-- model answers render as markdown; refusals/errors stay plain -->
          <MarkdownView v-if="m.role === 'assistant' && !isRefused(m) && !isError(m)"
                        :source="m.text" class="md-answer" />
          <div v-else class="text">{{ m.text }}</div>

          <div v-if="m.meta && !isRefused(m) && !isError(m)" class="meta-row muted">
            <TrustBadge :trust="m.meta.trust || trustNow" />
            <span v-if="m.meta.snapshot?.window_end"
                  :title="'答案所依据快照时间'">基于 {{ relativeTime(new Date(m.meta.snapshot.window_end).getTime()) }}</span>
            <span>⚙ provider={{ m.meta.used_provider || '—' }}</span>
            <el-tag :type="m.meta.degraded ? 'warning' : 'success'" size="small">
              {{ m.meta.degraded ? '已降级' : '本地/原模型' }}
            </el-tag>
            <el-button link size="small" class="copy" @click="copyAnswer(m)">复制</el-button>
          </div>
          <div v-if="isRefused(m)" class="refused-note">
            ⛔ 门禁拒绝 — 未调用模型。无新鲜快照时不猜测。
          </div>
          <div v-if="m.ts" class="when muted">{{ relativeTime(m.ts) }}</div>
        </div>
      </div>
      <div v-if="busy" class="msg assistant"><div class="bubble muted">思考中…</div></div>
    </div>

    <div class="input-row">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        resize="none"
        placeholder="输入问题，Enter 发送 / Shift+Enter 换行"
        @keydown.enter="onEnter"
        :disabled="busy"
      />
      <el-button type="primary" @click="send()" :loading="busy">发送</el-button>
    </div>
    <div class="opts-row">
      <el-tooltip content="默认只基于新鲜快照回答；开启后无新鲜数据时改用最近快照（答案会标注其时间与可信度）"
                  placement="top">
        <el-checkbox v-model="allowStale">允许使用旧数据（无新鲜快照时不拒绝）</el-checkbox>
      </el-tooltip>
    </div>
  </div>
</template>

<style scoped>
.qa-page { display: flex; flex-direction: column; height: calc(100vh - 130px); }
.thread {
  flex: 1; overflow-y: auto; padding: 8px 4px;
  display: flex; flex-direction: column; gap: 12px;
}
.thread-empty { display: flex; flex-direction: column; align-items: center; gap: 14px; padding-top: 30px; }
.sugs { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; }
.sug { color: var(--ea-text-muted); }
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.msg.assistant { justify-content: flex-start; }
.bubble {
  max-width: 78%; background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: 12px; padding: 10px 14px; line-height: 1.6;
}
.msg.user .bubble {
  background: var(--ea-accent-soft); color: var(--ea-text); border-color: var(--ea-accent);
  white-space: pre-wrap;
}
.bubble.refused { border-color: rgba(244,85,79,0.5); background: rgba(244,85,79,0.06); }
.bubble.error { border-color: rgba(245,177,76,0.5); background: rgba(245,177,76,0.06); }
.md-answer { margin: 0; }
.meta-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; flex-wrap: wrap; }
.copy { margin-left: auto; }
.refused-note { margin-top: 8px; color: #f4554f; font-size: 12px; }
.when { font-size: 11px; margin-top: 4px; }
.input-row { display: flex; gap: 8px; margin-top: 12px; align-items: flex-end; }
.input-row .el-button { height: 54px; }
.opts-row { margin-top: 6px; }
</style>
