<template>
  <div ref="el" class="spark" />
</template>

<script setup lang="ts">
// Mini trend line: no axes, no tooltip — an at-a-glance shape. The last point
// is colored by its level so the current condition reads instantly.
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { levelTone } from '../theme'
import '../theme/echarts'

const props = withDefaults(defineProps<{
  points: Array<{ value: number | null; level?: string }>
  color?: string
}>(), { color: '#3f8ff7' })

const el = ref<HTMLElement>()
let chart: echarts.ECharts | null = null
let ro: ResizeObserver | null = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value, 'ea-dark')
  const xs = props.points.map((_, i) => i)
  const ys = props.points.map((p) => p.value)
  const last = props.points.length ? props.points[props.points.length - 1] : null
  const lastColor = last?.level ? (levelTone[last.level] || props.color) : props.color
  chart.setOption({
    grid: { left: 2, right: 2, top: 4, bottom: 2 },
    xAxis: { type: 'category', show: false, data: xs },
    yAxis: { type: 'value', show: false, min: 'dataMin', max: 'dataMax' },
    series: [{
      type: 'line', data: ys, smooth: true, symbol: 'none',
      lineStyle: { width: 1.6, color: props.color },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(63,143,247,0.30)' },
          { offset: 1, color: 'rgba(63,143,247,0.02)' },
        ]),
      },
      markPoint: last && last.value != null ? {
        symbol: 'circle', symbolSize: 5,
        itemStyle: { color: lastColor },
        coord: [props.points.length - 1, last.value],
      } : undefined,
    }],
    animation: false,
  })
}

onMounted(() => {
  render()
  ro = new ResizeObserver(() => chart?.resize())
  if (el.value) ro.observe(el.value)
})
watch(() => props.points, render, { deep: true })
onBeforeUnmount(() => {
  ro?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.spark { height: 40px; width: 100%; }
</style>
