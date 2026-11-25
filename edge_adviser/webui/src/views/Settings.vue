<script setup lang="ts">
// Settings — asset connector configuration followed by three read-only panels.
//  * 服务状态: /api/version (版本/DEMO/资产数/PIN) + /api/health (健康/
//    调度器/心跳/网关/有快照资产数)
//  * 运行观测: /api/stats — provider 成功率/调用数/gated_refusals/MTTA/
//    磁盘/每资产快照数
//  * 运行参数: /api/config 只读 (cron/门禁/PIN/工作区) — an industrial HMI
//    reads first; editing live config happens offline (change + restart)
// The global DEMO banner (red text across the app top when demo=true) is
// wired here per the brief — App.vue passes rt.demo already to the rail's
// vertical flag; this adds the horizontal top banner.
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { fetchConfig, fetchHealth, fetchStats, fetchVersion } from '../api'
import { useRuntime } from '../stores/runtime'
import ObsPanel from '../components/ObsPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import ConfigurationManager from '../components/ConfigurationManager.vue'

const rt = useRuntime()

const version = ref<Record<string, any> | null>(null)
const health = ref<Record<string, any> | null>(null)
const stats = ref<Record<string, any> | null>(null)
const config = ref<Record<string, any> | null>(null)
const loadError = ref('')
const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    // stats is supplementary (a fresh workspace may have nothing to aggregate)
    const [v, h, s, c] = await Promise.all([
      fetchVersion(), fetchHealth(), fetchStats().catch(() => null), fetchConfig(),
    ])
    version.value = v || null
    health.value = h || null
    stats.value = s || null
    config.value = c || null
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '设置信息加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => { load() })

// 60s refresh: heartbeat age / tick age are the only live numbers here
let timer: number | null = null
onMounted(() => { timer = window.setInterval(load, 60_000) })
onBeforeUnmount(() => { if (timer !== null) window.clearInterval(timer) })

// ------------------------------------------------------------- formatting --
function fmtBytes(b: number | null | undefined): string {
  if (typeof b !== 'number' || !Number.isFinite(b)) return '—'
  if (b < 1024) return `${b}B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)}MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(2)}GB`
}
function fmtS(v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  if (v < 60) return `${Math.round(v)}s`
  if (v < 3600) return `${Math.round(v / 60)}m`
  return `${(v / 3600).toFixed(1)}h`
}
function fmtPct(v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  return `${Math.round(v * 100)}%`
}

const providers = computed(() => stats.value?.providers || null)
const alertsMeta = computed(() => stats.value?.alerts || null)
const storage = computed(() => stats.value?.storage || null)
const perAsset = computed<Array<Record<string, any>>>(() => stats.value?.assets || [])
const sched = computed(() => health.value?.scheduler || null)

/** cron strings from config (deep_report_cron / learn_cron) */
const crons = computed(() => [
  { k: 'deep_report_cron', label: '深度报告' },
  { k: 'learn_cron', label: '学习' },
].map((x) => ({ ...x, v: config.value?.[x.k] || '未配置' })))

const PARAM_ROWS = computed(() => [
  { k: 'monitor_interval_s', label: '巡检间隔', v: config.value?.monitor_interval_s != null ? `${config.value.monitor_interval_s}s` : '—' },
  { k: 'data_freshness_s', label: '新鲜度门禁', v: config.value?.data_freshness_s != null ? `${config.value.data_freshness_s}s` : '—' },
  { k: 'coverage_threshold', label: '覆盖率阈值', v: config.value?.coverage_threshold ?? '—' },
  { k: 'max_empty_windows', label: '离线判定空窗数', v: config.value?.max_empty_windows ?? '—' },
  { k: 'learn_gate', label: '学习门禁', v: (config.value?.learn_gate || '—').toUpperCase() },
  { k: 'memory_conflict_policy', label: '冲突策略', v: config.value?.memory_conflict_policy || '—' },
  { k: 'hmi_pin_enabled', label: 'PIN 访问', v: config.value?.hmi_pin_enabled ? '已启用' : '未启用' },
  { k: 'workspace', label: '工作区', v: config.value?.workspace || '—' },
])
</script>

