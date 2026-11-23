// Q&A threads — one conversation per asset, persisted in localStorage so a
// reload (or an operator handing over the terminal) keeps the transcript.
//
// Contract notes (field names verified against the backend):
//  * POST /api/assets/{id}/ask answers with {content, used_provider,
//    used_role, degraded, elapsed_s, snapshot:{window_end,trust,status,
//    overall_level}} — the provenance row must come from `snapshot` (the data
//    the answer was actually built from), never from live telemetry.
//  * Refusals arrive as ok=false: `no_fresh_snapshot: …` (freshness gate, the
//    model was never called → 'gate_refused') vs `provider_failed: …` (the
//    model chain was called and failed → 'provider_failed'). They render
//    differently in QaPanel, so they must be stored as distinct kinds.
//
// Message shape per the rewrite plan (task-3 brief): role 'user'|'ai',
// optional ref {ts, trust, provider} and a kind ('answer' default).
// `trust` mirrors the backend's snapshot trust vocabulary; 'none' means the
// ref was not attached (old persisted threads / live fallback absent).

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { ask as askApi } from '../api'
import { toast } from './toast'

export type QaRole = 'user' | 'ai'
export type QaKind = 'answer' | 'gate_refused' | 'provider_failed'

export interface QaMsgRef {
  /** epoch ms of the snapshot the answer was built from (ISO window_end → ms). */
  ts: number
  /** snapshot trust vocabulary full|partial|low|none (UI maps to high/medium/low). */
  trust: string
  /** model provider that produced the answer. */
  provider: string
}

export interface QaMsg {
  role: QaRole
  text: string
  ref?: QaMsgRef
  kind?: QaKind
  /** client wall-clock ms — ordering + relative "x 分钟前" display. */
  ts?: number
}

export type QaThread = QaMsg[]

const STORAGE_KEY = 'ea_qa_threads'
/** one asset's transcript is bounded — a shift-long session must not bloat the tab */
const MAX_THREAD = 200
const MAX_ASSETS = 50

function loadThreads(): Record<string, QaThread> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, QaThread>) : {}
  } catch {
    // corrupted payload → start clean rather than crash the drawer
    return {}
  }
}

function persist(threads: Record<string, QaThread>) {
  try {
    // prune before writing: stale assets drop off, threads stay bounded
    const keys = Object.keys(threads)
    if (keys.length > MAX_ASSETS) {
      for (const k of keys.slice(0, keys.length - MAX_ASSETS)) delete threads[k]
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(threads))
  } catch {
    // quota/private-mode: transcript simply stays in memory for this session
  }
}

export const useQaStore = defineStore('qa', () => {
  const threads = ref<Record<string, QaThread>>(loadThreads())
  /** per-asset in-flight flag — guards double-submit (Enter twice). */
  const busyFor = ref<Record<string, boolean>>({})
  /** last gate-refusal code per asset, so QaPanel can offer "允许旧数据重答". */
  const lastGateRefusal = ref<Record<string, string>>({})

  // persist on every mutation (small payloads; single-terminal usage)
  watch(threads, (t) => persist(t), { deep: true })

  function messagesFor(assetId: string): QaMsg[] {
    return threads.value[assetId] || []
  }

  function clearThread(assetId: string): void {
    delete threads.value[assetId]
    delete lastGateRefusal.value[assetId]
  }

  function isBusy(assetId: string): boolean {
    return !!busyFor.value[assetId]
  }

  async function ask(assetId: string, question: string, allowStale: boolean): Promise<void> {
    const q = (question || '').trim()
    if (!assetId || !q || busyFor.value[assetId]) return
    if (!threads.value[assetId]) threads.value[assetId] = []
    threads.value[assetId].push({ role: 'user', text: q, ts: Date.now() })
    busyFor.value[assetId] = true
    try {
      const r = await askApi(assetId, q, allowStale)
      const snap = r?.snapshot
      threads.value[assetId].push({
        role: 'ai',
        text: r?.content || '(空回答)',
        kind: 'answer',
        ref: snap
          ? {
              ts: Date.parse(snap.window_end) || Date.now(),
              trust: snap.trust || 'none',
              provider: r.used_provider || '',
            }
          : undefined,
        ts: Date.now(),
      })
      delete lastGateRefusal.value[assetId]
    } catch (e: any) {
      const code = String(e?.message || e)
      // deterministic gate refusal (model never called) vs provider outage:
      // distinct kinds → distinct cards in QaPanel (spec §5.4)
      const isGate = code.includes('no_fresh_snapshot')
      const isProvider = code.includes('provider_failed')
      if (isGate) lastGateRefusal.value[assetId] = code
      threads.value[assetId].push({
        role: 'ai',
        text: isGate
          ? '无新鲜快照，已拒绝回答（未调用模型）。请先采集到新鲜数据。'
          : isProvider
            ? '模型服务不可用，请稍后重试。'
            : code,
        kind: isGate ? 'gate_refused' : isProvider ? 'provider_failed' : 'answer',
        ts: Date.now(),
      })
      // refusal cards are the UI (not a toast); only unexpected errors toast
      if (!isGate && !isProvider) toast(`问答失败：${code}`, 'warn')
    } finally {
      // keep each transcript bounded even across a long shift
      const t = threads.value[assetId]
      if (t && t.length > MAX_THREAD) threads.value[assetId] = t.slice(-MAX_THREAD)
      busyFor.value[assetId] = false
    }
  }

  return { threads, busyFor, lastGateRefusal, messagesFor, clearThread, isBusy, ask }
})
