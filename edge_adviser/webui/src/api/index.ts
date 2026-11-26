// Endpoint catalog — one named function per backend route. The old
// implementation scattered inline `api.get/post/patch` calls across views; this
// module is now the single place that knows the URL + payload/field contract.
// Field names were verified against the old view sources (git 69259d21^
// tree: views/Alerts.vue, Assets.vue, Qa.vue, Runlog.vue, Reports.vue,
// Knowledge.vue, Feedback.vue, Settings.vue, AssetHub.vue) and against the
// backend routers (server/router_*.py). Envelope unwrapping stays in http.ts.
//
// All functions return the unwrapped `data` (or null/undefined per backend);
// failures throw ApiError/UnauthorizedError (401 handling in http.ts).

import { api, connectSSE, downloadFile, downloadJsonFile } from './http'

// ---- shared payload/response types (field names == backend contract) ---- //

/** warn/crit alarm bands for one metric (PATCH /metrics/{key}/thresholds). */
export interface ThresholdBands {
  warn_min?: number | null
  warn_max?: number | null
  crit_min?: number | null
  crit_max?: number | null
}

/** QA answer provenance — snapshot the answer was actually built from. */
export interface QaSnapshotRef {
  window_end: string
  trust: string
  status: string
  overall_level: string
}

/** Response of POST /api/assets/{id}/ask (server/routes …/router_actions.py). */
export interface QaAnswer {
  content: string
  used_provider: string
  used_role: string | null
  degraded: boolean
  elapsed_s: number
  snapshot: QaSnapshotRef | null
}

/** Response of POST /api/assets/{id}/report | /learn | /run-now: a task token
 * the client watches on /stream/task/{token} (queued→start→done/failed). */
export interface TaskToken {
  token: string
  [k: string]: any
}

// ---- overview / meta ---- //

/** Sparkline series embedding: 0 disables it, N embeds the last N snapshots. */
export const fetchOverview = (history = 8) =>
  api.get<any>(`/api/overview?history=${history}`)

export const fetchVersion = () => api.get<any>('/api/version')

export const fetchConfig = () => api.get<any>('/api/config')
export const fetchConfiguration = () => api.get<ConfigurationView>('/api/configuration')

export interface ConfigurationMutationResult {
  asset_id: string
  warnings: string[]
}

export interface ConnectorSource {
  kind: 'simulator' | 'rest' | 'mqtt' | 'opcua' | 'modbus'
  url?: string
  topic?: string
  poll_interval_s?: number
  credential_id?: string
  params?: Record<string, unknown>
}

export interface AssetConfiguration {
  asset_id: string
  name: string
  kind: string
  description: string
  sample_freq_s: number
  window_s: number
  enabled: boolean
  metrics: Array<Record<string, unknown>>
  source: ConnectorSource | null
  credential_configured?: boolean
  runtime?: Record<string, unknown>
}

export interface ConfigurationView {
  assets: AssetConfiguration[]
  credential_store_available: boolean
  protocols: Record<string, 'ready' | 'planned'>
}

export interface AssetConfigurationInput extends Omit<AssetConfiguration, 'credential_configured' | 'runtime' | 'source'> {
  source: ConnectorSource
}

export interface AssetBackup {
  format: string
  version: number
  config_revision: string
  asset: Record<string, unknown>
  source: ConnectorSource | null
  credentials_included: false
}

export interface ConnectorTestResult {
  ok: boolean
  runs: Array<{ run: number; ok: boolean; sample_count?: number; error?: string; issues?: string[] }>
  total_samples: number
  issues: string[]
  sample_preview: Array<Record<string, unknown>>
}

export type ConnectorCredentialsInput =
  | { headers: Record<string, string> }
  | { username: string; password: string; private_key_password?: string }
  | { private_key_password: string }

/** Scheduler liveness (status ok|degraded + heartbeat age + per-asset data). */
export const fetchHealth = () => api.get<any>('/api/health')

/** Fleet run-observability (model success rate, MTTA, disk, per-asset counts). */
export const fetchStats = () => api.get<any>('/api/stats')

// ---- assets ---- //

/** Card wall: per-asset runtime (state/trust/coverage/latest_values/alerts). */
export const fetchAssets = () => api.get<any>('/api/assets')
export const createAssetConfiguration = (config: AssetConfigurationInput) =>
  api.post<ConfigurationMutationResult>('/api/assets', config)
export const updateAssetConfiguration = (id: string, config: AssetConfigurationInput) =>
  api.put<ConfigurationMutationResult>(`/api/assets/${encodeURIComponent(id)}/configuration`, config)
export const deleteAssetConfiguration = (id: string, revision: string) =>
  api.delete<ConfigurationMutationResult & { history_preserved: boolean }>(
    `/api/assets/${encodeURIComponent(id)}/configuration`, { expected_revision: revision })
export const downloadAssetBackup = (id: string) =>
  downloadJsonFile<AssetBackup>(
    `/api/assets/${encodeURIComponent(id)}/backup.json`,
    `${id}-backup.json`,
  )