<template>
  <div class="page st-page">
    <!-- spec §5.6: demo=true → global red DEMO banner across the app top -->
    <div v-if="rt.demo" class="st-demo-banner" role="status">
      演示环境 · 使用模拟数据，不代表真实设备状态
    </div>

    <EmptyState v-if="loadError" label="设置信息加载失败" :hint="loadError" />

    <div v-else class="st-grid">
      <ConfigurationManager />

      <!-- ─── 服务状态 ─────────────────────────────────────────────────────── -->
      <ObsPanel title="服务状态">
        <div class="st-cells">
          <div class="st-cell">
            <span class="st-k">版本</span>
            <span class="st-v mono">{{ version?.version || '—' }}</span>
          </div>
          <div class="st-cell">
            <span class="st-k">健康</span>
            <span class="st-v" :style="{ color: health?.status === 'ok' ? 'var(--ok)' : 'var(--warn)' }">
              {{ health?.status === 'ok' ? '正常' : (health?.status || '—') }}
            </span>
          </div>
          <div class="st-cell">
            <span class="st-k">资产</span>
            <span class="st-v mono">
              {{ health?.assets_with_snapshots ?? '—' }} / {{ health?.assets ?? '—' }} 有快照
            </span>
          </div>
          <div class="st-cell">
            <span class="st-k">调度器</span>
            <span class="st-v" :style="{ color: sched?.running ? 'var(--ok)' : 'var(--crit)' }">
              {{ sched?.running ? '运行中' : '未运行' }}
            </span>
            <span v-if="sched?.last_tick_age_s != null" class="st-sub mono">
              上次运行于 {{ Math.round(sched.last_tick_age_s) }} 秒前
            </span>
          </div>
          <div class="st-cell">
            <span class="st-k">网关</span>
            <span class="st-v">{{ health?.gateway || '—' }}</span>
          </div>
          <div class="st-cell">
            <span class="st-k">PIN</span>
            <span class="st-v">{{ version?.pin_enabled ? '已启用' : '未启用' }}</span>
          </div>
        </div>
      </ObsPanel>

      <!-- ─── 运行观测 ─────────────────────────────────────────────────────── -->
      <ObsPanel title="运行统计">
        <div class="st-cells">
          <div class="st-cell">
            <span class="st-k">模型成功率</span>
            <span class="st-v mono" :style="{ color: (providers?.success_rate ?? 0) >= 0.9 ? 'var(--ok)' : 'var(--warn)' }">
              {{ fmtPct(providers?.success_rate) }}</span>
            <span class="st-sub mono">{{ providers?.ok ?? '—' }} / {{ providers?.calls ?? '—' }} 次调用</span>
          </div>
          <div class="st-cell">
            <span class="st-k">门禁拦截</span>
            <span class="st-v mono">{{ providers?.gated_refusals ?? '—' }}</span>
          </div>
          <div class="st-cell">
            <span class="st-k">MTTA</span>
            <span class="st-v mono">{{ fmtS(alertsMeta?.mtta_s) }}</span>
            <span class="st-sub mono">告警 {{ alertsMeta?.total ?? '—' }} · 未确认 {{ alertsMeta?.unacked ?? '—' }}</span>
          </div>
          <div class="st-cell">
            <span class="st-k">工作区</span>
            <span class="st-v mono">{{ fmtBytes(storage?.workspace_bytes) }}</span>
          </div>
          <div class="st-cell">
            <span class="st-k">磁盘剩余</span>
            <span class="st-v mono">{{ fmtBytes(storage?.disk_free_bytes) }}</span>
          </div>
          <div class="st-cell">
            <span class="st-k">平均耗时</span>
            <span class="st-v mono">{{ fmtS(providers?.avg_elapsed_s) }}</span>
          </div>
        </div>

        <div v-if="perAsset.length" class="st-perasset">
          <div v-for="a in perAsset" :key="a.asset_id" class="st-pa-row">
            <span class="st-pa-name">{{ rt.assetById(a.asset_id)?.name || a.asset_id }}</span>
            <span class="st-pa-v mono">快照 {{ a.snapshot_count ?? '—' }}/{{ a.snapshot_cap ?? '—' }}</span>
            <span class="st-pa-v mono">审计 {{ a.runlog_rows ?? '—' }}</span>
            <span class="st-pa-v mono">磁盘 {{ fmtBytes(a.disk_bytes) }}</span>
          </div>
        </div>
      </ObsPanel>

      <!-- ─── 运行参数（只读） ─────────────────────────────────────────────── -->
      <ObsPanel title="运行参数">
        <div class="st-cells">
          <div v-for="row in PARAM_ROWS" :key="row.k" class="st-cell">
            <span class="st-k">{{ row.label }}</span>
            <span class="st-v mono">{{ row.v }}</span>
          </div>
          <div v-for="c in crons" :key="c.k" class="st-cell">
            <span class="st-k">{{ c.label }}计划</span>
            <span class="st-v mono">{{ c.v }}</span>
          </div>
        </div>
        <p class="st-note">运行参数只读展示 — 修改需在服务器编辑配置后重启服务。</p>
      </ObsPanel>
    </div>
  </div>
</template>

<style scoped>
.st-page { display: flex; flex-direction: column; gap: 14px; padding: 16px 18px;
  min-height: 0; flex: 1; overflow: hidden auto; }

.st-demo-banner { border: 1px solid var(--crit); background: color-mix(in srgb, var(--crit) 10%, var(--bg1));
  color: var(--crit); font-size: 12px; letter-spacing: .14em; font-weight: 700;
  padding: 8px 14px; border-radius: var(--r-s); text-align: center; }

.st-grid { display: flex; flex-direction: column; gap: 14px; min-width: 0; }

.st-cells { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
.st-cell { display: flex; flex-direction: column; gap: 3px; padding: 10px 14px;
  border-bottom: 1px solid var(--line); border-right: 1px solid var(--line); min-width: 0; }
.st-cell:nth-last-child(-n+1) { border-bottom: 0; }
.st-k { font-size: 9.5px; letter-spacing: .1em; color: var(--tx-dim); }
.st-v { font-size: 12.5px; color: var(--tx-hi); overflow-wrap: anywhere; }
.st-sub { font-size: 10px; color: var(--tx-dim); font-variant-numeric: tabular-nums; }

.st-perasset { border-top: 1px solid var(--line); }
.st-pa-row { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
  padding: 7px 14px; border-bottom: 1px solid var(--line); }
.st-pa-row:last-child { border-bottom: 0; }
.st-pa-name { font-size: 11.5px; color: var(--tx-hi); min-width: 90px; }
.st-pa-v { font-size: 10.5px; color: var(--tx-dim); font-variant-numeric: tabular-nums; }

.st-note { margin: 0; padding: 8px 14px 10px; font-size: 10.5px;
  color: var(--tx-dim); border-top: 1px solid var(--line); line-height: 1.5; }

@media (max-width: 699.98px) {
  .st-cell { border-right: 0; }
}
</style>
