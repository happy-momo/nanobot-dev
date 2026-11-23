import { createRouter, createWebHistory } from 'vue-router'

// TEMPORARY (Task 1 scaffold): old views/ were deleted, so each route points at
// the same inline stub. Paths, names and meta titles are unchanged — later
// tasks swap `component` back to `() => import('../views/X.vue')` per view.
const stub = () => Promise.resolve({ render: () => null })

const routes = [
  { path: '/', name: 'dashboard', component: stub, meta: { title: '总览' } },
  { path: '/asset/:assetId', name: 'asset-hub', component: stub, meta: { title: '设备中心' } },
  { path: '/asset-hub', name: 'asset-hub-picker', component: stub, meta: { title: '设备中心' } },
  { path: '/monitor/:assetId?', name: 'monitor', component: stub, meta: { title: '实时监控' } },
  { path: '/alerts', name: 'alerts', component: stub, meta: { title: '告警中心' } },
  { path: '/reports', name: 'reports', component: stub, meta: { title: '深度报告' } },
  { path: '/qa', name: 'qa', component: stub, meta: { title: '智能问答' } },
  { path: '/knowledge/:assetId?', name: 'knowledge', component: stub, meta: { title: '知识库' } },
  { path: '/feedback', name: 'feedback', component: stub, meta: { title: '反馈' } },
  { path: '/assets', name: 'assets', component: stub, meta: { title: '设备管理' } },
  { path: '/runlog', name: 'runlog', component: stub, meta: { title: '审计' } },
  { path: '/settings', name: 'settings', component: stub, meta: { title: '系统设置' } },
]

export const router = createRouter({
  history: createWebHistory('/dist/'),
  routes,
})
