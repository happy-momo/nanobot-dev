<script setup lang="ts">
// App shell (Task 4): rail + router-view + global QaDrawer + ToastHost.
//
// Owns the app-level concerns that outlive any single view:
//  * bootstrap / 401 re-entry (setUnauthorizedHandler → drop token → re-PIN →
//    re-bootstrap, carried over from the previous shell's flow)
//  * visibility resync: an industrial tablet sleeping for hours wakes with a
//    "connected" shell and frozen data → re-visibility with no SSE event for
//    >60s resyncs server truth (carry-forward fix from the Task 3 review)
//  * role folding: localStorage `ea_role` hides rail entries (pure UI, fix G)
//  * demo banner: red vertical DEMO flag at the rail top (spec §5.7)
//  * provide('openQa'): any view can slide the QA drawer open with an asset
//    context (architecture-level root fix for the old `?asset=` bug A)
//
// NO element-plus patterns: notifications go through stores/toast (ToastHost),
// dialogs through Reka (QaDrawer + later tasks' dialogs), icons from lucide.
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRuntime } from './stores/runtime'
import { api, setToken, setUnauthorizedHandler } from './api/http'
import { toast } from './stores/toast'
import AppRail from './components/AppRail.vue'
import QaDrawer from './components/QaDrawer.vue'
import ToastHost from './components/ToastHost.vue'
import type { AppRole } from './router'

const rt = useRuntime()
const route = useRoute()
const router = useRouter()

// ---------------------------------------------------------------- role fold --
const ROLE_KEY = 'ea_role'
const role = ref<AppRole>(localStorage.getItem(ROLE_KEY) === 'operator'
  ? 'operator'
  : 'engineer')

function setRole(r: AppRole) {
  if (role.value === r) return
  role.value = r
  localStorage.setItem(ROLE_KEY, r)
  toast(r === 'operator' ? '已切换为【操作工】视图' : '已切换为【工程师】视图')
}

// current route requires a hidden role (deep link while folded) → fold home
watch([role, () => route.name], () => {
  const gate = route.meta?.roleGate as { operator?: boolean; engineer?: boolean } | undefined
  if (gate && !gate[role.value]) router.replace({ name: 'overview' })
})

// ------------------------------------------------------------- narrow mode --
const narrow = ref(window.innerWidth < 900)
function onResize() { narrow.value = window.innerWidth < 900 }

// ---------------------------------------------------------------- QA drawer --
const qaOpen = ref(false)
const qaAssetId = ref<string | undefined>(undefined)
/** openQa(assetId?) — the injected hook every view calls (fix A). */
function openQa(assetId?: string) {
  qaAssetId.value = assetId || undefined
  qaOpen.value = true
}
provide('openQa', openQa)

// ------------------------------------------------------------- 401 re-entry --
function askPin(): Promise<string | null> {
  return Promise.resolve(window.prompt('请输入现场使用 PIN 码（访问控制）') || '')
}

// Mid-session 401 (server restarted without its secret, token expired): drop
// the stale token and re-run the PIN flow instead of letting every request
// 401-loop. Same shape as the previous shell, element-free.
setUnauthorizedHandler(async () => {
  try {
    rt.stopStreams()
    const pin = await askPin()
    if (!pin) return
    const r = await api.post<{ token?: string }>('/api/auth/pin', { pin })
    setToken(r?.token || '')
    await rt.bootstrap(async () => null)   // token already set; no second prompt
    await rt.loadOverview(true)
  } catch {
    // stay usable; the next 401 re-triggers the prompt
  }
})

// --------------------------------------------------------------- lifecycle --
function onVisibility() {
  if (document.visibilityState === 'visible' && rt.ready) {
    const staleMs = Date.now() - rt.lastEventAt
    // nothing received in 60s (or ever) → full resync; SSE auto-reconnects.
    // (Carry-forward fix: the previous shell had this listener but the Task 3
    // shell never re-registered it — a woken tablet kept stale data forever.)
    if (!rt.lastEventAt || staleMs > 60_000) {
      rt.loadOverview(true).catch(() => { /* retry on next visibility */ })
      rt.loadAlerts().catch(() => { /* retry on next visibility */ })
    }
  }
}

onMounted(async () => {
  window.addEventListener('resize', onResize)
  document.addEventListener('visibilitychange', onVisibility)
  await rt.bootstrap(askPin)
  // full snapshot with sparkline series once the shell is up
  await rt.loadOverview(true).catch(() => { /* streams resync on reconnect */ })
})

onBeforeUnmount(() => {
  rt.stopStreams()
  window.removeEventListener('resize', onResize)
  document.removeEventListener('visibilitychange', onVisibility)
})

// ------------------------------------------------------------- rail wiring --
// conn is 'online' | 'connecting' | 'reconnecting' from the store. The LIVE dot
// must tell the truth about DATA (spec §5.1: 断开时变灰 RECONNECTING), so an
// online stream whose last event is older than the freshness gate reads as
// reconnecting rather than LIVE.
// rt.nowSec (the store's 1s ticking clock) is read on the stale path so this
// computed re-evaluates every second — otherwise a silent-but-open stream
// stayed "实时" forever, because Date.now() alone is not a reactive dependency.
const connDisplay = computed(() => {
  if (rt.conn !== 'online') return rt.conn
  const staleMs = rt.nowSec * 1000 + 500 - rt.lastEventAt
  return staleMs > 60_000 ? 'reconnecting' : 'online'
})

function onNavigate(name: string) {
  router.push({ name })
}
</script>

<template>
  <div class="shell" :class="narrow ? 'mode-bottom' : 'mode-rail'">
    <AppRail
      :role="role"
      :alerts-unacked="rt.overallAlertCount"
      :conn="connDisplay"
      :demo="rt.demo"
      :last-event-at="rt.lastEventAt"
      :current="(route.name as string) || ''"
      @navigate="onNavigate"
      @open-qa="openQa()"
      @toggle-role="setRole(role === 'operator' ? 'engineer' : 'operator')"
    />

    <main class="content">
      <!-- bootstrap / load failure: honest failure surface with a retry -->
      <div v-if="rt.loadError" class="load-error">
        <span class="eyebrow">CONNECTION</span>
        <p class="le-msg">无法连接服务器：{{ rt.loadError }}</p>
        <button type="button" class="le-retry" @click="rt.retryBootstrap()">重试</button>
      </div>
      <router-view v-else />
    </main>
  </div>

  <QaDrawer :open="qaOpen" :asset-id="qaAssetId" @close="qaOpen = false" />
  <ToastHost />
</template>

<style scoped>
.shell { display: flex; height: 100vh; min-height: 0; }

.content { flex: 1; min-width: 0; min-height: 0; overflow: hidden auto;
  padding: 16px 20px 24px; max-width: 1520px; }

/* narrow: the rail becomes a bottom tab bar (AppRail's media query restyles
   itself; here we only stack the shell vertically) */
@media (max-width: 899.98px) {
  .shell { flex-direction: column; }
  .content { padding: 14px 12px 20px; }
}

.load-error { display: flex; flex-direction: column; align-items: center; gap: 10px;
  padding: 60px 20px; text-align: center; }
.le-msg { margin: 0; color: var(--tx); font-size: 13px; max-width: 420px; }
.le-retry { border: 1px solid var(--line-2); background: var(--bg2);
  color: var(--cyan); border-radius: var(--r-s); padding: 6px 18px; cursor: pointer;
  font-size: 12px; letter-spacing: 1px; }
.le-retry:hover { border-color: rgba(59, 224, 216, 0.25); background: var(--bg3); }
</style>
