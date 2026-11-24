// Shared ECharts deep-space observatory theme so charts and badges speak the
// same palette. Registered explicitly at app start (imported by main.ts) — the
// only registration point; every chart uses the "ea-obs" theme name.
// All hex values are copied verbatim from src/theme/tokens.css (spec §1).
import * as echarts from 'echarts'

// tokens.css values (kept in sync by hand; tokens.css is the source of truth)
const COLORS = {
  textHi: '#E9F2FC',   // --tx-hi
  text: '#9FB4CC',     // --tx
  textDim: '#5C7189',  // --tx-dim
  line: '#14243A',     // --line
  line2: '#1E3552',    // --line-2
  bg2: '#0C1626',      // --bg2
  bg3: '#101D30',      // --bg3
  cyan: '#3BE0D8',     // --cyan
  ok: '#3DDC97',       // --ok
  warn: '#F5B84C',     // --warn
  crit: '#FF6B5E',     // --crit
}

/**
 * Hex palette for ECharts option objects. Canvas cannot read CSS custom
 * properties, and only this theme layer may hardcode hexes (same-source
 * contract with tokens.css — meta.ts does the same for badge colors).
 * Band tints are the spec-mandated 7% alpha (§5.2: warn rgba(245,184,76,.07) /
 * crit rgba(255,107,94,.07)); the fill under the line is a cyan wash.
 */
export const EA_COLORS = {
  textHi: COLORS.textHi,
  text: COLORS.text,
  textDim: COLORS.textDim,
  line: COLORS.line,
  line2: COLORS.line2,
  cyan: COLORS.cyan,
  ok: COLORS.ok,
  warn: COLORS.warn,
  crit: COLORS.crit,
  cyanArea: 'rgba(59,224,216,0.10)',
  okBand: 'rgba(61,220,151,0.05)',
  warnBand: 'rgba(245,184,76,0.07)',
  critBand: 'rgba(255,107,94,0.07)',
} as const

echarts.registerTheme('ea-obs', {
  backgroundColor: 'transparent',
  color: [COLORS.cyan, COLORS.ok, COLORS.warn, COLORS.crit, COLORS.text],
  textStyle: { color: COLORS.text },
  axisPointer: {
    lineStyle: { color: COLORS.line2 },
    crossStyle: { color: COLORS.line2 },
    label: { backgroundColor: COLORS.bg3 },
  },
  legend: { textStyle: { color: COLORS.text } },
  grid: { left: 8, right: 16, top: 30, bottom: 8, containLabel: true },
  categoryAxis: {
    axisLine: { lineStyle: { color: COLORS.line } },
    axisTick: { show: false },
    axisLabel: { color: COLORS.text, fontSize: 10 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: COLORS.text, fontSize: 10 },
    splitLine: { lineStyle: { color: COLORS.line } },
  },
  dataZoom: {
    backgroundColor: 'rgba(20,36,58,0.35)',      // --line @ 0.35
    dataBackgroundColor: 'rgba(59,224,216,0.15)', // --cyan @ 0.15
    fillerColor: 'rgba(59,224,216,0.10)',         // --cyan @ 0.10
    handleColor: COLORS.cyan,
    textStyle: { color: COLORS.text },
  },
  tooltip: {
    backgroundColor: 'rgba(8,15,26,0.95)',        // --bg1 @ 0.95
    borderColor: COLORS.line2,
    textStyle: { color: COLORS.textHi, fontSize: 12 },
  },
  line: { lineStyle: { width: 2 } },
})

export default echarts
