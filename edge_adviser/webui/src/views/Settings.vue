<script setup lang="ts">
// System settings: read-only view of the deterministic tuning knobs + provider
// status. Editing live config is deliberately out of the read-first HMI scope
// (change it in config.sample.json then restart). This page exists so the field
// engineer can SEE the trustworthiness policy actually in force.
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/http'

const cfg = ref<any>(null)

onMounted(async () => {
  cfg.value = await api.get<any>('/api/config')
})

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
    <div v-if="!cfg" class="muted" style="padding:40px;text-align:center">加载中…</div>
    <div v-else class="grid">
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
        <template #header><b>模型 Provider</b></template>
        <div v-for="rk in providerKeys" :key="rk" class="provider">
          <div class="phead"><b>{{ rk }}</b> <span class="muted small">{{ cfg.providers[rk].model }}</span></div>
          <div class="muted small">preset={{ cfg.providers[rk].model_preset }} · api_base={{ cfg.providers[rk].api_base }}</div>
          <div class="muted small">timeout={{ cfg.providers[rk].timeout_s }}s · max_tokens={{ cfg.providers[rk].max_tokens }}</div>
        </div>
      </el-card>

      <el-card shadow="never" class="wide">
        <template #header><b>运行参数</b></template>
        <div class="kv">
          <div><span class="muted">工作区</span> {{ cfg.workspace }}</div>
          <div><span class="muted">监控周期</span> {{ cfg.monitor_interval_s }}s</div>
          <div><span class="muted">深度报告</span> {{ cfg.deep_report_cron }}</div>
          <div><span class="muted">历史学习</span> {{ cfg.learn_cron }}</div>
          <div><span class="muted">HMI PIN</span> {{ cfg.hmi_pin_enabled ? '已启用（共享 PIN）' : '未启用（局域网开放）' }}</div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.wide { grid-column: 1 / -1; }
.row { padding: 8px 0; border-bottom: 1px solid var(--ea-border); }
.provider { padding: 8px 0; border-bottom: 1px solid var(--ea-border); }
.phead { font-size: 14px; }
.kv { display: flex; flex-direction: column; gap: 8px; }
.small { font-size: 12px; }
</style>