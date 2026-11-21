<script setup lang="ts">
// Alert center: filterable (asset / severity / ack state) list with per-row
// acknowledge + feedback. Rows key on asset+ts; severity color bar comes from
// overall_level via levelMeta.
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useRuntime, type AlertRec } from '../stores/runtime'
import { formatTs, relativeTime, levelMeta, metaOf, toEpochMs } from '../theme'
import StateBadge from '../components/StateBadge.vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()

const fAsset = ref('')
const fSeverity = ref('')
const showAcked = ref(false)
const acking = ref<number | null>(null)

const severities = [
  { value: 'warn', label: '警告' },
  { value: 'critical', label: '严重' },
  { value: 'offline', label: '断连' },
  { value: 'stale', label: '数据过时' },
]

const active = computed(() =>
  rt.alerts
    .filter((a) => !a.acked)
    .filter((a) => (!fAsset.value || a.asset_id === fAsset.value))
    .filter((a) => (!fSeverity.value || (a.subject || a.state) === fSeverity.value))
    .sort((a, b) => b.ts - a.ts))

const acked = computed(() =>
  rt.alerts
    .filter((a) => a.acked)
    .filter((a) => (!fAsset.value || a.asset_id === fAsset.value))
    .filter((a) => (!fSeverity.value || (a.subject || a.state) === fSeverity.value))
    .sort((a, b) => (toEpochMs(b.acked_at) ?? b.ts * 1000) - (toEpochMs(a.acked_at) ?? a.ts * 1000)))

const counts = computed(() => ({
  warn: rt.unackedAlerts.filter((a) => a.subject === 'warn' || a.overall_level === 'warn').length,
  critical: rt.unackedAlerts.filter((a) => a.subject === 'critical' || a.overall_level === 'critical').length,
  offline: rt.unackedAlerts.filter((a) => a.subject === 'offline').length,
  stale: rt.unackedAlerts.filter((a) => a.subject === 'stale').length,
}))

function keyOf(a: AlertRec) {
  return `${a.asset_id}:${a.ts}`
}

async function ack(a: AlertRec) {
  acking.value = a.ts
  try {
    await rt.acknowledge(a.asset_id, a.ts)
    ElMessage.success('已确认')
  } catch (e: any) {
    ElMessage.error(e?.message || '确认失败')
  } finally {
    acking.value = null
  }
}

function severityTone(a: AlertRec) {
  return levelMeta[a.overall_level || 'warn']?.fg || '#f5b14c'
}
function severityBg(a: AlertRec) {
  return levelMeta[a.overall_level || 'warn']?.bg || 'rgba(245,177,76,0.16)'
}
function severityLabel(a: AlertRec) {
  const s = a.subject || a.overall_level || 'warn'
  return levelMeta[s]?.label || s
}
</script>

<template>
  <div class="page">
    <PageHeader title="告警中心" subtitle="真实状态变化；计划停机豁免">
      <el-select v-model="fAsset" clearable placeholder="全部设备" style="width:170px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <el-select v-model="fSeverity" clearable placeholder="全部级别" style="width:130px">
        <el-option v-for="s in severities" :key="s.value" :value="s.value" :label="s.label" />
      </el-select>
      <el-switch v-model="showAcked" active-text="已确认" />
    </PageHeader>

    <!-- severity count chips -->
    <div class="count-row">
      <span class="chip" v-for="s in severities" :key="s.value"
            :style="{ color: levelMeta[s.value]?.fg, background: levelMeta[s.value]?.bg }">
        {{ s.label }} {{ (counts as any)[s.value] }}
      </span>
      <span class="muted small" style="margin-left:auto">活动 {{ active.length }} 条</span>
    </div>

    <EmptyState v-if="!active.length && (!showAcked || !acked.length)"
                icon="✅" text="当前无活动告警" hint="告警仅在真实状态变化时触发（去重直至确认）" />

    <div v-else class="list">
      <el-collapse>
        <el-collapse-item v-for="a in active" :key="keyOf(a)" :name="keyOf(a)">
          <template #title>
            <div class="row" :style="{ borderLeftColor: severityTone(a) }">
              <span class="sev-tag" :style="{ color: severityTone(a), background: severityBg(a) }">
                {{ severityLabel(a) }}
              </span>
              <span class="asset mono">{{ a.asset_id }}</span>
              <span class="headline">{{ a.headline || a.reason }}</span>
              <span class="when muted small">{{ relativeTime(a.ts) }}</span>
            </div>
          </template>
          <div class="detail">
            <div class="kv"><span class="muted small">数据状态</span><StateBadge :state="a.state" /></div>
            <div class="kv"><span class="muted small">告警主体</span><span>{{ a.subject || '—' }}</span></div>
            <div class="kv"><span class="muted small">判读</span><span>{{ a.reason || a.headline || '—' }}</span></div>
            <div class="kv"><span class="muted small">窗口</span><span class="mono">{{ formatTs(a.window_end) }}</span></div>
            <div class="kv"><span class="muted small">触发时间</span><span class="mono">{{ formatTs(a.ts) }}</span></div>
            <div class="actions">
              <el-button size="small" type="primary" :loading="acking === a.ts" @click="ack(a)">
                确认 (ACK)
              </el-button>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>

      <template v-if="showAcked">
        <div class="ack-title muted">已确认历史</div>
        <div v-for="a in acked" :key="keyOf(a)" class="row acked-row">
          <span class="muted">✓</span>
          <span class="asset mono">{{ a.asset_id }}</span>
          <span class="headline muted">{{ a.headline || a.reason }}</span>
          <span class="when muted small">
            确认于 {{ a.acked_at ? formatTs(a.acked_at) : '—' }}
          </span>
        </div>
        <div v-if="!acked.length" class="muted small" style="padding:10px 4px">
          暂无已确认记录
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.count-row { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.list { display: flex; flex-direction: column; gap: 8px; }
.row {
  display: flex; align-items: center; gap: 10px; padding: 6px 10px;
  border-left: 3px solid transparent; min-width: 0; flex: 1;
}
.headline { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; min-width: 0; }
.asset { font-size: 12px; color: var(--ea-text-muted); flex-shrink: 0; }
.when { flex-shrink: 0; }
.sev-tag { padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; flex-shrink: 0; }
.detail {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px 20px; padding: 8px 14px;
}
.kv { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.actions { margin-top: 6px; }
.ack-title { margin: 14px 0 6px; font-size: 13px; }
.acked-row { border-left-color: var(--ea-border); opacity: 0.75; }
</style>
