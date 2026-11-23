import { createRouter, createWebHistory } from 'vue-router'

// Route table per the new IA (spec §4): 5 top-level pages + the asset detail
// page. The old /reports /knowledge /assets /feedback /monitor /asset-hub
// top-level entries are gone (their content lives inside asset detail tabs, the
// alerts page, or the QA drawer). The catchall keeps legacy deep-links from
// dying in a router-warning void: it renders NotFound (blank panel) so a
// bookmarked old URL at least paints the shell.
//
// Every view is a lazy import. The view FILES exist as minimal placeholders
// (Task 4 step 1: 占位视图先建 views/*.vue 最小骨架逐个 Task 填充) — later
// tasks fill them in (Overview T5, AssetDetail T6/T7, Qa T8, Alerts T9,
// Audit/Settings T10).

export type AppRole = 'operator' | 'engineer'

export interface RouteRoles {
  /** roles allowed to SEE this rail entry (pure UI folding, not a permission
   * gate — the route itself stays reachable, spec §4 + fix G). */
  operator?: boolean
  engineer?: boolean
}

/** The 6 rail entries in display order; AppRail renders from this list. */
export const RAIL_NAV: Array<{
  name: string
  label: string
  icon: 'overview' | 'assets' | 'alerts' | 'qa' | 'audit' | 'settings'
  roles: RouteRoles
}> = [
  { name: 'overview', label: '总览', icon: 'overview', roles: { operator: true, engineer: true } },
  { name: 'asset', label: '资产', icon: 'assets', roles: { operator: true, engineer: true } },
  { name: 'alerts', label: '告警', icon: 'alerts', roles: { operator: true, engineer: true } },
  { name: 'qa', label: '问答', icon: 'qa', roles: { engineer: true } },
  { name: 'audit', label: '审计', icon: 'audit', roles: { engineer: true } },
  { name: 'settings', label: '设置', icon: 'settings', roles: { engineer: true } },
]

/** meta.roleGate derives from RAIL_NAV so the rail and guards cannot drift. */
const roleGate = Object.fromEntries(
  RAIL_NAV.map((n) => [n.name, n.roles]),
) as Record<string, RouteRoles>

const routes = [
  {
    path: '/',
    name: 'overview',
    component: () => import('../views/Overview.vue'),
    meta: { title: '总览', roleGate: roleGate.overview },
  },
  {
    // no default param: the rail's entry lands here and the view picks the
    // first asset from the runtime store — a client-side guess must not bake a
    // fake :assetId into the URL
    path: '/asset',
    name: 'asset',
    component: () => import('../views/AssetDetail.vue'),
    meta: { title: '资产', roleGate: roleGate.asset },
  },
  {
    path: '/asset/:assetId',
    name: 'asset-detail',
    component: () => import('../views/AssetDetail.vue'),
    meta: { title: '资产', roleGate: roleGate.asset },
  },
  {
    path: '/alerts',
    name: 'alerts',
    component: () => import('../views/Alerts.vue'),
    meta: { title: '告警中心', roleGate: roleGate.alerts },
  },
  {
    path: '/qa',
    name: 'qa',
    component: () => import('../views/Qa.vue'),
    meta: { title: '问答', roleGate: roleGate.qa },
  },
  {
    path: '/audit',
    name: 'audit',
    component: () => import('../views/Audit.vue'),
    meta: { title: '审计', roleGate: roleGate.audit },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/Settings.vue'),
    meta: { title: '设置', roleGate: roleGate.settings },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('../views/NotFound.vue'),
    meta: { title: '' },
  },
]

export const router = createRouter({
  history: createWebHistory('/dist/'),
  routes,
})

// document.title per page — one central hook instead of per-view boilerplate
router.afterEach((to) => {
  const t = to.meta?.title as string | undefined
  document.title = t ? `${t} · EdgeAdviser` : 'EdgeAdviser'
})
