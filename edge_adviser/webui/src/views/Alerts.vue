<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRuntime } from '../stores/runtime'
import StateBadge from '../components/StateBadge.vue'
import { metaOf } from '../theme'

const rt = useRuntime()

const activeAlerts = computed(() => rt.alerts.filter((a) => !a.acked).sort((a, b) => b.ts - a.ts))
const ackedAlerts = computed(() => rt.alerts.filter((a) => a.acked).sort((a, b) => b.ts - a.ts))
const showAcked = ref(false)
const openActive = ref([] as number[])
const openAcked = ref([] as number[])

function fmtTs(ts: number) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function ack(a: any) {
  await rt.acknowledge(a.asset_id, a.ts)
}
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <div class="muted" style="font-size:13px">
        <span style="color:var(--ea-accent)">{{ activeAlerts.length }}</span> 条未确认告警（真实状态变化，已去重/去抖）
      </div>
      <div class="spacer" />
      <el-switch v-model="showAcked" active-text="显示已确认" />
    </div>

    <el-collapse v-model="openActive">
      <el-collapse-item v-for="a in activeAlerts" :key="a.ts" :name="a.ts">
        <template #title>
          <div class="al-row">
            <StateBadge :state="a.subject || a.state" :pulse="true" />
            <span class="name">{{ a.asset_id }}</span>
            <span class="muted small">{{ a.headline }}</span>
            <span class="muted num" style="margin-left:auto">{{ fmtTs(a.ts) }}</span>
          </div>
        </template>
        <div class="al-detail">
          <div>动作对象：<b>{{ a.subject || a.state }}</b> · 数据状态：<span :style="{ color: metaOf(a.state).fg }">{{ metaOf(a.state).label }}</span></div>
          <div class="muted">原因：{{ a.reason || '—' }}</div>
          <div class="muted">判读：{{ a.headline || '—' }}</div>
          <div class="actions">
            <el-button size="small" type="primary" @click="ack(a)">确认</el-button>
          </div>
        </div>
      </el-collapse-item>
      <div v-if="!activeAlerts.length" class="empty muted">当前无未确认告警 ✓</div>
    </el-collapse>

    <template v-if="showAcked && ackedAlerts.length">
      <h3 class="muted" style="margin-top:24px">已确认</h3>
      <el-collapse v-model="openAcked">
        <el-collapse-item v-for="a in ackedAlerts" :key="a.ts" :name="a.ts">
          <template #title>
            <div class="al-row">
              <StateBadge :state="a.state" />
              <span class="name">{{ a.asset_id }}</span>
              <span class="muted num" style="margin-left:auto">{{ fmtTs(a.ts) }}</span>
            </div>
          </template>
          <div class="muted">已确认 {{ fmtTs(a.acked_at) }}</div>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<style scoped>
.spacer { flex: 1; }
.al-row { display: flex; align-items: center; gap: 12px; width: 100%; padding-right: 10px; }
.name { font-weight: 600; }
.small { font-size: 12px; }
.al-detail { padding: 4px 8px 4px 16px; display: flex; flex-direction: column; gap: 4px; }
.actions { margin-top: 8px; }
.empty { padding: 30px; text-align: center; }
</style>