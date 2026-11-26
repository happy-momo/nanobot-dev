<script setup lang="ts">
// TabKnowledge — the 知识 tab (spec §5.2): MEMORY.md chapter navigation
// (split on `## ` — same boundary the backend's memory_service.split_sections
// uses for claim indexing) + claims/conflicts ledger + learn-gate status +
// TaskProgress generation (label 运行学习, run: triggerLearn → token →
// /stream/task/{token}; done/failed/timeout all reset the button — bug B).
//
// The conflicts ledger is the HONESTY surface: a superseded claim renders in
// warn tones with 已被取代 so an outdated threshold is never silently trusted
// (the old page's danger tag carried the same rule). Content is AI-derived →
// the MEMORY body frame is violet; the ledger and gate panel stay factual.
import { computed, onMounted, ref, watch } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { fetchKnowledge, triggerLearn } from '../../api'
import { toast } from '../../stores/toast'
import ObsPanel from '../../components/ObsPanel.vue'
import EmptyState from '../../components/EmptyState.vue'
import MarkdownView from '../../components/MarkdownView.vue'
import TaskProgress from '../../components/TaskProgress.vue'

const props = defineProps<{ assetId: string }>()

// ------------------------------------------------------------------ shapes --
// Ledger records come from ONE producer spelling (v2, memory_service.py):
// claim = {id, heading, text, created_at, source_window, status}
// conflict = {id, heading, old, new, created_at}. The demo seed writes the
// same shape (0.6.0 schema unification) and the knowledge endpoint
// normalizes legacy v1 rows at the API boundary.
interface ClaimRec {
  id?: string
  heading?: string
  text?: string
  created_at?: string
  source_window?: string
  status?: string
}
interface ConflictRec {
  id?: string
  heading?: string
  old?: string
  new?: string
  created_at?: string
}
interface KnowledgeDoc {
  memory_md?: string
  claims?: ClaimRec[]
  conflicts?: ConflictRec[]
  learn_gate?: string
}

const data = ref<KnowledgeDoc | null>(null)
const loading = ref(false)
const loadError = ref('')
const activeChapter = ref(0)
let seq = 0   // stale-response guard: A→B switch must not paint A's memory

