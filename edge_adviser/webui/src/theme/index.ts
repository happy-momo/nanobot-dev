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
  panel: string
  panelSoft: string
  border: string
  text: string
  textMuted: string
  accent: string
}

export const colors: HmiColors = {
  bg: '#0f1f2d',
  panel: '#15293a',
  panelSoft: '#1c3347',
  border: '#26415a',
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

// alert -> band / level tone (for charts & flashes)
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