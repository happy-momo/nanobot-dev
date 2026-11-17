<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRuntime } from './stores/runtime'
import { connectSSE } from './api/http'
import StateBadge from './components/StateBadge.vue'
import TrustBadge from './components/TrustBadge.vue'

const rt = useRuntime()
const route = useRoute()

// role folding: 操作工 see operations; 工程师/管理 see everything.
const role = ref<'operator' | 'engineer'>(
  (localStorage.getItem('ea_role') as any) || 'engineer',
)
const isOp = computed(() => role.value === 'operator')

const nav = computed(() => [
  { to: '/', label: '总览', key: 'dashboard', basic: true },
  { to: '/monitor', label: '实时监控', key: 'monitor', basic: true },
  { to: '/alerts', label: '告警中心', key: 'alerts', basic: true, badge: rt.overallAlertCount },
  { to: '/reports', label: '深度报告', key: 'reports', opOnly: true },
  { to: '/qa', label: '智能问答', key: 'qa', opOnly: true },
  { to: '/knowledge', label: '知识库', key: 'knowledge', opOnly: true },
  { to: '/feedback', label: '反馈', key: 'feedback', basic: true },
  { to: '/assets', label: '设备管理', key: 'assets', opOnly: true },
  { to: '/runlog', label: '审计', key: 'runlog', opOnly: true },
  { to: '/settings', label: '系统设置', key: 'settings', opOnly: true },
])

const visibleNav = computed(() =>
  nav.value.filter((n) => (isOp.value ? n.basic : true)),
)

async function askPin(): Promise<string> {
  try {
    const { value } = await ElMessageBox.prompt('请输入现场使用 PIN 码', '访问控制', {
      inputType: 'password',
      confirmButtonText: '进入',
      cancelButtonText: '取消',
    })
    return value || ''
  } catch (e) {
    return ''
  }
}

onMounted(async () => {
  await rt.bootstrap(() => askPin())
  // live schedule tick ping to keep tab-wake freshest (informational only)
  connectSSE('/stream/telemetry', () => {})
})

function toggleRole() {
  role.value = role.value === 'operator' ? 'engineer' : 'operator'
  localStorage.setItem('ea_role', role.value)
  ElMessage.success(role.value === 'operator' ? '已切换为【操作工】视图' : '已切换为【工程师】视图')
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="logo">EA</div>
        <div class="title">EdgeAdviser</div>
        <div class="sub">现场设备智能顾问</div>
      </div>
      <nav class="nav">
        <router-link
          v-for="n in visibleNav"
          :key="n.key"
          :to="n.to"
          class="nav-item"
          :class="{ active: route.name === n.key }"
        >
          <span>{{ n.label }}</span>
          <el-badge v-if="n.badge" :value="n.badge" class="nav-badge" />
        </router-link>
      </nav>
      <div class="role-toggle">
        <el-button size="small" round @click="toggleRole">
          当前：{{ isOp ? '操作工' : '工程师' }}，切换视图
        </el-button>
      </div>
    </aside>

    <main class="content">
      <header class="topbar">
        <h1 class="page-title">{{ route.meta.title || '' }}</h1>
        <div class="spacer" />
        <!-- a minimal live strip: asset count + worst state -->
        <template v-if="rt.overviewCards.length">
          <span class="live-chip muted">
            {{ rt.overviewCards.length }} 台设备 · 未确认告警 {{ rt.overallAlertCount }}
          </span>
          <div v-for="c in rt.overviewCards" :key="c.asset_id" class="chip">
            <StateBadge :state="c.runtime.state" />
            <span class="muted">{{ c.asset_id }}</span>
          </div>
        </template>
      </header>
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.shell { display: flex; height: 100vh; }
.sidebar { width: 210px; background: #0a1621; border-right: 1px solid var(--ea-border); display: flex; flex-direction: column; flex-shrink: 0; }
.brand { padding: 18px 16px; border-bottom: 1px solid var(--ea-border); display: flex; flex-direction: column; gap: 2px; }
.logo { width: 40px; height: 40px; border-radius: 10px; background: var(--ea-accent); display: grid; place-items: center; font-weight: 800; color: #04121f; margin-bottom: 6px; }
.title { font-size: 18px; font-weight: 700; }
.sub { font-size: 12px; color: var(--ea-text-muted); }
.nav { flex: 1; padding: 10px 8px; display: flex; flex-direction: column; gap: 2px; overflow-y: auto; }
.nav-item { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-radius: 8px; color: var(--ea-text-muted); text-decoration: none; font-size: 14px; }
.nav-item:hover { background: #1c3347; color: var(--ea-text); }
.nav-item.active { background: var(--ea-accent); color: #04121f; font-weight: 600; }
.role-toggle { padding: 12px; border-top: 1px solid var(--ea-border); }
.content { flex: 1; display: flex; flex-direction: column; overflow-y: auto; min-width: 0; }
.topbar { display: flex; align-items: center; gap: 12px; padding: 14px 20px; border-bottom: 1px solid var(--ea-border); }
.page-title { font-size: 18px; margin: 0; }
.spacer { flex: 1; }
.live-chip { font-size: 12px; }
.chip { display: flex; align-items: center; gap: 6px; }
</style>