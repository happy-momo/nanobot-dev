// Central client state: asset registry + live telemetry + alerts.
// Kept small & reactive; SSE pushes update it in place so Dashboard, Monitor and
// Alerts all react to the same source of truth without separate polling.

import { defineStore } from 'pinia'
import { api, connectSSE, setToken, UnauthorizedError, SSEStatus } from '../api/http'

export interface MetricLive {
  key: string
  unit: string
  n: number
  mean?: number | null
  min?: number | null
  max?: number | null
  p95?: number | null
  level?: string
  slope?: number | null
  over_warn_runs?: number
  over_crit_runs?: number
}

export interface SnapshotPayload {
  asset_id: string
  status: string
  trust: string
  coverage: number
  overall_level: string
  headline: string
  window_end: string
  sample_count: number
  metrics?: MetricLive[]
}

export interface SparkPoint {
  window_end: string
  value: number | null
  level: string
}

export interface MetricSpec {
  key: string
  unit: string
  description?: string
  norm_min?: number | null
  norm_max?: number | null
  warn_min?: number | null
  warn_max?: number | null
  crit_min?: number | null
  crit_max?: number | null
  persist?: number
  min_samples?: number
}

export interface AssetCard {
  asset_id: string
  name: string
  kind: string
  description?: string
  window_s?: number
  sample_freq_s?: number
  metrics?: MetricSpec[]
  downtime_windows?: any[]
  config?: {
    metrics?: MetricSpec[]
    downtime_windows?: any[]
    [k: string]: any
  }
  runtime: {
    state: string
    trust: string
    coverage: number
    overall_level: string
    worst_metric_level: string
    state_reason: string
    sample_count: number
    window_end: string
    headline: string
    latest_values: Record<string, number | null>
    alerts: number
    series?: SparkPoint[]
  }
}

export interface AlertRec {
  asset_id: string
  state: string
  subject?: string
  reason: string
  overall_level?: string
  headline: string
  window_end: string
  ts: number
  acked: boolean
  acked_at?: string | number | null  // epoch SECONDS (backend) — formatTs handles both
  dedup_key: string
}

export interface SnapshotRow {
  filename: string
  window_end: string
  status: string
  trust: string
  coverage: number
  overall_level: string
  sample_count: number
  headline: string
  metrics?: MetricLive[]
}

export type Conn = 'connecting' | 'online' | 'reconnecting' | 'offline'

export interface FleetKpi {
  total: number
  healthy: number
  warn: number
  critical: number
  degraded: number  // offline/stale/unknown
  unackedAlerts: number
  avgCoverage: number
}

interface RT {
  overviewCards: AssetCard[]
  live: Record<string, SnapshotPayload>
  alerts: AlertRec[]
  ready: boolean
  pinRequired: boolean
  conn: Conn
  lastEventAt: number
  history: Record<string, SnapshotRow[]>
  demo: boolean
  version: string
  dataFreshnessS: number
  _closers: Array<() => void>
  _streamStatus: Record<string, SSEStatus>
  loadError: string
  /** reactive wall clock (seconds) — bumped every second so relative ages
   * ("42 秒前") advance without waiting for an SSE event */
  nowSec: number
  _clockTimer: number
  /** one-shot flag so both streams opening doesn't double-resync */
  _resyncDirty: boolean
}

