import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '总览' } },
  { path: '/asset/:assetId', name: 'asset-hub', component: () => import('../views/AssetHub.vue'), meta: { title: '设备中心' } },
  { path: '/asset-hub', name: 'asset-hub-picker', component: () => import('../views/AssetHub.vue'), meta: { title: '设备中心' } },
  { path: '/monitor/:assetId?', name: 'monitor', component: () => import('../views/Monitor.vue'), meta: { title: '实时监控' } },
  { path: '/alerts', name: 'alerts', component: () => import('../views/Alerts.vue'), meta: { title: '告警中心' } },
  { path: '/reports', name: 'reports', component: () => import('../views/Reports.vue'), meta: { title: '深度报告' } },
  { path: '/qa', name: 'qa', component: () => import('../views/Qa.vue'), meta: { title: '智能问答' } },
  { path: '/knowledge/:assetId?', name: 'knowledge', component: () => import('../views/Knowledge.vue'), meta: { title: '知识库' } },
  { path: '/feedback', name: 'feedback', component: () => import('../views/Feedback.vue'), meta: { title: '反馈' } },
  { path: '/assets', name: 'assets', component: () => import('../views/Assets.vue'), meta: { title: '设备管理' } },
  { path: '/runlog', name: 'runlog', component: () => import('../views/Runlog.vue'), meta: { title: '审计' } },
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue'), meta: { title: '系统设置' } },
]

export const router = createRouter({
  history: createWebHistory('/dist/'),
  routes,
})