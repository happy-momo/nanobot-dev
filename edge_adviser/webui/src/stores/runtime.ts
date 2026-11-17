// Central client state: asset registry + live telemetry + alerts.
// Kept small & reactive; SSE pushes update it in place so Dashboard, Monitor and
// Alerts all react to the same source of truth without separate polling.

import { defineStore } from 'pinia'
import { api, connectSSE, setToken, UnauthorizedError } from '../api/http'

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

export interface AssetCard {
  asset_id: string
  name: string
  kind: string
  description?: string
  window_s?: number
  sample_freq_s?: number
  metrics?: any[]
  downtime_windows?: any[]
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
  }
}

export interface AlertRec {
  asset_id: string
  state: string
  reason: string
  headline: string
  window_end: string
  ts: number
  acked: boolean
  dedup_key: string
}

interface RT {
  overviewCards: AssetCard[]
  live: Record<string, SnapshotPayload>
  alerts: AlertRec[]
  ready: boolean
  pinRequired: boolean
}

export const useRuntime = defineStore('runtime', {
  state: (): RT => ({
    overviewCards: [],
    live: {},
    alerts: [],
    ready: false,
    pinRequired: false,
  }),
  getters: {
    assets: (s) => s.overviewCards,
    assetById: (s) => (id: string) => s.overviewCards.find((c) => c.asset_id === id),
    liveFor: (s) => (id: string) => s.live[id] || null,
    overallAlertCount: (s) => s.alerts.filter((a) => !a.acked).length,
    alertCountFor: (s) => (id: string) =>
      s.alerts.filter((a) => !a.acked && a.asset_id === id).length,
  },
  actions: {
    async bootstrap(askPin: () => Promise<string | null>) {
      try {
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
              await this.loadOverview()
              await this.loadAlerts()
            } catch (err) {
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
    async loadOverview() {
      const d = await api.get<any>('/api/overview')
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
      this.alerts = []
      for (const c of cards) {
        try {
          const d = await api.get<AlertRec[]>(`/api/assets/${c.asset_id}/alerts`)
          this.alerts.push(...(d || []))
        } catch (e) {
          /* ignore per-asset failures */
        }
      }
    },
    startStreams() {
      connectSSE('/stream/telemetry', (kind, data) => {
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
      })
      connectSSE('/stream/alerts', (kind, data) => {
        if (kind === 'alert' && data && data.asset_id) {
          // dedup on client too (SSE is one-way; a late replay could double)
          const exists = this.alerts.some(
            (a) => a.dedup_key === data.dedup_key && !a.acked && a.asset_id === data.asset_id,
          )
          if (!exists) this.alerts.push(data)
        }
      })
    },
    async acknowledge(asset_id: string, ts: number) {
      await api.post(`/api/assets/${asset_id}/alerts/${ts}/ack`)
      const a = this.alerts.find((x) => x.asset_id === asset_id && x.ts === ts)
      if (a) a.acked = true
    },
    async sendFeedback(asset_id: string, note: string) {
      await api.post(`/api/assets/${asset_id}/feedback`, { note })
    },
    async runNow(asset_id: string) {
      const d = await api.post<any>(`/api/assets/${asset_id}/run-now`)
      return d?.status || null
    },
    async triggerReport(asset_id: string) {
      const d = await api.post<any>(`/api/assets/${asset_id}/report`)
      return d || null
    },
  },
})