// ------------------------------------------------------------- chapter split --
// carried over from the old Knowledge.vue: split on `^## `, keep the original
// heading markdown so MarkdownView renders it, first segment (before any `##`)
// is the preamble chapter.
const chapters = computed<Array<{ title: string; body: string }>>(() => {
  const md: string = data.value?.memory_md || ''
  if (!md.trim()) return []
  const parts = md.split(/^## /m).filter((p) => p.trim())
  const out: Array<{ title: string; body: string }> = []
  for (const p of parts) {
    const nl = p.indexOf('\n')
    const title = (nl >= 0 ? p.slice(0, nl) : p).trim().replace(/^#\s*/, '') || '(untitled)'
    const body = (nl >= 0 ? p.slice(nl + 1) : '').trim()
    // keep the original heading markdown so MarkdownView renders it
    out.push({ title, body: '## ' + title + '\n\n' + body })
  }
  return out
})

const chapterBody = computed(() =>
  chapters.value[activeChapter.value]?.body || data.value?.memory_md || '')

// ---------------------------------------------------------------- ledger --
/** claim status → (label, tone). active=生效中 / stale=已过时 / superseded=已被取代 */
function claimStatus(s?: string): { label: string; tone: 'ok' | 'warn' | 'crit' | 'dim' } {
  if (s === 'active') return { label: '生效中', tone: 'ok' }
  if (s === 'stale') return { label: '已过时', tone: 'warn' }
  if (s === 'superseded') return { label: '已被取代', tone: 'crit' }
  return { label: s || '未知', tone: 'dim' }
}

const claimText = (c: ClaimRec): string => {
  // production `text` duplicates its heading prefix (memory_service writes
  // title + " " + body); trim it so the heading isn't shown twice. A bodyless
  // section trims to '' — fall through instead of rendering an empty row.
  if (c.text) {
    const body = c.heading && c.text.startsWith(c.heading + ' ')
      ? c.text.slice(c.heading.length + 1) : c.text
    if (body.trim()) return body
  }
  return c.heading || '(无正文)'
}
const claimWhen = (c: ClaimRec): string =>
  (c.created_at || '').slice(0, 19).replace('T', ' ')

/** learn gate: the config's gate mode + what it means for this asset */
const gate = computed(() => {
  const g = data.value?.learn_gate || ''
  if (g === 'off') return { label: 'OFF — 学习不做数据门禁', tone: 'warn' as const }
  if (g === 'strict') return { label: 'STRICT — 数据不足时拒绝学习', tone: 'crit' as const }
  if (g === 'soft') return { label: 'SOFT — 数据不足时降置信学习', tone: 'ok' as const }
  return { label: g ? `GATE ${g.toUpperCase()}` : '未配置', tone: 'dim' as const }
})

const conflicts = computed<ConflictRec[]>(() => data.value?.conflicts || [])
const claims = computed<ClaimRec[]>(() => data.value?.claims || [])

// ------------------------------------------------------------------ loads --
async function load(): Promise<void> {
  if (!props.assetId) return
  const mySeq = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const d = await fetchKnowledge(props.assetId)
    if (mySeq !== seq) return
    data.value = d
    activeChapter.value = 0
  } catch (e) {
    if (mySeq !== seq) return
    // failed read ≠ empty knowledge: show the error with a retry, not the
    // "run learning" CTA (which would waste a model call) — carried over
    loadError.value = e instanceof Error ? e.message : '知识库加载失败'
    data.value = null
  } finally {
    if (mySeq === seq) loading.value = false
  }
}

/** TaskProgress done (done/failed/timeout all land here) → re-read the memory:
 * a successful learn pass rewrote MEMORY.md; a failed one left it alone, and
 * the reload is cheap + honest either way. */
function onLearnDone(): void {
  load()
}

onMounted(() => { load() })
watch(() => props.assetId, (v, o) => {
  if (!v || v === o) return
  data.value = null
  loadError.value = ''
  load()
})
</script>

<template>
  <div class="tk">
    <!-- ─── left: MEMORY.md chapters ─────────────────────────────────────── -->
    <div class="tk-main">
      <!-- load failure: retry, NOT the learn CTA (a model call can't fix a 500) -->
      <ObsPanel v-if="loadError" title="知识记录">
        <EmptyState label="知识库加载失败" :hint="loadError" />
        <div class="tk-retry-row">
          <button type="button" class="tk-btn" @click="load">重试</button>
        </div>
      </ObsPanel>

      <!-- empty: learn CTA is legitimate here (nothing learned yet) -->
      <ObsPanel v-else-if="!loading && !(data?.memory_md || '').trim()" title="知识记录">
        <EmptyState
          label="该设备还没有学习知识"
          hint="运行学习后，模型会从历史快照中沉淀运行洞察、阈值校准与维护动作"
        />
        <div class="tk-learn-row">
          <TaskProgress label="运行学习" :run="() => triggerLearn(props.assetId)" @done="onLearnDone" />
        </div>
      </ObsPanel>

      <template v-else>
        <div class="tk-layout">
          <!-- chapter rail -->
          <aside class="tk-chapters">
            <div class="tk-ch-head">
              <span class="eyebrow">MEMORY.md</span>
              <button
                type="button"
                class="tk-reload"
                :disabled="loading"
                aria-label="刷新知识库"
                @click="load"
              >
                <RefreshCw :size="13" :stroke-width="1.5" :class="{ 'tk-spin': loading }" />
              </button>
            </div>
            <div class="tk-ch-list">
              <button
                v-for="(ch, i) in chapters"
                :key="i"
                type="button"
                class="tk-ch"
                :class="{ 'tk-ch-on': activeChapter === i }"
                :title="ch.title"
                @click="activeChapter = i"
              >{{ ch.title }}</button>
            </div>
          </aside>

          <!-- AI content (violet frame): the memory markdown itself -->
          <ObsPanel
            class="tk-mem-panel"
            title="章节正文"
            :meta="`${activeChapter + 1}/${chapters.length}`"
          >
            <template #actions>
              <span class="tk-ai-marker">AI</span>
            </template>
            <div v-if="loading" class="tk-skeleton" aria-hidden="true" />
            <div v-else class="tk-mem">
              <MarkdownView :source="chapterBody" />
            </div>
          </ObsPanel>
        </div>

        <!-- learn action under the memory panel -->
        <div class="tk-learn-bar">
          <TaskProgress label="运行学习" :run="() => triggerLearn(props.assetId)" @done="onLearnDone" />
        </div>
      </template>
    </div>

    <!-- ─── right: ledger + gate ─────────────────────────────────────────── -->
    <aside class="tk-side">
      <!-- conflicts ledger — warn tones, honest 已被取代 marks -->
      <ObsPanel title="冲突账本" :meta="conflicts.length ? `${conflicts.length} 条` : undefined">
        <div v-if="conflicts.length">
          <div v-for="(c, i) in conflicts" :key="`${c.id || ''}-${i}`" class="tk-conflict">
            <div class="tk-conflict-head">
              <span class="tk-tag tk-tag-superseded">已被取代</span>
              <span class="tk-conflict-when mono">
                {{ (c.created_at || '').slice(0, 19).replace('T', ' ') }}</span>
            </div>
            <p v-if="c.new && c.old" class="tk-conflict-text">
              旧: {{ c.old }} → 新: {{ c.new }}</p>
            <p v-else-if="c.new" class="tk-conflict-text">{{ c.new }}</p>
            <p v-else-if="c.old" class="tk-conflict-old">旧: {{ c.old }}</p>
            <p v-else class="tk-conflict-text">该声明与当前数据冲突</p>
          </div>
        </div>
        <EmptyState v-else label="无冲突记录" hint="学习过程未发现互相矛盾的声明" />
      </ObsPanel>

      <!-- claims ledger -->
      <ObsPanel title="知识声明" :meta="claims.length ? `${claims.length} 条` : undefined">
        <div v-if="claims.length" class="tk-claims">
          <div v-for="(c, i) in claims" :key="`${c.id || ''}-${i}`" class="tk-claim">
            <div class="tk-claim-head">
              <span
                class="tk-tag"
                :class="{
                  'tk-tag-ok': claimStatus(c.status).tone === 'ok',
                  'tk-tag-warn': claimStatus(c.status).tone === 'warn',
                  'tk-tag-superseded': claimStatus(c.status).tone === 'crit',
                }"
              >{{ claimStatus(c.status).label }}</span>
              <span v-if="c.source_window" class="tk-claim-title">{{ c.source_window === 'feedback' ? '来自反馈' : '来自学习' }}</span>
              <span v-else-if="c.heading" class="tk-claim-title">{{ c.heading }}</span>
            </div>
            <p class="tk-claim-text">{{ claimText(c) }}</p>
            <p v-if="claimWhen(c)" class="tk-claim-when mono">{{ claimWhen(c) }}</p>
          </div>
        </div>
        <EmptyState v-else label="暂无声明记录" hint="运行学习后会按章节登记声明" />
      </ObsPanel>

      <!-- learn gate status -->
      <ObsPanel title="学习门禁">
        <p class="tk-gate" :class="{ 'tk-gate-dim': gate.tone === 'dim' }">{{ gate.label }}</p>
        <p class="tk-gate-note">门禁决定学习是否采信当前快照：数据不足时 SOFT 降置信、STRICT 拒绝。</p>
      </ObsPanel>
    </aside>
  </div>
</template>

<style scoped>
.tk {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(260px, 1fr);
  gap: 14px;
  align-items: start;
}
.tk-main { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.tk-side { display: flex; flex-direction: column; gap: 14px; min-width: 0; }

.tk-layout { display: flex; flex-direction: column; gap: 0; }

/* ── chapter rail ── */
.tk-chapters { border: 1px solid var(--line); border-bottom: 0; background: var(--bg1);
  border-radius: var(--r-m) var(--r-m) 0 0; overflow: hidden; }
.tk-ch-head { display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; border-bottom: 1px solid var(--line); }
.tk-reload { display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border: 1px solid var(--line-2); background: var(--bg2);
  color: var(--tx); border-radius: var(--r-s); cursor: pointer; }
.tk-reload:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.tk-reload:disabled { opacity: .55; cursor: default; }
.tk-spin { animation: tk-rot 1s linear infinite; }
@keyframes tk-rot { to { transform: rotate(360deg); } }

.tk-ch-list { display: flex; overflow-x: auto; gap: 2px; padding: 6px 8px; }
.tk-ch {
  border: 0; background: none; color: var(--tx-dim); font-size: 11.5px;
  padding: 6px 12px; border-radius: var(--r-s); cursor: pointer; white-space: nowrap;
  min-height: 32px; max-width: 220px; overflow: hidden; text-overflow: ellipsis;
}
.tk-ch:hover { background: var(--bg2); color: var(--tx); }
.tk-ch-on { background: var(--bg2); color: var(--cyan); }

/* ── memory body (AI content → violet frame) ── */
.tk-mem-panel { border-radius: 0 0 var(--r-m) var(--r-m); }
.tk-mem-panel :deep(.panel-frame), .tk-mem-panel.panel-frame {
  border-color: color-mix(in srgb, var(--violet) 35%, var(--line)); }
.tk-mem-panel :deep(.op-head) { border-bottom-color: color-mix(in srgb, var(--violet) 25%, var(--line)); }
.tk-ai-marker { color: var(--violet); font-weight: 700; letter-spacing: .14em;
  font-size: 10px; border: 1px solid var(--violet); border-radius: 2px; padding: 0 4px; }
.tk-mem { padding: 12px 16px 16px; max-height: 58vh; overflow-y: auto; }
.tk-skeleton { height: 320px; background: var(--bg2);
  background-image: linear-gradient(var(--bg3) 12%, transparent 12%);
  background-size: 100% 34px; }

/* learn bar */
.tk-learn-bar { border: 1px solid var(--line); background: var(--bg1);
  border-radius: var(--r-m); padding: 10px 12px;
  display: flex; align-items: center; justify-content: flex-end; }
.tk-learn-row { display: flex; justify-content: center; padding-bottom: 16px; }
.tk-retry-row { display: flex; justify-content: center; padding-bottom: 16px; }
.tk-btn { border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11.5px; padding: 5px 14px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; }
.tk-btn:hover { border-color: var(--cyan-dim); color: var(--tx-hi); }

/* ── ledger rows ── */
.tk-conflict { padding: 10px 14px; border-bottom: 1px solid var(--line); position: relative; }
.tk-conflict:last-child { border-bottom: 0; }
.tk-conflict::before { content: ''; position: absolute; left: 0; top: 8px; bottom: 8px;
  width: 2px; background: var(--warn); }
.tk-conflict-head { display: flex; align-items: center; gap: 8px; }
.tk-conflict-when { margin-left: auto; font-size: 10px; color: var(--tx-dim); }
.tk-conflict-text, .tk-conflict-old, .tk-conflict-new {
  margin: 5px 0 0; font-size: 11.5px; line-height: 1.55; color: var(--tx); }
.tk-conflict-old { color: var(--tx-dim); }
.tk-conflict-new { color: var(--tx-hi); }

/* status tags — semantic tones only (ok/warn/crit), no violet in the ledger */
.tk-tag {
  display: inline-flex; align-items: center; font-size: 9.5px; font-weight: 700;
  letter-spacing: .08em; border-radius: 2px; padding: 1px 6px;
  border: 1px solid var(--line-2); color: var(--tx-dim); flex-shrink: 0;
}
.tk-tag-ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
.tk-tag-warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
.tk-tag-superseded { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 55%, transparent);
  background: color-mix(in srgb, var(--warn) 12%, transparent); }

.tk-claims { max-height: 42vh; overflow-y: auto; }
.tk-claim { padding: 10px 14px; border-bottom: 1px solid var(--line); }
.tk-claim:last-child { border-bottom: 0; }
.tk-claim-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tk-claim-title { font-size: 11px; color: var(--tx-hi); letter-spacing: .03em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tk-claim-text { margin: 5px 0 0; font-size: 11.5px; color: var(--tx); line-height: 1.55; }
.tk-claim-when { margin: 3px 0 0; font-size: 9.5px; color: var(--tx-dim); }

/* gate */
.tk-gate { margin: 0; padding: 10px 14px 0; font-size: 12px; color: var(--cyan);
  letter-spacing: .04em; }
.tk-gate-dim { color: var(--tx-dim); }
.tk-gate-note { margin: 4px 14px 10px; font-size: 10.5px; color: var(--tx-dim); line-height: 1.5; }

/* breakpoint: detail-tab side-rail collapse (1099.98px — asset tabs only) */
@media (max-width: 1099.98px) {
  .tk { grid-template-columns: 1fr; }
}
</style>
