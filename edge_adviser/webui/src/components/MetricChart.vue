<script setup lang="ts">
// ECharts metric chart: value curve over the snapshot history with the
// normal/warn/critical bands drawn as colored markArea zones behind the line —
// the chart's colors come from the same theme table as the badges, so
// "band = alarm zone" is one visual language. Threshold changes alone (spec)
// also trigger a redraw; a ResizeObserver handles container-only resizes such
// as the collapsible sidebar.
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, MarkAreaComponent, MarkLineComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsType } from 'echarts/core'
import { axisTs, toEpochMs } from '../theme'

echarts.use([LineChart, GridComponent, TooltipComponent, MarkAreaComponent, MarkLineComponent, DataZoomComponent, CanvasRenderer])

export interface ChartSpec {
  norm_min?: number | null
  norm_max?: number | null
  warn_min?: number | null
  warn_max?: number | null
  crit_min?: number | null
  crit_max?: number | null
}

const props = defineProps<{
  metricKey: string
  metricLabel: string
  unit: string
  series: { window_end: string; value: number | null; level?: string }[]
  spec?: ChartSpec | null
}>()

const el = ref<HTMLDivElement>()
let chart: EChartsType | null = null
let ro: ResizeObserver | null = null

const TONE = {
  norm: 'rgba(61,220,151,0.10)',
  warn: 'rgba(245,177,76,0.12)',
  crit: 'rgba(244,85,79,0.14)',
}

function formatFull(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function buildOption() {
  const s = props.spec || {}
  // local-time axis labels (MM-DD HH:mm, HH:mm within a day) instead of raw
  // UTC string slices; tooltip shows the full local timestamp
  const cats = props.series.map((p, i) => axisTs(p.window_end, props.series[i - 1]?.window_end))
  const values = props.series.map((p) => p.value)

  // markArea bands: contiguous zones between sorted thresholds
  const areas: Array<{ yAxis: number[]; itemStyle: { color: string } }> = []
  const edges = [s.crit_min, s.warn_min, s.norm_min, s.norm_max, s.warn_max, s.crit_max]
    .filter((v) => v != null) as number[]
  const uniq = [...new Set(edges)].sort((a, b) => a - b)
  const bandTone = (mid: number) => {
    if (s.crit_min != null && mid < s.crit_min) return TONE.crit
    if (s.crit_max != null && mid > s.crit_max) return TONE.crit
    if ((s.warn_min != null && mid < s.warn_min) || (s.warn_max != null && mid > s.warn_max))
      return TONE.warn
    return TONE.norm
  }
  for (let i = 0; i < uniq.length - 1; i++) {
    areas.push({
      yAxis: [uniq[i], uniq[i + 1]],
      itemStyle: { color: bandTone((uniq[i] + uniq[i + 1]) / 2) },
    })
  }
  // extend the outermost zones to the axis edges
  if (uniq.length >= 2) {
    if (s.crit_min != null) areas.push({ yAxis: [uniq[0] - (uniq[1] - uniq[0]) * 4, uniq[0]], itemStyle: { color: TONE.crit } })
    if (s.crit_max != null) areas.push({ yAxis: [uniq[uniq.length - 1], uniq[uniq.length - 1] + (uniq[uniq.length - 1] - uniq[uniq.length - 2]) * 4], itemStyle: { color: TONE.crit } })
  }

  const markLines: any[] = []
  for (const [v, color] of [
    [s.warn_min, '#f5b14c'], [s.warn_max, '#f5b14c'],
    [s.crit_min, '#f4554f'], [s.crit_max, '#f4554f'],
  ] as const) {
    if (v != null) markLines.push({ yAxis: v, lineStyle: { color, type: 'dashed', width: 1 }, label: { show: false } })
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p0 = Array.isArray(params) ? params[0] : params
        const idx = p0?.dataIndex ?? 0
        const raw = props.series[idx]?.window_end
        const t = toEpochMs(raw)
        const full = t !== null ? formatFull(t) : String(raw ?? '')
        return `${full}<br/>${p0?.marker || ''}${props.metricLabel || props.metricKey}: ${p0?.value ?? '—'} ${props.unit || ''}`
      },
    },
    grid: { left: 8, right: 16, top: 26, bottom: 36, containLabel: true },
    xAxis: { type: 'category', data: cats },
    yAxis: { type: 'value', name: props.unit || '' },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 4 }],
    series: [
      {
        name: props.metricLabel || props.metricKey,
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: '#3f8ff7', width: 2 },
        itemStyle: { color: '#3f8ff7' },
        areaStyle: { color: 'rgba(63,143,247,0.08)' },
        markArea: { silent: true, data: areas },
        markLine: { silent: true, symbol: 'none', data: markLines },
      },
    ],
  }
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value, 'ea-dark')
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  render()
  ro = new ResizeObserver(() => chart?.resize())
  if (el.value) ro.observe(el.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  chart?.dispose()
  chart = null
})
watch(() => JSON.stringify(props.series), render)
watch(() => JSON.stringify(props.spec), render)
</script>

<template>
  <div ref="el" class="metric-chart" />
</template>

<style scoped>
.metric-chart { width: 100%; height: 260px; }
</style>