export const putConnectorCredentials = (id: string, credentials: ConnectorCredentialsInput) =>
  api.put<{ credential_id: string; configured: boolean }>(
    `/api/connector-credentials/${encodeURIComponent(id)}`, credentials)
export const deleteConnectorCredentials = (id: string) =>
  api.delete<{ deleted: boolean }>(`/api/connector-credentials/${encodeURIComponent(id)}`)
export const testConnector = (config: AssetConfigurationInput) =>
  api.post<ConnectorTestResult>('/api/connectors/test', config)

/** Full asset record + asset_md + downtime_windows + source_kind. */
export const fetchAsset = (id: string) => api.get<any>(`/api/assets/${id}`)

/** Historical snapshots, oldest-first. includeMetrics → per-metric arrays. */
export const fetchSnapshots = (id: string, limit = 40, includeMetrics = true) =>
  api.get<any>(
    `/api/assets/${id}/snapshots?limit=${limit}&include_metrics=${includeMetrics ? 1 : 0}`)

// ---- reports / knowledge ---- //

/** Report list rows: {date, degraded, provider, meta, has_report}. */
export const fetchReports = (id: string) =>
  api.get<any>(`/api/assets/${id}/reports`)

/** One day's report: {date, report_md, meta, snapshot, degraded, provider}. */
export const fetchReport = (id: string, date: string) =>
  api.get<any>(`/api/assets/${id}/reports/${date}`)

export const fetchKnowledge = (id: string) =>
  api.get<any>(`/api/assets/${id}/knowledge`)

// ---- audit / alerts / feedback ---- //

/** Audit rows {rows, next_before, total}; `before` = rows already consumed
 * from the newest end (offset cursor — stable for audit browsing). */
export const fetchRunlog = (id: string, before?: number, limit = 300) => {
  let p = `/api/assets/${id}/runlog?limit=${limit}`
  if (before !== undefined) p += `&before=${encodeURIComponent(String(before))}`
  return api.get<any>(p)
}

/** Alert records; includeAcked=true keeps the ack history after a reload. */
export const fetchAlerts = (id: string, includeAcked = false) =>
  api.get<any>(`/api/assets/${id}/alerts?include_acked=${includeAcked ? 1 : 0}`)

// ---- actions ---- //

/** Synchronous manual sample run: returns the snapshot (+ task token). */
export const runNow = (id: string) =>
  api.post<any>(`/api/assets/${id}/run-now`)

/** Background report generation → TaskToken (watch /stream/task/{token}). */
export const triggerReport = (id: string) =>
  api.post<any>(`/api/assets/${id}/report`)

/** Background learn pass → TaskToken (watch /stream/task/{token}). */
export const triggerLearn = (id: string) =>
  api.post<any>(`/api/assets/${id}/learn`)

/** Ask the QA engine. Refusals arrive as a thrown ApiError whose message is
 * the backend error code (`no_fresh_snapshot: …` / `provider_failed: …`). */
export const ask = (id: string, question: string, allowStale = false) =>
  api.post<QaAnswer>(`/api/assets/${id}/ask`,
    { question, allow_stale: allowStale })

/** Acknowledge one alert (epoch-seconds ts); note feeds the learn loop. */
export const ackAlert = (id: string, ts: number, note = '') =>
  api.post<any>(`/api/assets/${id}/alerts/${ts}/ack`, { ts, note })

/** Bulk-ack one asset, optionally scoped to an alert subject (warn/critical/offline/stale). */
export const ackAllAsset = (id: string, subject?: string | null, note = '') =>
  api.post<any>(`/api/assets/${id}/alerts/ack-all`, { subject: subject || null, note })

/** Fleet-wide sweep across every asset (post-outage batch). */
export const ackAllFleet = (note = '', subject?: string | null) =>
  api.post<any>('/api/alerts/ack-all', { subject: subject || null, note })

/** Update one metric's alarm bands → {changed: {field: [old, new]}, metric}. */
export const patchThresholds = (id: string, metricKey: string, bands: ThresholdBands) =>
  api.patch<any>(`/api/assets/${id}/metrics/${metricKey}/thresholds`, bands)

/** Operator feedback note — recorded and fed into the learn loop. */
export const sendFeedback = (id: string, note: string) =>
  api.post<any>(`/api/assets/${id}/feedback`, { note })

// ---- downloads / task streams ---- //

/** Authenticated report download (bearer token rides the fetch headers).
 *  'html' is the standalone print/handoff export (stdlib-rendered backend). */
export const downloadReport = (id: string, date: string, kind: 'md' | 'json' | 'html') =>
  downloadFile(`/api/assets/${id}/reports/${date}/download.${kind}`,
    `${id}_${date}.${kind}`)

/** Authenticated audit CSV export. */
export const downloadRunlog = (id: string) =>
  downloadFile(`/api/assets/${id}/runlog.csv`, `${id}-runlog.csv`)

/** SSE task stream for report/learn/run-now tokens (phase queued→done/failed). */
export const connectTaskStream = (token: string, onEvent: (kind: string, data: any) => void) =>
  connectSSE(`/stream/task/${token}`, onEvent)
