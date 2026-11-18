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
  acked_at?: string | null
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
      const by = (st: string) => cards.filter((c) => c.runtime.state === st).length
      const cov = cards.length
        ? cards.reduce((acc, c) => acc + (c.runtime.coverage || 0), 0) / cards.length
        : 0
      return {
        total: cards.length,
        healthy: by('healthy'),
        warn: by('warn'),
        critical: by('critical'),
        degraded: by('offline') + by('stale') + by('unknown'),
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
              const r = await api.post<any>('/api/auth/pin', { pin })
              const tok = r?.data?.token
              setToken(tok || '')
              this.pinRequired = false
              await this.loadVersion()
              await this.loadOverview()
              await this.loadAlerts()
            } catch {
              this.pinRequired = true
            }
          }
        } else {
          throw e
        }
      }
      this.ready = true
      this.startStreams()
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
          metrics: rt.series?.length ? undefined : undefined,
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
          }
        }
      }, (st: SSEStatus) => {
        this.conn = st === 'open' ? 'online' : st === 'connecting' ? 'connecting' : 'reconnecting'
      })

      const closeAlerts = connectSSE('/stream/alerts', (kind, data) => {
        stamp()
        if (kind === 'alert' && data && data.asset_id) {
          // dedup on client too (SSE is one-way; a late replay could double)
          const exists = this.alerts.some(
            (a) => a.dedup_key === data.dedup_key && !a.acked && a.asset_id === data.asset_id,
          )
          if (!exists) this.alerts.unshift(data)
        }
      }, (st: SSEStatus) => {
        this.conn = st === 'open' ? 'online' : st === 'connecting' ? 'connecting' : 'reconnecting'
      })

      this._closers = [closeTel, closeAlerts]
    },
    stopStreams() {
      for (const c of this._closers || []) {
        try { c() } catch { /* already closed */ }
      }
      this._closers = []
    },
    async loadHistory(assetId: string, limit = 40, includeMetrics = true) {
      const d = await api.get<SnapshotRow[]>(
        `/api/assets/${assetId}/snapshots?limit=${limit}&include_metrics=${includeMetrics ? 1 : 0}`)
      this.history[assetId] = d || []
      return this.history[assetId]
    },
    async acknowledge(asset_id: string, ts: number) {
      await api.post(`/api/assets/${asset_id}/alerts/${ts}/ack`)
      const a = this.alerts.find((x) => x.asset_id === asset_id && x.ts === ts)
      if (a) {
        a.acked = true
        a.acked_at = new Date().toISOString()
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
