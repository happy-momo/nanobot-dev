<script setup lang="ts">
// App shell: collapsible sidebar + topbar (title / demo badge / connection /
// alert count / role) + router view. The store owns all SSE streams — no extra
// connections here. New unacked alerts raise a notification (severity-following).
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import {
  DataBoard, Monitor as MonitorIcon, Bell, Document, ChatDotRound, Reading,
  Comment, Setting as SettingIcon, Tickets, Tools, Expand,
} from '@element-plus/icons-vue'
import { useRuntime } from './stores/runtime'
import { api, setToken, setUnauthorizedHandler } from './api/http'
import ConnStatus from './components/ConnStatus.vue'

const rt = useRuntime()
const route = useRoute()
const router = useRouter()

// role folding: 操作工 see operations; 工程师/管理 see everything.
const role = ref<'operator' | 'engineer'>(
  (localStorage.getItem('ea_role') as any) || 'engineer',
)
const isOp = computed(() => role.value === 'operator')

// sidebar collapse persisted per device
const collapsed = ref(localStorage.getItem('ea_sidebar') === '1')
function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('ea_sidebar', collapsed.value ? '1' : '0')
}

// narrow screens: sidebar becomes a drawer
const narrow = ref(window.innerWidth < 900)
const drawerOpen = ref(false)
function onResize() { narrow.value = window.innerWidth < 900 }
window.addEventListener('resize', onResize)

const nav = computed(() => [
  { to: '/', label: '总览', key: 'dashboard', basic: true, icon: DataBoard },
  { to: '/monitor', label: '实时监控', key: 'monitor', basic: true, icon: MonitorIcon },
  { to: '/alerts', label: '告警中心', key: 'alerts', basic: true, icon: Bell, badge: rt.overallAlertCount },
  { to: '/reports', label: '深度报告', key: 'reports', opOnly: true, icon: Document },
  { to: '/qa', label: '智能问答', key: 'qa', opOnly: true, icon: ChatDotRound },
  { to: '/knowledge', label: '知识库', key: 'knowledge', opOnly: true, icon: Reading },
  { to: '/feedback', label: '反馈', key: 'feedback', basic: true, icon: Comment },
  { to: '/assets', label: '设备管理', key: 'assets', opOnly: true, icon: Tools },
  { to: '/runlog', label: '审计', key: 'runlog', opOnly: true, icon: Tickets },
  { to: '/settings', label: '系统设置', key: 'settings', opOnly: true, icon: SettingIcon },
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
  } catch {
    return ''
  }
}

// Mid-session 401 (server restarted without its secret, token expired): drop the
// stale token and re-run the PIN flow instead of letting every request 401-loop.
setUnauthorizedHandler(async () => {
  try {
    rt.stopStreams()
    const pin = await askPin()
    if (!pin) return
    const r = await api.post<any>('/api/auth/pin', { pin })
    setToken(r?.data?.token || '')
    await rt.bootstrap(async () => null)  // token already set; no second prompt
    await rt.loadOverview(true)
  } catch {
    // keep the app usable; next 401 will re-trigger the prompt
  }
})

onMounted(async () => {
  await rt.bootstrap(() => askPin())
  await rt.loadOverview(true)  // with sparkline series
})

onBeforeUnmount(() => {
  rt.stopStreams()
  window.removeEventListener('resize', onResize)
})

// new unacked alerts → notification (severity-following), click jumps to Alerts
let lastAlertTs = 0
watch(() => rt.alerts.length, (n, o) => {
  if (o === undefined || n <= o) return
  const fresh = rt.alerts.filter((a) => !a.acked && a.ts > lastAlertTs)
  if (!fresh.length) return
  lastAlertTs = Math.max(...fresh.map((a) => a.ts))
  for (const a of fresh.slice(0, 3)) {
    const level = a.overall_level || 'warn'
    ElNotification({
      title: a.asset_id + (a.subject ? ` · ${a.subject}` : ''),
      message: a.headline || a.reason || '新告警',
      type: level === 'critical' ? 'error' : 'warning',
      duration: 6000,
      onClick: () => router.push('/alerts'),
    })
  }
})

function toggleRole() {
  role.value = role.value === 'operator' ? 'engineer' : 'operator'
  localStorage.setItem('ea_role', role.value)
  ElMessage.success(role.value === 'operator' ? '已切换为【操作工】视图' : '已切换为【工程师】视图')
}

function onNavClick() {
  if (narrow.value) drawerOpen.value = false
}
</script>

