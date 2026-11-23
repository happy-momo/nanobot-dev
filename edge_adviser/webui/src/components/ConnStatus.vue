<script setup lang="ts">
// ConnStatus — SSE health chip (spec §5.1 LIVE 指示): dot + label, plus the
// mono age of the last data event when one has arrived. No blinking: spec §1.4
// allows exactly four animations site-wide, so state changes are color-only.
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ conn: 'online' | 'reconnecting' | 'connecting'; lastEventAt?: number }>(),
  { lastEventAt: undefined },
)

const LABEL: Record<'online' | 'reconnecting' | 'connecting', string> = {
  online: '实时',
  reconnecting: '重连中',
  connecting: '连接中',
}

const label = computed(() => LABEL[props.conn])

const ageS = computed(() => {
  if (!props.lastEventAt) return null
  return Math.max(0, Math.floor((Date.now() - props.lastEventAt) / 1000))
})

const ageText = computed(() => {
  const s = ageS.value
  if (s === null) return ''
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h`
})

const tip = computed(() => {
  const s = ageS.value
  return s === null ? '尚未收到数据事件' : `最近数据事件：${ageText.value}前`
})
</script>

<template>
  <span class="cs" :class="`cs-${conn}`" role="status" :title="tip">
    <span class="cs-dot" />
    <span class="cs-label">{{ label }}</span>
    <span v-if="conn === 'online' && ageText" class="cs-age mono">{{ ageText }}</span>
  </span>
</template>

<style scoped>
.cs { display: inline-flex; align-items: center; gap: 6px; font-size: 11px;
  padding: 3px 10px; border: 1px solid var(--line); border-radius: var(--r-s);
  background: var(--bg1); white-space: nowrap; }
.cs-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--tx-dim); flex-shrink: 0; }
.cs-online .cs-dot { background: var(--ok); }
.cs-reconnecting .cs-dot { background: var(--warn); }
.cs-connecting .cs-dot { background: var(--tx-dim); }
.cs-label { color: var(--tx); letter-spacing: 1px; }
.cs-age { font-size: 10px; color: var(--tx-dim); font-variant-numeric: tabular-nums; }
</style>
