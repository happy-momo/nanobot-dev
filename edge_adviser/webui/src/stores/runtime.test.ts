import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { api } from '../api/http'
import { useRuntime, type AlertRec, type AssetCard } from './runtime'

vi.mock('../api/http', () => ({
  api: { get: vi.fn() },
  connectSSE: vi.fn(() => () => {}),
  setToken: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}))

function card(assetId: string): AssetCard {
  return {
    asset_id: assetId,
    name: assetId,
    kind: 'machine',
    runtime: {
      state: 'unknown',
      trust: 'none',
      coverage: 0,
      overall_level: 'unknown',
      worst_metric_level: 'unknown',
      state_reason: '',
      sample_count: 0,
      window_end: '',
      headline: '',
      latest_values: {},
      alerts: 0,
    },
  }
}

function alert(assetId: string): AlertRec {
  return {
    asset_id: assetId,
    state: 'offline',
    subject: 'offline',
    reason: 'source unavailable',
    headline: '设备断连',
    window_end: '',
    ts: 1,
    acked: false,
    dedup_key: `${assetId}:offline`,
  }
}

describe('runtime alert loading', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.get).mockReset()
  })

  it('marks a partial asset failure instead of presenting a false empty state', async () => {
    const rt = useRuntime()
    rt.overviewCards = [card('pump1'), card('fan1')]
    vi.mocked(api.get)
      .mockResolvedValueOnce([alert('pump1')])
      .mockRejectedValueOnce(new Error('fan1 unavailable'))

    await rt.loadAlerts()

    expect(rt.alerts).toEqual([alert('pump1')])
    expect(rt.alertsPartialFailure).toBe(true)
  })

  it('clears the partial-failure state after a complete reload', async () => {
    const rt = useRuntime()
    rt.overviewCards = [card('pump1')]
    rt.alertsPartialFailure = true
    vi.mocked(api.get).mockResolvedValueOnce([])

    await rt.loadAlerts()

    expect(rt.alerts).toEqual([])
    expect(rt.alertsPartialFailure).toBe(false)
  })
})
