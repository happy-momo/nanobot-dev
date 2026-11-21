<script setup lang="ts">
// System settings: read-only view of the deterministic tuning knobs + provider
// status + a new service card (version/health/demo). Editing live config is
// deliberately out of the read-first HMI scope (change config then restart).
// This page exists so the field engineer can SEE the trustworthiness policy
// actually in force. Loading/error/retry states wired.
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()
const cfg = ref<any>(null)
const ver = ref<any>(null)
const health = ref<any>(null)
const loading = ref(false)
const errored = ref(false)

async function load() {
  loading.value = true
  errored.value = false
  try {
    const [c, v, h] = await Promise.all([
      api.get<any>('/api/config'),
      api.get<any>('/api/version'),
      api.get<any>('/api/health'),
    ])
    cfg.value = c
    ver.value = v
    health.value = h
  } catch {
    errored.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

const strategy = [
  { k: 'data_freshness_s', label: '数据新鲜度阈值(s)', hint: '超过该秒数视为数据过时，门禁拒绝模型' },
  { k: 'coverage_threshold', label: '覆盖率阈值', hint: '低于则判 PARTIAL，可信度降级' },
  { k: 'max_empty_windows', label: '连续空窗判定', hint: '连续 N 个空窗口判 OFFLINE(断连)' },
  { k: 'learn_gate', label: '学习闸门', hint: 'off/soft/strict —— 坏数据是否写回 MEMORY.md' },
  { k: 'memory_index', label: '记忆检索', hint: 'off=最近优先 / keywords=关键词相关' },
  { k: 'memory_conflict_policy', label: '记忆冲突策略', hint: 'off=忽略 / flag=标记冲突与陈旧' },
  { k: 'memory_decay_s', label: '记忆衰减(s)', hint: '超过则视为陈旧知识被降权' },
  { k: 'runlog_enabled', label: '审计账本', hint: '是否记录 runlog.jsonl' },
]

function val(k: string) {
  return cfg.value?.[k]
}

const providerKeys = computed(() => (cfg.value?.providers ? Object.keys(cfg.value.providers) : []))
</script>

<template>
  <div class="page">
    <PageHeader title="系统设置" subtitle="确定性策略视图（只读）— 修改需编辑配置文件并重启服务">
      <el-button size="small" @click="load" :loading="loading">刷新</el-button>
    </PageHeader>

    <div v-if="loading" style="padding:40px;text-align:center" class="muted">加载中…</div>

    <EmptyState v-else-if="errored" icon="⚠️" text="配置加载失败"
                hint="请确认后端服务正在运行">
      <el-button size="small" type="primary" @click="load">重试</el-button>
    </EmptyState>

    <div v-else-if="cfg" class="grid">
      <!-- service status -->
      <el-card shadow="never" class="wide">
        <template #header><b>服务状态</b></template>
        <div class="svc">
          <div class="svc-item">
            <span class="muted small">版本</span>
            <span class="num">{{ ver?.version || '—' }}</span>
          </div>
          <div class="svc-item">
            <span class="muted small">模式</span>
            <el-tag v-if="ver?.demo" type="warning" size="small">DEMO（MockGateway）</el-tag>
            <el-tag v-else type="success" size="small">生产</el-tag>
          </div>
          <div class="svc-item">
            <span class="muted small">在册资产</span>
            <span class="num">{{ ver?.assets ?? '—' }}</span>
          </div>
          <div class="svc-item">
            <span class="muted small">有快照的资产</span>
            <span class="num">{{ health?.assets_with_snapshots ?? '—' }} / {{ health?.assets ?? '—' }}</span>
          </div>
          <div class="svc-item">
            <span class="muted small">监控周期</span>
            <span class="num">{{ ver?.monitor_interval_s ?? cfg.monitor_interval_s }}s</span>
          </div>
          <div class="svc-item">
            <span class="muted small">调度器</span>
            <el-tag :type="health?.scheduler?.running ? 'success' : 'danger'" size="small">
              {{ health?.scheduler?.running ? '运行中' : '未运行/已停止' }}
            </el-tag>
            <span v-if="health?.scheduler?.last_tick_age_s != null" class="muted small">
              上次 tick {{ Math.round(health.scheduler.last_tick_age_s) }}s 前
            </span>
          </div>
          <div class="svc-item">
            <span class="muted small">健康检查</span>
            <el-tag :type="health?.status === 'ok' ? 'success' : 'warning'" size="small">
              {{ health?.status || '—' }}
            </el-tag>
          </div>
          <div class="svc-item">
            <span class="muted small">网关</span>
            <span class="num">{{ health?.gateway || '—' }}</span>
          </div>
        </div>
      </el-card>

      <el-card shadow="never">
        <template #header><b>可信度策略（确定性，非模型判断）</b></template>
        <div v-for="s in strategy" :key="s.k" class="row">
          <div>
            <div><b>{{ s.label }}</b> <el-tag size="small" type="info">{{ val(s.k) }}</el-tag></div>
            <div class="muted small">{{ s.hint }}</div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <b>模型 Provider</b>
          <el-tag v-if="!providerKeys.length" type="warning" size="small" style="margin-left:8px">
            未配置（demo 模式使用 MockGateway）
          </el-tag>
        </template>
        <div v-if="!providerKeys.length" class="muted small" style="padding:8px 0">
          demo 模式下问答/报告/学习由确定性 MockGateway 生成，无需真实模型。
        </div>
        <div v-for="rk in providerKeys" :key="rk" class="provider">
          <div class="phead">
            <el-tag size="small" type="info">{{ rk }}</el-tag>
            <b>{{ cfg.providers[rk].model || cfg.providers[rk].model_preset }}</b>
          </div>
          <div class="muted small">preset={{ cfg.providers[rk].model_preset }} · api_base={{ cfg.providers[rk].api_base || '—' }}</div>
          <div class="muted small">timeout={{ cfg.providers[rk].timeout_s }}s · max_tokens={{ cfg.providers[rk].max_tokens }}</div>
        </div>
      </el-card>

      <el-card shadow="never" class="wide">
        <template #header><b>运行参数</b></template>
        <div class="kv">
          <div><span class="muted">工作区</span> <span class="mono">{{ cfg.workspace }}</span></div>
          <div><span class="muted">深度报告</span> <span class="mono">{{ cfg.deep_report_cron }}</span></div>
          <div><span class="muted">历史学习</span> <span class="mono">{{ cfg.learn_cron }}</span></div>
          <div><span class="muted">HMI PIN</span> {{ cfg.hmi_pin_enabled ? '已启用（共享 PIN）' : '未启用（局域网开放）' }}</div>
          <div><span class="muted">演示模式</span> {{ cfg.demo ? '开启' : '关闭' }}</div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.wide { grid-column: 1 / -1; }
.row { padding: 8px 0; border-bottom: 1px solid var(--ea-border); }
.row:last-child { border-bottom: none; }
.provider { padding: 8px 0; border-bottom: 1px solid var(--ea-border); }
.provider:last-child { border-bottom: none; }
.phead { font-size: 14px; display: flex; align-items: center; gap: 8px; }
.kv { display: flex; flex-direction: column; gap: 8px; }
.small { font-size: 12px; }
.svc { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.svc-item { display: flex; flex-direction: column; gap: 4px; }

@media (max-width: 800px) {
  .grid { grid-template-columns: 1fr; }
}
</style>
