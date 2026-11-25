<script setup lang="ts">
// AppRail — the 52px observatory navigation column (spec §2) + its <900px
// bottom tab-bar form (touch targets ≥44px, spec §8). Purely presentational:
// it receives role / conn / alerts / demo as props and reports intent through
// emits; the app-level state (SSE, store, role persistence) lives in App.vue.
//
// Desktop: icon buttons 32px, active = --bg2 fill + 1px inset cyan-25% border +
// cyan icon; brand symbol ◈ on top; bottom block = LIVE conn + unacked-alert
// badge (crit red when >0) + vertical EDGE ADVISER wordmark.
// Narrow: same entries as a bottom bar (icons 44px+), no wordmark.
import { computed } from 'vue'
import { Gauge, Activity, BellRing, MessagesSquare, ScrollText, Settings,
  UserCog } from '@lucide/vue'
import { RAIL_NAV, type AppRole } from '../router'
import type { Conn } from '../stores/runtime'
import ConnStatus from './ConnStatus.vue'

type RailIcon = 'overview' | 'assets' | 'alerts' | 'qa' | 'audit' | 'settings'

const props = withDefaults(
  defineProps<{
    role: AppRole
    alertsUnacked: number
    conn: Conn
    demo?: boolean
    lastEventAt?: number
    /** current route name ('asset-detail' folds onto the 'asset' entry) */
    current?: string
  }>(),
  { demo: false, lastEventAt: undefined, current: '' },
)

const emit = defineEmits<{
  (e: 'navigate', routeName: string): void
  (e: 'openQa'): void
  (e: 'toggleRole'): void
}>()

const ICONS: Record<RailIcon, typeof Gauge> = {
  overview: Gauge,
  assets: Activity,
  alerts: BellRing,
  qa: MessagesSquare,
  audit: ScrollText,
  settings: Settings,
}

const entries = computed(() =>
  RAIL_NAV
    .filter((n) => n.roles[props.role])
    .map((n) => ({
      name: n.name,
      label: n.label,
      icon: ICONS[n.icon],
      // 'qa' is the drawer opener (never navigates); the rest are routes
      isQa: n.icon === 'qa',
    })),
)

// badge: crit red whenever there is at least one unacked alert (spec §5.7)
const badgeN = computed(() => (props.alertsUnacked > 99 ? '99+' : props.alertsUnacked || ''))
const badgeHot = computed(() => props.alertsUnacked > 0)

// active entry: the current route folded onto its rail entry (the asset detail
// route has its own name, but the rail highlights the single 资产 icon)
const activeName = computed(() => {
  const cur = props.current
  if (cur === 'asset-detail') return 'asset'
  return cur
})

/** component vocabulary → ConnStatus's prop vocabulary */
const connProp = computed<'online' | 'reconnecting' | 'connecting'>(() =>
  props.conn === 'online' ? 'online'
    : props.conn === 'reconnecting' ? 'reconnecting'
      : 'connecting',
)

function onEntry(name: string, isQa: boolean) {
  if (isQa) emit('openQa')
  else emit('navigate', name)
}
</script>

<template>
  <nav class="rail" aria-label="主导航">
    <div class="rail-top">
      <span v-if="demo" class="demo-flag" title="演示模式：模拟数据">DEMO</span>
      <span class="brand" title="EdgeAdviser" aria-hidden="true">◈</span>
    </div>

    <div class="rail-entries">
      <button
        v-for="e in entries"
        :key="e.name"
        type="button"
        class="rail-btn"
        :class="{ active: e.name === activeName }"
        :aria-current="e.name === activeName ? 'page' : undefined"
        :aria-label="e.label"
        :title="e.label"
        @click="onEntry(e.name, e.isQa)"
      >
        <component :is="e.icon" class="rail-ic" :size="17" :stroke-width="1.5" />
        <span v-if="e.name === 'alerts' && badgeHot" class="rail-badge mono">{{ badgeN }}</span>
      </button>
    </div>

    <div class="rail-foot">
      <button
        type="button"
        class="role-btn"
        :aria-label="role === 'operator' ? '切换为工程师视图' : '切换为操作工视图'"
        :title="role === 'operator'
          ? '当前【操作工】视图 — 界面视图切换，非权限控制（点击切换）'
          : '当前【工程师】视图 — 界面视图切换，非权限控制（点击切换）'"
        @click="emit('toggleRole')"
      >
        <UserCog :size="15" :stroke-width="1.5" aria-hidden="true" />
      </button>
      <ConnStatus :conn="connProp" :last-event-at="lastEventAt" class="rail-conn" />
      <span class="wordmark" aria-hidden="true">EDGE ADVISER</span>
    </div>
  </nav>
