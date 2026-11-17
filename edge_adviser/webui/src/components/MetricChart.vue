<script setup lang="ts">
// ECharts metric chart: value curve(s) over a history of snapshots, with the
// normal / warn / critical bands drawn behind the line — the chart's colors come
// from the same theme table as the badges, so "band = alarm zone" is one language.
import { onMounted, onBeforeUnmount, ref, watch, computed } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, MarkLineComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsType } from 'echarts/core'

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, MarkLineComponent, DataZoomComponent, CanvasRenderer])

const props = defineProps<{
  metricKey: string
  metricLabel: string
  unit: string
  series: { window_end: string; value: number | null; level?: string }[]
  spec?: { norm_min?: number | null; norm_max?: number | null; warn_min?: number | null; warn_max?: number | null; crit_min?: number | null; crit_max?: number | null } | null
}>()

const el = ref<HTMLDivElement>()
let chart: EChartsType | null = null

function buildOption() {
  const s = props.spec
  const cats = props.series.map((p) => (p.window_end ? p.window_end.slice(5, 16) : ''))
  const values = props.series.map((p) => p.value)
  // band lines for warn/critical (dashed thresholds) if provided
  const markLines: any[] = []
  const axis = [{ type: 'category', data: cats, axisLabel: { color: '#8fa6bb' }, name: '窗口' }]
  if (s) {
    for (const [v, name, color] of [
      [s.norm_min, 'norm<=', '#3ddc97'],
      [s.norm_max, 'norm', '#3ddc97'],
      [s.warn_min, 'warn', '#f5b14c'],
      [s.warn_max, 'warn', '#f5b14c'],
      [s.crit_min, 'crit', '#f4554f'],
      [s.crit_max, 'crit', '#f4554f'],
    ] as const) {
      if (v != null) markLines.push({ yAxis: v, name, lineStyle: { color, type: 'dashed', width: 1 } })
    }
    markLines.push({
      yAxis: (s.crit_max ?? s.norm_max ?? 50),
      label: { show: true, position: 'end', color: '#f4554f', formatter: () => '' },
    })
  }
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#15293a',
      borderColor: '#26415a',
      textStyle: { color: '#e8f0f7' },
    },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: axis,
    yAxis: { type: 'value', name: props.unit || '', nameTextStyle: { color: '#8fa6bb' }, axisLabel: { color: '#8fa6bb' }, splitLine: { lineStyle: { color: '#1c3347' } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 6, borderColor: '#26415a', backgroundColor: '#12283a', dataBackground: { lineStyle: { color: '#26415a' }, areaStyle: { color: '#1c3347' } } }],
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
        markLine: { data: markLines, symbol: 'none' },
      },
    ],
  }
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption(buildOption(), true)
}

function onResize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
watch(() => JSON.stringify(props.series), () => render(), { deep: false })
</script>

<template>
  <div ref="el" class="metric-chart" />
</template>

<style scoped>
.metric-chart { width: 100%; height: 260px; }
</style>