<template>
  <div class="shell">
    <!-- desktop sidebar / mobile drawer -->
    <aside v-if="!narrow" class="sidebar" :class="{ collapsed }">
      <div class="brand" @click="toggleSidebar" :title="collapsed ? '展开导航' : '收起导航'">
        <div class="logo">EA</div>
        <div v-if="!collapsed" class="brand-text">
          <div class="title">EdgeAdviser</div>
          <div class="sub">现场设备智能顾问</div>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="n in visibleNav"
          :key="n.key"
          :to="n.to"
          class="nav-item"
          :class="{ active: route.name === n.key }"
          :title="collapsed ? n.label : undefined"
        >
          <el-icon class="nav-icon"><component :is="n.icon" /></el-icon>
          <span v-if="!collapsed" class="nav-label">{{ n.label }}</span>
          <el-badge v-if="!collapsed && n.badge" :value="n.badge" class="nav-badge" />
        </router-link>
      </nav>
      <div class="role-toggle">
        <el-button size="small" round @click="toggleRole" class="role-btn">
          {{ isOp ? '操作工' : '工程师' }}视图
        </el-button>
      </div>
    </aside>

    <el-drawer v-if="narrow" v-model="drawerOpen" direction="ltr" size="220px"
               :with-header="false" class="ea-drawer">
      <div class="brand">
        <div class="logo">EA</div>
        <div class="brand-text">
          <div class="title">EdgeAdviser</div>
          <div class="sub">现场设备智能顾问</div>
        </div>
      </div>
      <nav class="nav">
        <router-link v-for="n in visibleNav" :key="n.key" :to="n.to" class="nav-item"
                     :class="{ active: route.name === n.key }" @click="onNavClick">
          <el-icon class="nav-icon"><component :is="n.icon" /></el-icon>
          <span class="nav-label">{{ n.label }}</span>
          <el-badge v-if="n.badge" :value="n.badge" class="nav-badge" />
        </router-link>
      </nav>
    </el-drawer>

    <main class="content">
      <header class="topbar">
        <el-icon v-if="narrow" class="burger" @click="drawerOpen = true"><Expand /></el-icon>
        <h1 class="page-title">{{ route.meta.title || '' }}</h1>
        <span v-if="rt.demo" class="demo-badge" title="演示模式：MockGateway + 模拟数据">DEMO</span>
        <div class="spacer" />
        <template v-if="rt.overviewCards.length">
          <span class="live-chip muted">{{ rt.overviewCards.length }} 台设备</span>
        </template>
        <el-badge :value="rt.overallAlertCount" :hidden="!rt.overallAlertCount"
                  class="alert-badge" title="未确认告警">
          <el-icon class="bell"><Bell /></el-icon>
        </el-badge>
        <ConnStatus />
      </header>
      <router-view v-if="!rt.loadError" />
      <div v-else class="load-error">
        <el-result icon="warning" title="无法连接服务器"
                   :sub-title="rt.loadError">
          <template #extra>
            <el-button type="primary" @click="rt.retryBootstrap()">重试</el-button>
          </template>
        </el-result>
      </div>
    </main>
  </div>
</template>

<style scoped>
.shell { display: flex; height: 100vh; }
.sidebar {
  width: 210px; background: var(--ea-bg-deep);
  border-right: 1px solid var(--ea-border);
  display: flex; flex-direction: column; flex-shrink: 0;
  transition: width var(--ea-t-med);
}
.sidebar.collapsed { width: 64px; }
.brand {
  padding: 16px; border-bottom: 1px solid var(--ea-border);
  display: flex; align-items: center; gap: 10px; cursor: pointer; min-height: 72px;
}
.logo {
  width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0;
  background: var(--ea-grad-accent); display: grid; place-items: center;
  font-weight: 800; color: #04121f;
}
.brand-text { display: flex; flex-direction: column; gap: 1px; overflow: hidden; white-space: nowrap; }
.title { font-size: 17px; font-weight: 700; }
.sub { font-size: 11px; color: var(--ea-text-muted); }
.nav { flex: 1; padding: 10px 8px; display: flex; flex-direction: column; gap: 2px; overflow-y: auto; }
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; border-radius: var(--ea-r-sm);
  color: var(--ea-text-muted); text-decoration: none; font-size: 14px;
  position: relative; transition: background var(--ea-t-fast), color var(--ea-t-fast);
}
.nav-item:hover { background: var(--ea-panel-soft); color: var(--ea-text); }
.nav-item.active {
  background: var(--ea-accent-soft); color: var(--ea-text); font-weight: 600;
}
.nav-item.active::before {
  content: ''; position: absolute; left: 0; top: 20%; bottom: 20%; width: 3px;
  border-radius: 2px; background: var(--ea-grad-accent);
}
.nav-icon { flex-shrink: 0; font-size: 17px; }
.nav-label { overflow: hidden; white-space: nowrap; }
.nav-badge { margin-left: auto; }
.role-toggle { padding: 12px; border-top: 1px solid var(--ea-border); }
.role-btn { width: 100%; }
.collapsed .role-toggle { padding: 8px; }
.collapsed .nav-item { justify-content: center; padding: 12px 0; }

.load-error { padding: 60px 20px; }

.content { flex: 1; display: flex; flex-direction: column; overflow-y: auto; min-width: 0; }
.topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 20px; border-bottom: 1px solid var(--ea-border);
  position: sticky; top: 0; z-index: 5;
  background: var(--ea-panel-glass); backdrop-filter: blur(10px);
}
.burger { font-size: 20px; cursor: pointer; }
.page-title { font-size: 17px; margin: 0; font-weight: 600; }
.demo-badge {
  font-size: 10px; font-weight: 800; letter-spacing: 1px;
  color: #04121f; background: #f5b14c; border-radius: 4px; padding: 2px 6px;
}
.spacer { flex: 1; }
.live-chip { font-size: 12px; }
.bell { font-size: 18px; color: var(--ea-text-muted); cursor: pointer; }
.alert-badge :deep(.el-badge__content) { background: #f4554f; }
</style>
