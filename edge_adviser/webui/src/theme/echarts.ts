// Shared ECharts dark theme so charts and badges speak the same palette.
// Registered once at app start (imported by main.ts); every chart uses the
// "ea-dark" theme name.
import * as echarts from 'echarts'
import { colors } from './index'

echarts.registerTheme('ea-dark', {
  backgroundColor: 'transparent',
  textStyle: { color: colors.textMuted },
  axisPointer: {
    lineStyle: { color: colors.border },
    crossStyle: { color: colors.border },
    label: { backgroundColor: colors.panelSoft },
  },
  legend: { textStyle: { color: colors.textMuted } },
  grid: { left: 8, right: 16, top: 30, bottom: 8, containLabel: true },
  categoryAxis: {
    axisLine: { lineStyle: { color: colors.border } },
    axisTick: { show: false },
    axisLabel: { color: colors.textMuted, fontSize: 10 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: colors.textMuted, fontSize: 10 },
    splitLine: { lineStyle: { color: 'rgba(36,64,90,0.45)' } },
  },
  dataZoom: {
    backgroundColor: 'rgba(36,64,90,0.25)',
    dataBackgroundColor: 'rgba(63,143,247,0.25)',
    fillerColor: 'rgba(63,143,247,0.15)',
    handleColor: colors.accent,
    textStyle: { color: colors.textMuted },
  },
  tooltip: {
    backgroundColor: 'rgba(10,22,33,0.95)',
    borderColor: colors.border,
    textStyle: { color: colors.text, fontSize: 12 },
  },
  line: { lineStyle: { width: 2 } },
})

export default echarts
