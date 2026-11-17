<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useRuntime } from '../stores/runtime'
import { metaOf } from '../theme'
import StateBadge from '../components/StateBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'
import CoverageBar from '../components/CoverageBar.vue'

const rt = useRuntime()
const router = useRouter()

const cards = computed(() => rt.overviewCards)

function latestValues(c: any): [string, any][] {
  return Object.entries(c.runtime.latest_values || {}).slice(0, 4)
}

function openMonitor(id: string) {
  router.push({ path: '/monitor/' + id })
}
</script>

<template>
  <div class="page">
    <div v-if="!rt.ready" class="muted" style="padding:40px;text-align:center">加载中…</div>
    <div v-else-if="!cards.length" class="muted" style="padding:40px;text-align:center">暂无设备配置</div>
    <div class="grid">
      <div
        v-for="c in cards"
        :key="c.asset_id"
        class="card"
        :style="{ borderColor: metaOf(c.runtime.state).fg + '44' }"
        @click="openMonitor(c.asset_id)"
      >
        <div class="head">
          <div>
            <div class="name">{{ c.name }}</div>
            <div class="muted small">{{ c.asset_id }} · {{ c.kind }}</div>
          </div>
          <StateBadge :state="c.runtime.state" :pulse="c.runtime.state === 'critical' || c.runtime.state === 'warn'" />
        </div>

        <div class="latest">
          <template v-if="latestValues(c).length">
            <div v-for="[k, v] in latestValues(c)" :key="k" class="kv">
              <span class="muted">{{ k }}</span>
              <span class="num big-value" :style="{ fontSize: '20px' }">{{ v != null ? v.toFixed(1) : '—' }}</span>
            </div>
          </template>
          <div v-else class="muted small">无最新值</div>
        </div>

        <CoverageBar :coverage="c.runtime.coverage" :samples="c.runtime.sample_count" />

        <div class="foot">
          <TrustBadge :trust="c.runtime.trust" />
          <span class="muted small">{{ c.runtime.overall_level }} / {{ c.runtime.worst_metric_level }}</span>
        </div>
        <div v-if="c.runtime.state !== 'healthy'" class="reason small" :style="{ color: metaOf(c.runtime.state).fg }">
          {{ c.runtime.state_reason || c.runtime.headline }}
        </div>
        <el-badge v-if="c.runtime.alerts" :value="c.runtime.alerts" class="alert-badge" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.card { position: relative; background: var(--ea-panel); border: 1px solid var(--ea-border); border-radius: 12px; padding: 16px; cursor: pointer; transition: transform .15s ease, border-color .15s ease; display: flex; flex-direction: column; gap: 10px; }
.card:hover { transform: translateY(-2px); border-color: var(--ea-accent); }
.head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.name { font-size: 16px; font-weight: 700; }
.small { font-size: 12px; }
.latest { display: grid; grid-template-columns: repeat(auto-fill, minmax(90px,1fr)); gap: 8px; }
.kv { display: flex; flex-direction: column; }
.foot { display: flex; justify-content: space-between; align-items: center; }
.reason { margin-top: -2px; }
.alert-badge { position: absolute; top: -8px; right: -6px; }
</style>