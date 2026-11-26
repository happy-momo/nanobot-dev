import { describe, expect, it } from 'vitest'
import { fmtBytes, fmtS, fmtPct, fmtTs, fmtIso } from './fmt'

describe('fmtBytes', () => {
  it('formats byte scales', () => {
    expect(fmtBytes(500)).toBe('500B')
    expect(fmtBytes(2048)).toBe('2.0KB')
    expect(fmtBytes(3 * 1024 * 1024)).toBe('3.0MB')
    expect(fmtBytes(1.5 * 1024 ** 3)).toBe('1.50GB')
  })
  it('dashes non-finite', () => {
    expect(fmtBytes(null)).toBe('—')
    expect(fmtBytes(NaN)).toBe('—')
  })
})

describe('fmtS', () => {
  it('picks a human unit', () => {
    expect(fmtS(45)).toBe('45s')
    expect(fmtS(120)).toBe('2m')
    expect(fmtS(7200)).toBe('2.0h')
    expect(fmtS(undefined)).toBe('—')
  })
})

describe('fmtPct', () => {
  it('rounds', () => {
    expect(fmtPct(0.916)).toBe('92%')
    expect(fmtPct(null)).toBe('—')
  })
})

describe('fmtTs', () => {
  it('formats an epoch-seconds timestamp', () => {
    expect(fmtTs(0)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
  })
  it('dashes non-finite', () => {
    expect(fmtTs(NaN)).toBe('—')
    expect(fmtTs(null as unknown as number)).toBe('—')
  })
})

describe('fmtIso', () => {
  it('formats an ISO string', () => {
    expect(fmtIso('2026-11-26T06:00:00+00:00')).toMatch(/^\d{4}-\d{2}-\d{2} /)
  })
  it('dashes invalid', () => {
    expect(fmtIso('not-a-date')).toBe('—')
    expect(fmtIso('')).toBe('—')
    expect(fmtIso(undefined)).toBe('—')
  })
})
