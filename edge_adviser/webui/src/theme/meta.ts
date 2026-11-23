// State / trust / level metadata — the single source of truth for the UI's
// state colors and their Chinese labels (spec §1.1 mapping table).
//
// WHY HEX LITERALS LIVE HERE: TS cannot read CSS custom properties, and
// SVG/canvas consumers (ECharts theme, sparklines, spectrum strip) need real
// color strings. Every hex below is copied verbatim from src/theme/tokens.css
// (same-source contract) — when a token changes, change it here too.
//
// Backend contract (edge_adviser/edge_adviser/config/model.py) spells trust
// full|partial|low|none and has no 'warn' AssetState; the vocabulary below is
// the design-system spelling agreed in the spec, so callers map backend values
// onto these keys before rendering.
export type AssetState =
  | 'healthy'
  | 'warn'
  | 'critical'
  | 'insufficient'
  | 'unknown'
  | 'offline'
  | 'stale'
  | 'planned_downtime'
export type TrustLevel = 'high' | 'medium' | 'low'
export type AlertLevel = 'ok' | 'warn' | 'crit'

export const stateMeta: Record<AssetState, { color: string; label: string; glow?: boolean }> = {
  healthy:          { color: '#3DDC97', label: '正常' },
  warn:             { color: '#F5B84C', label: '警告' },
  critical:         { color: '#FF6B5E', label: '严重', glow: true }, // 唯一允许光晕的状态
  offline:          { color: '#FF6B5E', label: '离线' }, // 数据故障按 crit 处理
  stale:            { color: '#7E93AC', label: '过期' },
  planned_downtime: { color: '#273043', label: '计划停机' }, // 不计告警
  insufficient:     { color: '#273043', label: '数据不足' },
  unknown:          { color: '#273043', label: '未知' },
}

export const levelMeta: Record<AlertLevel, { color: string; label: string }> = {
  ok:   { color: '#3DDC97', label: '正常' },
  warn: { color: '#F5B84C', label: '警告' },
  crit: { color: '#FF6B5E', label: '严重' },
}

export const trustMeta: Record<TrustLevel, { color: string; label: string }> = {
  high:   { color: '#3BE0D8', label: '高可信' }, // trust 一律用青色（事实可信度）
  medium: { color: '#F5B84C', label: '中可信' },
  low:    { color: '#FF6B5E', label: '低可信' },
}