</template>

<!-- desktop layout; the narrow bottom-tab form lives in the unscoped block below -->
<style scoped>
.rail {
  width: 52px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  background: var(--bg0);
  border-right: 1px solid var(--line);
  flex-shrink: 0;
  min-height: 0;
}

.rail-top { position: relative; display: flex; flex-direction: column; align-items: center;
  padding: 8px 0 4px; }
.demo-flag { writing-mode: vertical-rl; text-orientation: upright;
  font-family: var(--font-mono); font-size: 8px; letter-spacing: 1px; font-weight: 600;
  background: var(--crit); color: var(--bg0);
  border-radius: 2px; padding: 4px 1px; margin-bottom: 4px; line-height: 1; }
.brand { color: var(--cyan); font-size: 17px; line-height: 1; }

.rail-entries { display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 10px 0; flex: 1; min-height: 0; overflow: hidden auto; scrollbar-width: none; }
.rail-entries::-webkit-scrollbar { display: none; }

.rail-btn { position: relative; width: 32px; height: 32px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid transparent; border-radius: var(--r-s); background: transparent;
  color: var(--tx-dim); cursor: pointer; padding: 0; }
.rail-btn:hover { background: var(--bg2); color: var(--tx); border-color: var(--line); }
.rail-btn.active, .rail-btn[aria-current="page"] { background: var(--bg2); color: var(--cyan);
  border-color: rgba(59, 224, 216, 0.25); border-style: inset; }

.rail-badge { position: absolute; top: 2px; right: 2px; min-width: 13px; height: 13px;
  padding: 0 2px; border-radius: 7px; background: var(--crit); color: var(--bg0);
  font-size: 8px; line-height: 13px; font-weight: 600; text-align: center;
  font-variant-numeric: tabular-nums; pointer-events: none; }

.rail-foot { display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 6px 0 10px; }
.role-btn { width: 32px; height: 32px; display: inline-flex; align-items: center;
  justify-content: center; border: 1px solid var(--line); border-radius: var(--r-s);
  background: transparent; color: var(--tx-dim); cursor: pointer; padding: 0; }
.role-btn:hover { background: var(--bg2); color: var(--tx); }
.rail-conn { max-width: 100%; }
.rail-conn :deep(.cs-label) { display: none; }
.rail-conn :deep(.cs-age) { display: none; }
.rail-conn :deep(.cs) { padding: 3px 6px; }
.rail-conn :deep(.cs-dot) { width: 6px; height: 6px; }
.wordmark { writing-mode: vertical-rl; text-orientation: mixed;
  font-size: 7px; letter-spacing: 2px; color: var(--tx-dim); text-transform: uppercase;
  user-select: none; }
</style>

<style>
/* bottom tab-bar form <900px — an unscoped block because the mode class lives
   on App.vue's .shell (AppRail cannot media-query its own parent), while the
   media query itself must live in the component per the Task 4 brief.
   Touch targets 44px+ (spec §8). */
@media (max-width: 899.98px) {
  .mode-bottom .rail {
    width: 100%;
    height: 58px;
    order: 2;
    flex-direction: row;
    align-items: stretch;
    border-right: none;
    border-top: 1px solid var(--line);
  }
  .mode-bottom .rail-top { position: absolute; inset: auto auto 100% 0; padding: 0;
    flex-direction: row; gap: 6px; align-items: center; }
  .mode-bottom .brand { display: none; }
  .mode-bottom .demo-flag { writing-mode: horizontal-tb; text-orientation: initial;
    margin-bottom: 0; padding: 1px 5px; font-size: 8px; }
  .mode-bottom .rail-entries { flex-direction: row; justify-content: space-around;
    padding: 0 4px; overflow: visible; }
  .mode-bottom .rail-btn { width: 44px; height: 44px; align-self: center; }
  .mode-bottom .rail-btn .rail-ic { width: 20px; height: 20px; }
  .mode-bottom .rail-foot { flex-direction: row; padding: 0 8px; gap: 6px;
    align-items: center; }
  .mode-bottom .wordmark { display: none; }
  .mode-bottom .rail-conn { display: none; }
}
</style>
