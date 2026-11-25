import { createRouter, createWebHistory } from 'vue-router'
import type { RouteLocationGeneric, RouteRecordRaw } from 'vue-router'

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

/** The 6 rail entries in display order; AppRail renders from this list. */
export const RAIL_NAV: Array<{
  name: string
  label: string
  icon: 'overview' | 'assets' | 'alerts' | 'qa' | 'audit' | 'settings'
}> = [
  { name: 'overview', label: '总览', icon: 'overview' },
  { name: 'asset', label: '资产', icon: 'assets' },
  { name: 'alerts', label: '告警', icon: 'alerts' },
  { name: 'qa', label: '问答', icon: 'qa' },
  { name: 'audit', label: '审计', icon: 'audit' },
  { name: 'settings', label: '设置', icon: 'settings' },
]

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'overview',
    component: () => import('../views/Overview.vue'),
    meta: { title: '总览' },
  },
  {
    // no default param: the rail's entry lands here and the view picks the
    // first asset from the runtime store — a client-side guess must not bake a
    // fake :assetId into the URL
    path: '/asset',
    name: 'asset',
    component: () => import('../views/AssetDetail.vue'),
    meta: { title: '资产' },
  },
  {
    path: '/asset/:assetId',
    name: 'asset-detail',
    component: () => import('../views/AssetDetail.vue'),
    meta: { title: '资产' },
  },
  {
    path: '/alerts',
    name: 'alerts',
    component: () => import('../views/Alerts.vue'),
    meta: { title: '告警中心' },
  },
  {
    path: '/qa',
    name: 'qa',
    component: () => import('../views/Qa.vue'),
    meta: { title: '问答' },
  },
  {
    path: '/audit',
    name: 'audit',
    component: () => import('../views/Audit.vue'),
    meta: { title: '审计' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/Settings.vue'),
    meta: { title: '设置' },
  },
  // ─── legacy deep-link redirects (0.4.x bookmarks) ──────────────────────────
  // The IA collapsed 11 pages into 5 + asset tabs; old bookmarks must land on
  // the nearest replacement instead of the near-blank NotFound panel.
  { path: '/monitor', redirect: { name: 'asset' } },
  { path: '/monitor/:assetId', redirect: (to: RouteLocationGeneric) =>
      ({ name: 'asset-detail', params: { assetId: to.params.assetId } }) },
  { path: '/asset-hub', redirect: { name: 'asset' } },
  { path: '/asset-hub/:assetId', redirect: (to: RouteLocationGeneric) =>
      ({ name: 'asset-detail', params: { assetId: to.params.assetId } }) },
  { path: '/reports', redirect: { name: 'asset' } },
  { path: '/knowledge', redirect: { name: 'asset' } },
  { path: '/feedback', redirect: { name: 'asset' } },
  { path: '/assets', redirect: { name: 'asset' } },
  { path: '/runlog', redirect: { name: 'audit' } },
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
