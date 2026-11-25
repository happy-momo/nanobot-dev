export function localizeConditionText(value: string | null | undefined): string {
  if (!value) return ''

  const offline = value.match(/^(.+): no data in window — status unknown\.?$/i)
  if (offline) return `${offline[1]}：当前数据窗口无有效数据，设备状态未知。`

  const critical = value.match(/^(.+): one or more parameters exceed critical bands\.?$/i)
  if (critical) return `${critical[1]}：部分参数超出严重阈值范围。`

  const warning = value.match(/^(.+): some parameters approach warning bands\.?$/i)
  if (warning) return `${warning[1]}：部分参数接近警告阈值。`

  const nominal = value.match(/^(.+): nominal conditions\.?$/i)
  if (nominal) return `${nominal[1]}：各项指标均在正常范围内，设备运行平稳。`

  if (value === 'no samples in window') return '当前数据窗口无有效样本，设备状态未知。'
  if (value === 'no samples for metric') return '该指标在当前数据窗口内没有有效样本。'

  const insufficient = value.match(/^n=(\d+)<min_samples=(\d+)$/)
  if (insufficient) return `样本不足：当前 ${insufficient[1]} 个，至少需要 ${insufficient[2]} 个。`

  if (/^vibration\s+/i.test(value)) {
    return value.replace(/^vibration\s+/i, '振动信号').replace(/初期/g, '早期').replace(/,/g, '，')
  }

  return value
}
