// Industrial dark HMI theme + the single source of truth for state colors.
//
// Contract with the backend enums (config/model.py): every «status» and «trust»
// value maps to one color. The HMI treats colors as the ONE visual language —
// state badges, chart bands and alert flashes all read from this table, so the
// operator never has to decode two different schemes. Trust is intentionally a
// separate shape (a shield) from status, because "state" and "data-trust" are
// two different things and must both be visible at a glance.

export interface HmiColors {
  bg: string
  bgDeep: string
  panel: string
  panelSoft: string
  border: string
  text: string
  textMuted: string
  accent: string
}

export const colors: HmiColors = {
  bg: '#0b1620',
  bgDeep: '#081018',
  panel: '#122331',
  panelSoft: '#1a3040',
  border: '#24405a',
  text: '#e8f0f7',
  textMuted: '#8fa6bb',
  accent: '#3f8ff7',
}

// status → foreground / soft background (for a pill/badge)
export const stateMeta: Record<string, { label: string; fg: string; bg: string }> = {
  healthy: { label: '正常', fg: '#3ddc97', bg: 'rgba(61,220,151,0.16)' },
  warn: { label: '警告', fg: '#f5b14c', bg: 'rgba(245,177,76,0.16)' },
  critical: { label: '严重', fg: '#f4554f', bg: 'rgba(244,85,79,0.18)' },
  insufficient: { label: '数据不足', fg: '#e8c16b', bg: 'rgba(232,193,107,0.14)' },
  unknown: { label: '无数据', fg: '#8ea0b3', bg: 'rgba(142,160,179,0.15)' },
  offline: { label: '断连', fg: '#a07cf0', bg: 'rgba(160,124,240,0.16)' },
  stale: { label: '数据过时', fg: '#73899c', bg: 'rgba(115,137,156,0.15)' },
  planned_downtime: { label: '计划停机', fg: '#4aa8ff', bg: 'rgba(74,168,255,0.16)' },
}

// trust → a shield glyph + tone
export const trustMeta: Record<string, { label: string; tone: string }> = {
  full: { label: '可信', tone: '#3ddc97' },
  partial: { label: '部分可信', tone: '#f5b14c' },
  low: { label: '低可信', tone: '#f4554f' },
  none: { label: '不可信', tone: '#73899c' },
}

// level → fg/bg. The single source for level chips, alert severity and chart
// bands (MetricChart markArea / Sparkline end-point all read from here).
export const levelMeta: Record<string, { label: string; fg: string; bg: string }> = {
  info: { label: '正常', fg: '#3ddc97', bg: 'rgba(61,220,151,0.16)' },
  warn: { label: '警告', fg: '#f5b14c', bg: 'rgba(245,177,76,0.16)' },
  critical: { label: '严重', fg: '#f4554f', bg: 'rgba(244,85,79,0.18)' },
  insufficient: { label: '数据不足', fg: '#e8c16b', bg: 'rgba(232,193,107,0.14)' },
  unknown: { label: '无数据', fg: '#8ea0b3', bg: 'rgba(142,160,179,0.15)' },
}

// alert -> band / level tone (kept for backward compatibility)
export const levelTone: Record<string, string> = {
  info: colors.accent,
  warn: '#f5b14c',
  critical: '#f4554f',
  insufficient: '#e8c16b',
  unknown: '#8ea0b3',
}

export function stateOf(s?: string): string {
  return (s || 'unknown') in stateMeta ? (s as string) : 'unknown'
}

export function metaOf(s?: string) {
  return stateMeta[stateOf(s)]
}

export function levelOf(s?: string): string {
  return (s || 'unknown') in levelMeta ? (s as string) : 'unknown'
}

// --------------------------------------------------------------------------- //
//  shared formatting helpers (were duplicated per-view)
// --------------------------------------------------------------------------- //
export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(digits)
}

export function formatTs(ts: string | number | undefined | null): string {
  const t = toEpochMs(ts)
  if (t === null) return ts ? String(ts) : '—'
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** Normalize any backend timestamp (ISO string or epoch seconds) to epoch ms.
 * Epoch-seconds and epoch-millis are disambiguated (acked_at arrived in both
 * units across code paths); ISO strings parse as absolute instants. */
export function toEpochMs(ts: string | number | undefined | null): number | null {
  if (ts === undefined || ts === null || ts === '') return null
  if (typeof ts === 'number') {
    // < 1e12 → epoch seconds; ≥ 1e12 → already milliseconds
    return ts < 1e12 ? ts * 1000 : ts
  }
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? null : d.getTime()
}

/** Relative age like "42 秒前" / "5 分钟前" / "3 小时前" / "2 天前".
 * `nowMs` lets callers pass a reactive clock so ages advance without waiting
 * for an unrelated re-render; future timestamps read as "刚刚" (never negative). */
export function relativeTime(ts: string | number | undefined | null, nowMs?: number): string {
  const t = toEpochMs(ts)
  if (t === null) return ts ? String(ts) : '—'
  const now = nowMs ?? Date.now()
  const diff = Math.floor((now - t) / 1000)
  if (diff < 5) return '刚刚'
  if (diff < 60) return `${diff} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}
