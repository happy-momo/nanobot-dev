<script setup lang="ts">
// Fleet overview: KPI strip + card wall with sparklines. Every number carries
// its unit; levels use the shared levelMeta chips; the window age is relative.
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useRuntime } from '../stores/runtime'
import { fmtNum, relativeTime, levelMeta } from '../theme'
import StateBadge from '../components/StateBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'
import CoverageBar from '../components/CoverageBar.vue'
import Sparkline from '../components/Sparkline.vue'
import KpiCard from '../components/KpiCard.vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()
const router = useRouter()

const cards = computed(() => rt.overviewCards)
const kpi = computed(() => rt.fleetKpi)

// metric unit lookup from the card's own config
function unitOf(c: any, key: string): string {
  const spec = (c.config?.metrics || []).find((m: any) => m.key === key)
  return spec?.unit || ''
}

function openMonitor(id: string) {
  // cards open the task-oriented hub (state/alerts/report/knowledge in one
  // screen); the Monitor deep page stays reachable from there
  router.push({ path: '/asset/' + id })
}

onMounted(() => {
  if (rt.ready && !cards.value[0]?.runtime?.series) {
    rt.loadOverview(true)
  }
})
</script>

<template>
  <div class="page">
    <PageHeader title="设备总览" subtitle="全场运行态势">
      <el-button size="small" @click="rt.loadOverview(true)" :disabled="!rt.ready">
        刷新
      </el-button>
    </PageHeader>

    <el-skeleton v-if="!rt.ready" :rows="6" animated class="skeleton" />

    <template v-else-if="cards.length">
      <!-- KPI strip -->
      <div class="kpi-strip">
        <KpiCard label="设备总数" :value="kpi.total" sub="在册受控资产" />
        <KpiCard label="正常" :value="kpi.healthy" tone="#3ddc97" sub="数据可信" />
        <KpiCard label="警告" :value="kpi.warn" tone="#f5b14c" sub="告警级别" />
        <KpiCard label="严重" :value="kpi.critical" tone="#f4554f" sub="告警级别" />
        <KpiCard label="数据劣化" :value="kpi.degraded" tone="#a07cf0" sub="断连/过时/无数据" />
        <KpiCard label="未确认告警" :value="kpi.unackedAlerts" tone="#f4554f" sub="待人工确认" />
        <KpiCard label="平均覆盖率" :value="Math.round(kpi.avgCoverage * 100) + '%'" sub="全场数据完整度" />
      </div>

      <!-- card wall -->
      <div class="grid">
        <div
          v-for="c in cards"
          :key="c.asset_id"
          class="card"
          :style="{ borderColor: levelMeta[c.runtime.overall_level]?.fg + '40' }"
          @click="openMonitor(c.asset_id)"
        >
          <div class="head">
            <div>
              <div class="name">{{ c.name }}</div>
              <div class="muted small">{{ c.asset_id }} · {{ c.kind }}</div>
            </div>
            <StateBadge :state="c.runtime.state"
                        :pulse="c.runtime.state === 'critical' || c.runtime.state === 'warn'" />
          </div>

          <!-- trend thumbnail from /api/overview?history=8 -->
          <Sparkline v-if="c.runtime.series?.length"
                     :points="c.runtime.series" />

          <div class="latest">
            <template v-if="Object.keys(c.runtime.latest_values || {}).length">
              <div v-for="(v, k) in c.runtime.latest_values" :key="k" class="kv">
                <span class="muted small">{{ k }}</span>
                <span class="num v" :style="{ color: levelMeta[c.runtime.overall_level]?.fg }">
                  {{ fmtNum(v, 1) }}<span class="unit muted">{{ unitOf(c, String(k)) }}</span>
                </span>
              </div>
            </template>
            <div v-else class="muted small">无最新值</div>
          </div>

          <CoverageBar :coverage="c.runtime.coverage" :samples="c.runtime.sample_count" />

          <div class="foot">
            <TrustBadge :trust="c.runtime.trust" />
            <span class="chip" :style="{
              color: levelMeta[c.runtime.overall_level]?.fg,
              background: levelMeta[c.runtime.overall_level]?.bg }">
              {{ levelMeta[c.runtime.overall_level]?.label || c.runtime.overall_level }}
            </span>
            <span class="muted small">{{ relativeTime(c.runtime.window_end, rt.nowSec * 1000) }}</span>
          </div>
          <div v-if="c.runtime.state_reason || (c.runtime.state !== 'healthy' && c.runtime.headline)"
               class="reason small"
               :style="{ color: levelMeta[c.runtime.overall_level]?.fg }">
            {{ c.runtime.state_reason || c.runtime.headline }}
          </div>
          <el-badge v-if="c.runtime.alerts" :value="c.runtime.alerts" class="alert-badge" />
        </div>
      </div>
    </template>

    <EmptyState v-else text="暂无设备配置" hint="检查 config.json 的 assets 列表">
      <el-button size="small" @click="rt.loadOverview(true)">重新加载</el-button>
    </EmptyState>
  </div>
</template>

<style scoped>
.skeleton { padding: 20px; }
.kpi-strip {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px; margin-bottom: 18px;
}
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.card {
  position: relative; background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-md); padding: 16px; cursor: pointer;
  transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
  display: flex; flex-direction: column; gap: 10px;
}
.card:hover { transform: translateY(-2px); border-color: var(--ea-accent); box-shadow: var(--ea-shadow-md); }
.head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.name { font-size: 16px; font-weight: 700; }
.small { font-size: 12px; }
.latest { display: grid; grid-template-columns: repeat(auto-fill, minmax(86px, 1fr)); gap: 8px; }
.kv { display: flex; flex-direction: column; gap: 2px; }
.v { font-size: 18px; font-weight: 700; }
.unit { font-size: 11px; margin-left: 3px; font-weight: 400; }
.foot { display: flex; justify-content: space-between; align-items: center; gap: 6px; flex-wrap: wrap; }
.reason { margin-top: -2px; }
.alert-badge { position: absolute; top: -8px; right: -6px; }
</style>
