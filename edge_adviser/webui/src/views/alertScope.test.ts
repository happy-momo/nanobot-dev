import { describe, expect, it } from 'vitest'
import { buildBulkAckScope } from './alertScope'
import type { AlertRec } from '../stores/runtime'

function alert(assetId: string, subject: string, acked = false): AlertRec {
  return {
    asset_id: assetId,
    state: subject,
    subject,
    reason: '',
    headline: '',
    window_end: '',
    ts: 1,
    acked,
    dedup_key: `${assetId}:${subject}`,
  }
}

describe('bulk acknowledgement scope', () => {
  it('keeps the active severity in the server-side scope', () => {
    const scope = buildBulkAckScope([
      alert('pump1', 'offline'),
      alert('pump2', 'warn'),
      alert('pump3', 'offline', true),
    ], 'offline')

    expect(scope).toEqual({ assetIds: ['pump1'], subject: 'offline' })
  })
})
