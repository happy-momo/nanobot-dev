<script setup lang="ts">
// MetricChart — the full ECharts curve for ONE metric (spec §5.2/§6): area line
// over the snapshot history with the metric's alarm bands drawn as translucent
// markArea zones (warn 7% / crit 7%) and the thresholds as dashed markLines, so
// "band = alarm zone" is one visual language across charts, MetricBand and the
// state dots. dataZoom inside keeps long histories explorable on a tablet.
//
// Canvas cannot read CSS custom properties, so option colors come from
// EA_COLORS (theme/echarts.ts, same-source contract with tokens.css) — the only
// hex literals allowed outside theme/meta.ts.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, MarkAreaComponent, MarkLineComponent,
  DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsType } from 'echarts/core'
import { EA_COLORS } from '../theme/echarts'
import type { MetricSpecMeta } from '../theme/meta'

echarts.use([
  LineChart, GridComponent, TooltipComponent, MarkAreaComponent,
  MarkLineComponent, DataZoomComponent, CanvasRenderer,
])

export interface ChartPoint {
  ts: number
  v: number
}

const props = withDefaults(
  defineProps<{
    spec: MetricSpecMeta
    points: ChartPoint[]
    unit?: string
    /** chart body height in px (panels size the box; charts keep ~230 default) */
    height?: number
  }>(),
  { unit: '', height: 230 },
)

const el = ref<HTMLDivElement>()
let chart: EChartsType | null = null
let ro: ResizeObserver | null = null

const label = () => props.spec.key || 'metric'

// ------------------------------------------------------------- bands & lines --
// Edges are the sorted, de-duplicated finite thresholds; every consecutive pair
// becomes one markArea whose tone follows the band the MIDPOINT sits in (the
// same trick the old Monitor chart used, kept because it renders one-tone zones
// without knowing the axis range). Outermost crit zones extend past the axis so
// excursions above/below the last threshold still read as crit territory.
type AreaDatum = [
  { yAxis: number; itemStyle: { color: string } },
  { yAxis: number },
]

function buildAreas(s: MetricSpecMeta): AreaDatum[] {
  const edges = [s.crit_min, s.warn_min, s.norm_min, s.norm_max, s.warn_max, s.crit_max]
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  const uniq = [...new Set(edges)].sort((a, b) => a - b)
  const tone = (mid: number): string => {
    if (s.crit_min != null && mid < s.crit_min) return EA_COLORS.critBand
    if (s.crit_max != null && mid > s.crit_max) return EA_COLORS.critBand
    if ((s.warn_min != null && mid < s.warn_min) ||
        (s.warn_max != null && mid > s.warn_max)) return EA_COLORS.warnBand
    return EA_COLORS.okBand  // healthy band: barely-there green wash
  }
  const areas: AreaDatum[] = []
  const area = (min: number, max: number, color: string): AreaDatum => [
    { yAxis: min, itemStyle: { color } },
    { yAxis: max },
  ]
  for (let i = 0; i < uniq.length - 1; i++) {
    areas.push(area(uniq[i], uniq[i + 1], tone((uniq[i] + uniq[i + 1]) / 2)))
  }
  if (uniq.length >= 2) {
    const step = uniq[1] - uniq[0]
    const lastStep = uniq[uniq.length - 1] - uniq[uniq.length - 2]
    if (s.crit_min != null) {
      areas.push(area(uniq[0] - step * 6, uniq[0], EA_COLORS.critBand))
    }
    if (s.crit_max != null) {
      areas.push(area(uniq[uniq.length - 1], uniq[uniq.length - 1] + lastStep * 6, EA_COLORS.critBand))
    }
  }
  return areas
}

// dashed threshold lines at every finite band edge (warn amber, crit red)
function buildMarkLines(s: MetricSpecMeta) {
  const out: Array<{ yAxis: number; lineStyle: Record<string, unknown>; label: Record<string, unknown> }> = []
  const push = (v: number | null | undefined, color: string) => {
    if (typeof v !== 'number' || !Number.isFinite(v)) return
    out.push({
      yAxis: v,
      lineStyle: { color, type: 'dashed', width: 1 },
      label: { show: false },
    })
  }
  push(s.warn_min, EA_COLORS.warn)
  push(s.warn_max, EA_COLORS.warn)
  push(s.crit_min, EA_COLORS.crit)
  push(s.crit_max, EA_COLORS.crit)
  return out
}

// --------------------------------------------------------------- x axis labels --
// timestamps are epoch SECONDS; axis labels compress to HH:MM, and when the
// window spans midnight (or more) the first label carries MM-DD.
function fmtCat(ts: number, prev: number | undefined): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`
  if (prev !== undefined) {
    const pd = new Date(prev * 1000)
    if (pd.getDate() === d.getDate() && pd.getMonth() === d.getMonth()) return hm
  }
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`
}

function fmtFull(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// ------------------------------------------------------------------- option --
function buildOption() {
  const s = props.spec
  const pts = props.points
  return {
    backgroundColor: 'transparent',
    animationDurationUpdate: 220,
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const arr = Array.isArray(params) ? params : [params]
        const p0 = arr[0] as { dataIndex?: number; marker?: string; value?: number } | undefined
        const idx = p0?.dataIndex ?? 0
        const pt = pts[idx]
        const val = p0?.value ?? pt?.v
        const when = pt && Number.isFinite(pt.ts) ? fmtFull(pt.ts) : ''
        const v = typeof val === 'number' && Number.isFinite(val)
          ? (Math.abs(val) >= 100 ? val.toFixed(0) : val.toFixed(1))
          : '—'
        return `${when}<br/>${p0?.marker || ''}${label()}: ${v} ${props.unit || ''}`
      },
    },
    grid: { left: 8, right: 16, top: 18, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: pts.map((p, i) => fmtCat(p.ts, pts[i - 1]?.ts)),
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      name: props.unit || '',
      nameTextStyle: { color: EA_COLORS.textDim, padding: [0, 0, 0, -14] },
      scale: true,   // don't force the axis to start at 0 — 60±3°C curves flatline otherwise
      axisLabel: { formatter: (v: number) => (Math.abs(v) >= 100 ? String(Math.round(v)) : String(Math.round(v * 10) / 10)) },
    },
    dataZoom: [{ type: 'inside' }],
    series: [
      {
        name: label(),
        type: 'line',
        data: pts.map((p) => p.v),
        smooth: 0.3,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: pts.length <= 40,
        lineStyle: { color: EA_COLORS.cyan, width: 1.6 },
        itemStyle: { color: EA_COLORS.cyan },
        areaStyle: { color: EA_COLORS.cyanArea },
        emphasis: { disabled: true },
        markArea: { silent: true, data: buildAreas(s) },
        markLine: { silent: true, symbol: 'none', data: buildMarkLines(s) },
      },
    ],
  }
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value, 'ea-obs')
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  render()
  ro = new ResizeObserver(() => chart?.resize())
  if (el.value) ro.observe(el.value)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
  chart?.dispose()
  chart = null
})

// points AND spec both drive the option — threshold edits (Task 7 settings tab)
// must redraw the bands without the data changing.
watch(() => props.points, render)
watch(() => props.spec, render)
</script>

<template>
  <div ref="el" class="mc" :style="{ height: height + 'px' }" />
</template>

<style scoped>
.mc { width: 100%; min-width: 0; }
</style>
