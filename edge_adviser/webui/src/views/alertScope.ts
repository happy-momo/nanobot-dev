import type { AlertRec } from '../stores/runtime'

export interface BulkAckScope {
  assetIds: string[]
  subject: string | null
}

export function buildBulkAckScope(
  alerts: readonly AlertRec[],
  severity: string,
): BulkAckScope {
  const normalizeSubject = (alert: AlertRec) => {
    const subject = alert.subject || alert.state || ''
    return subject === 'crit' ? 'critical' : subject
  }
  const assetIds = [...new Set(
    alerts
      .filter((alert) => !alert.acked)
      .filter((alert) => !severity || normalizeSubject(alert) === severity)
      .map((alert) => alert.asset_id),
  )]
  return { assetIds, subject: severity || null }
}
