<template>
  <div class="conn" :class="rt.conn" :title="tip">
    <span class="dot" />
    <span class="txt">{{ label }}</span>
  </div>
</template>

<script setup lang="ts">
// Topbar SSE health chip: connected / reconnecting / offline + last event age.
import { computed } from 'vue'
import { useRuntime } from '../stores/runtime'
import { relativeTime } from '../theme'

const rt = useRuntime()

const label = computed(() => {
  if (rt.conn === 'online') return '实时连接'
  if (rt.conn === 'reconnecting') return '重连中…'
  return '连接中…'
})

const tip = computed(() =>
  rt.lastEventAt ? `最近数据事件：${relativeTime(Math.floor(rt.lastEventAt / 1000))}` : '尚未收到数据事件')
</script>

<style scoped>
.conn { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 10px;
  border-radius: var(--ea-r-pill); border: 1px solid var(--ea-border); background: var(--ea-panel-soft); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ea-text-dim); }
.conn.online .dot { background: #3ddc97; box-shadow: 0 0 6px rgba(61,220,151,0.8); }
.conn.reconnecting .dot { background: #f5b14c; animation: blink 1s infinite; }
.conn.connecting .dot { background: var(--ea-text-dim); animation: blink 1.4s infinite; }
.txt { color: var(--ea-text-muted); }
@keyframes blink { 50% { opacity: 0.25; } }
</style>