export const useRuntime = defineStore('runtime', {
  state: (): RT => ({
    overviewCards: [],
    live: {},
    alerts: [],
    ready: false,
    pinRequired: false,
    conn: 'connecting',
    lastEventAt: 0,
    history: {},
    demo: false,
    version: '',
    dataFreshnessS: 3600,
    _closers: [],
    _streamStatus: {},
    loadError: '',
    nowSec: Math.floor(Date.now() / 1000),
    _clockTimer: 0,
    _resyncDirty: false,
  }),
  getters: {
    assets: (s) => s.overviewCards,
    assetById: (s) => (id: string) => s.overviewCards.find((c) => c.asset_id === id),
    liveFor: (s) => (id: string) => s.live[id] || null,
    overallAlertCount: (s) => s.alerts.filter((a) => !a.acked).length,
    alertCountFor: (s) => (id: string) =>
      s.alerts.filter((a) => !a.acked && a.asset_id === id).length,
    ackedAlerts: (s) => s.alerts.filter((a) => a.acked),
    unackedAlerts: (s) => s.alerts.filter((a) => !a.acked),
    fleetKpi: (s): FleetKpi => {
      const cards = s.overviewCards
      // warn/critical are ALARM levels (overall_level), not machine states —
      // AssetState has no 'warn'/'critical' members, so counting by state made
      // these KPIs permanently zero while real alarms burned (P0 fix).
      const byLevel = (lv: string) =>
        cards.filter((c) => (c.runtime.overall_level || '') === lv).length
      const byState = (st: string) =>
        cards.filter((c) => (c.runtime.state || '') === st).length
      const cov = cards.length
        ? cards.reduce((acc, c) => acc + (c.runtime.coverage || 0), 0) / cards.length
        : 0
      return {
        total: cards.length,
        healthy: byState('healthy') + byState('partial'),
        warn: byLevel('warn'),
        critical: byLevel('critical'),
        degraded: byState('offline') + byState('stale') + byState('unknown') +
          byState('insufficient'),
        unackedAlerts: s.alerts.filter((a) => !a.acked).length,
        avgCoverage: cov,
      }
    },
    /** Metric specs for an asset — prefer live snapshot metrics order, fall back to config. */
    metricsOf(): (id: string) => MetricSpec[] {
      return (id: string) => {
        const card = this.assetById(id)
        return card?.config?.metrics || card?.metrics || []
      }
    },
  },
  actions: {
    async bootstrap(askPin: () => Promise<string | null>) {
      this.loadError = ''
      try {
        await this.loadVersion()
        await this.loadOverview()
        await this.loadAlerts()
        this.pinRequired = false
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          this.pinRequired = true
          const pin = await askPin()
          if (pin) {
            try {
              // POST /api/auth/pin → envelope {ok, data:{enabled, token}};
              // http.ts unwraps the envelope, so the token rides on `r.token`.
              // The old `r?.data?.token` read undefined off the already-unwrapped
              // payload → setToken('') → every later call 401'd and the user was
              // stuck re-prompting forever (carry-forward fix, Task 3 review).
              const r = await api.post<{ enabled?: boolean; token?: string }>(
                '/api/auth/pin', { pin })
              setToken(r?.token || '')
              this.pinRequired = false
              await this.loadVersion()
              await this.loadOverview()
              await this.loadAlerts()
            } catch {
              this.pinRequired = true
            }
          }
        } else {
          // Server unreachable / unexpected failure: surface it instead of
          // leaving the app on a skeleton screen forever. The UI shows the
          // message with a retry; the streams still start so a recovering
          // backend repopulates live data.
          this.loadError = e instanceof Error ? e.message : String(e)
        }
      }
      this.ready = true
      this.startStreams()
    },
    async retryBootstrap() {
      await this.bootstrap(async () => null)
      await this.loadOverview(true)
    },
    async loadVersion() {
      try {
        const d = await api.get<any>('/api/version')
        this.demo = !!d?.demo
        this.version = d?.version || ''
      } catch {
        /* version is informational */
      }
      try {
        const c = await api.get<any>('/api/config')
        this.dataFreshnessS = c?.data_freshness_s || 3600
      } catch {
        /* keep default */
      }
    },
    async loadOverview(withHistory = false) {
      const d = await api.get<any>(
        withHistory ? '/api/overview?history=8' : '/api/overview')
      this.overviewCards = d?.cards || []
      // backfill live from latest snapshot per card
      for (const c of this.overviewCards) {
        const rt = c.runtime
        this.live[c.asset_id] = {
          asset_id: c.asset_id,
          status: rt.state,
          trust: rt.trust,
          coverage: rt.coverage,
          overall_level: rt.overall_level,
          headline: rt.headline,
          window_end: rt.window_end,
          sample_count: rt.sample_count,
        }
      }
    },
    async loadAlerts() {
      const cards = this.overviewCards
      // parallel + include acked so the history survives a reload
      const results = await Promise.allSettled(
        cards.map((c) =>
          api.get<AlertRec[]>(`/api/assets/${c.asset_id}/alerts?include_acked=true`)))
      const all: AlertRec[] = []
      for (const r of results) {
        if (r.status === 'fulfilled' && Array.isArray(r.value)) all.push(...r.value)
      }
      all.sort((a, b) => b.ts - a.ts)
      this.alerts = all
    },
    startStreams() {
      // idempotent: callers may re-enter after a PIN round-trip
      this.stopStreams()
      const stamp = () => { this.lastEventAt = Date.now() }

      // reactive wall clock: relative ages must advance on their own, not only
      // when an SSE event happens to trigger a re-render (P0 fix #38)
      this.nowSec = Math.floor(Date.now() / 1000)
      this._clockTimer = window.setInterval(() => {
        this.nowSec = Math.floor(Date.now() / 1000)
      }, 1000)

      // Each stream reports its own health into _streamStatus; `conn` is the
      // aggregate. Writing it directly from two callbacks made the indicator
      // flap (one stream open + one reconnecting → status flipped on each event).
      // A stream that (re)opens after a drop triggers a full resync — the server
      // replay cache may have been reset (restart), so anything that changed
      // while we were disconnected would otherwise drift forever.
      const setStatus = (name: string, st: SSEStatus) => {
        this._streamStatus[name] = st
        const states = Object.values(this._streamStatus)
        if (states.includes('open')) this.conn = 'online'
        else if (states.includes('connecting')) this.conn = 'connecting'
        else this.conn = 'reconnecting'
        if (st === 'open') {
          // one shared resync per tick; both streams open on startup
          this._resyncDirty = true
          window.setTimeout(() => {
            if (!this._resyncDirty) return
            this._resyncDirty = false
            this.loadOverview(true).catch(() => { /* next reconnect retries */ })
            this.loadAlerts().catch(() => { /* next reconnect retries */ })
          }, 500)
        }
      }

      const closeTel = connectSSE('/stream/telemetry', (kind, data) => {
        stamp()
        if (kind === 'snapshot' && data && data.asset_id) {
          this.live[data.asset_id] = { ...this.live[data.asset_id], ...data }
          const card = this.assetById(data.asset_id)
          if (card) {
            card.runtime.state = data.status
            card.runtime.trust = data.trust
            card.runtime.coverage = data.coverage
            card.runtime.overall_level = data.overall_level
            card.runtime.window_end = data.window_end
            card.runtime.sample_count = data.sample_count
            card.runtime.headline = data.headline
            // keep the card's numbers alive too: latest metric values, trend
            // thumbnail and per-asset alert badge otherwise freeze at page-load
            // values even while the timestamp says "刚刚" (P0 fix #37)
            if (Array.isArray(data.metrics) && data.metrics.length) {
              const lv: Record<string, number | null> = {}
              for (const m of data.metrics) lv[m.key] = m.max ?? m.mean ?? null
              card.runtime.latest_values = lv
              // append to the sparkline series (dedup by window_end)
              const series = card.runtime.series || []
              const first = data.metrics[0]
              const pt = { window_end: data.window_end,
                value: first.max ?? first.mean ?? null,
                level: first.level || 'info' }
              const i = series.findIndex((p) => p.window_end === pt.window_end)
              if (i >= 0) series.splice(i, 1)
              series.push(pt)
              card.runtime.series = series.slice(-8)
            }
          }
        }
      }, (st: SSEStatus) => setStatus('telemetry', st))

      const closeAlerts = connectSSE('/stream/alerts', (kind, data) => {
        stamp()
        if (kind === 'alert' && data && data.asset_id) {
          // dedup on client too (SSE is one-way; a late replay could double)
          const exists = this.alerts.some(
            (a) => a.dedup_key === data.dedup_key && !a.acked && a.asset_id === data.asset_id,
          )
          if (!exists) {
            this.alerts.unshift(data)
            const card = this.assetById(data.asset_id)
            if (card) card.runtime.alerts = (card.runtime.alerts || 0) + 1
          }
        }
      }, (st: SSEStatus) => setStatus('alerts', st))

      this._closers = [closeTel, closeAlerts]
    },
    stopStreams() {
      for (const c of this._closers || []) {
        try { c() } catch { /* already closed */ }
      }
      this._closers = []
      if (this._clockTimer) {
        window.clearInterval(this._clockTimer)
        this._clockTimer = 0
      }
      this._streamStatus = {}
    },
    async loadHistory(assetId: string, limit = 40, includeMetrics = true) {
      const d = await api.get<SnapshotRow[]>(
        `/api/assets/${assetId}/snapshots?limit=${limit}&include_metrics=${includeMetrics ? 1 : 0}`)
      this.history[assetId] = d || []
      return this.history[assetId]
    },
    async acknowledge(asset_id: string, ts: number, note = '') {
      await api.post(`/api/assets/${asset_id}/alerts/${ts}/ack`, { ts, note })
      const a = this.alerts.find((x) => x.asset_id === asset_id && x.ts === ts)
      if (a) {
        a.acked = true
        // epoch SECONDS to match the backend's persisted acked_at (alerts.py
        // writes time.time()); an ISO string here made the acked-sort compare
        // 1.7e3 against 1.7e9 (P1 fix)
        a.acked_at = Math.floor(Date.now() / 1000)
        if (note) (a as any).ack_note = note
      }
    },
    async sendFeedback(asset_id: string, note: string) {
      await api.post(`/api/assets/${asset_id}/feedback`, { note })
    },
    async runNow(asset_id: string) {
      const d = await api.post<any>(`/api/assets/${asset_id}/run-now`)
      return d || null
    },
    async triggerReport(asset_id: string) {
      const d = await api.post<any>(`/api/assets/${asset_id}/report`)
      return d || null
    },
    async triggerLearn(asset_id: string) {
      const d = await api.post<any>(`/api/assets/${asset_id}/learn`)
      return d || null
    },
  },
})
