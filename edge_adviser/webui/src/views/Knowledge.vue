<script setup lang="ts">
// Knowledge base: MEMORY.md rendered as safe markdown with chapter navigation,
// plus the claims/conflicts ledger. Conflict/stale claims get a distinct tag so
// a superseded threshold or an expired fact is never silently trusted. Empty
// state offers a "run learning" CTA.
import { ref, watch, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import MarkdownView from '../components/MarkdownView.vue'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()
const assetId = ref('')
const data = ref<any>(null)
const loading = ref(false)
const learning = ref(false)
const activeChapter = ref(0)

async function load() {
  if (!assetId.value) return
  loading.value = true
  try {
    data.value = await api.get<any>(`/api/assets/${assetId.value}/knowledge`)
    activeChapter.value = 0
  } catch (e: any) {
    ElMessage.error(e?.message || '知识库加载失败')
  } finally {
    loading.value = false
  }
}

async function runLearn() {
  if (!assetId.value || learning.value) return
  learning.value = true
  try {
    const r = await rt.triggerLearn(assetId.value)
    if (r?.error) ElMessage.warning('学习被门禁拒绝：' + r.error)
    else {
      ElMessage.success('学习完成，知识已更新')
      await load()
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '学习失败')
  } finally {
    learning.value = false
  }
}

// chapter split on `^## ` headings for the nav rail
const chapters = computed(() => {
  const md: string = data.value?.memory_md || ''
  if (!md.trim()) return []
  const parts = md.split(/^## /m).filter((p) => p.trim())
  const out: Array<{ title: string; body: string }> = []
  for (const p of parts) {
    const nl = p.indexOf('\n')
    const title = (nl >= 0 ? p.slice(0, nl) : p).trim().replace(/^#\s*/, '') || '(untitled)'
    const body = (nl >= 0 ? p.slice(nl + 1) : '').trim()
    // keep the original heading markdown so MarkdownView renders it
    out.push({ title, body: '## ' + title + '\n\n' + body })
  }
  return out
})

const claimStatus = computed(() => (s?: string) => {
  const map: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
    active: { label: '生效中', type: 'success' },
    stale: { label: '已过时', type: 'warning' },
    superseded: { label: '已被取代', type: 'danger' },
  }
  return map[s || ''] || { label: s || '未知', type: 'info' as const }
})

onMounted(async () => {
  if (!assetId.value && rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
  await load()
})
watch(assetId, () => load())
</script>

<template>
  <div class="page">
    <PageHeader title="知识库" subtitle="设备记忆（MEMORY.md）+ 知识声明账本">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <el-button size="small" @click="load">刷新</el-button>
    </PageHeader>

    <div v-if="loading" style="padding:40px;text-align:center" class="muted">加载中…</div>

    <EmptyState v-else-if="!data?.memory_md?.trim()"
                icon="📚" text="该设备还没有学习知识"
                hint="运行学习后，模型会从历史快照中沉淀运行洞察、阈值校准与维护动作">
      <el-button size="small" type="primary" :loading="learning" @click="runLearn">
        运行学习
      </el-button>
    </EmptyState>

    <div v-else class="layout">
      <!-- chapter rail -->
      <aside class="chapters">
        <div v-for="(ch, i) in chapters" :key="i" class="ch-item"
             :class="{ active: activeChapter === i }" @click="activeChapter = i">
          {{ ch.title }}
        </div>
      </aside>

      <section class="mem">
        <el-card shadow="never" v-loading="false">
          <MarkdownView :source="chapters[activeChapter]?.body || data.memory_md" />
        </el-card>
      </section>

      <aside class="ledger">
        <div class="ledger-card" v-if="data.conflicts?.length">
          <div class="lc-title">⚠ 冲突 / 过期标记</div>
          <div v-for="c in data.conflicts" :key="c.id || c.ts" class="lc-row">
            <el-tag type="danger" size="small">冲突</el-tag>
            <span class="small">{{ c.reason || c.claim_id }}</span>
          </div>
        </div>
        <div class="ledger-card">
          <div class="lc-title">知识声明</div>
          <div v-if="!data.claims?.length" class="muted small">暂无声明记录</div>
          <div v-for="c in data.claims" :key="c.id || c.ts" class="claim">
            <el-tag :type="claimStatus(c.status).type" size="small">
              {{ claimStatus(c.status).label }}
            </el-tag>
            <div class="small claim-text">{{ c.claim }}</div>
          </div>
        </div>
        <div class="ledger-card muted small">
          学习门禁：{{ data.learn_gate }}
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid; grid-template-columns: 180px 1fr 300px; gap: 16px; align-items: start;
}
.chapters {
  background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-md); padding: 8px;
  display: flex; flex-direction: column; gap: 2px;
  max-height: 70vh; overflow-y: auto;
}
.ch-item {
  padding: 8px 10px; border-radius: var(--ea-r-sm); cursor: pointer;
  font-size: 13px; color: var(--ea-text-muted);
}
.ch-item:hover { background: var(--ea-panel-soft); color: var(--ea-text); }
.ch-item.active { background: var(--ea-accent-soft); color: var(--ea-text); font-weight: 600; }
.mem { min-width: 0; }
.ledger { display: flex; flex-direction: column; gap: 12px; }
.ledger-card {
  background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: var(--ea-r-md); padding: 12px 14px;
}
.lc-title { font-weight: 700; font-size: 13px; margin-bottom: 8px; }
.lc-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.claim { margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.claim-text { line-height: 1.5; }

@media (max-width: 1000px) {
  .layout { grid-template-columns: 1fr; }
  .chapters { flex-direction: row; overflow-x: auto; max-height: none; }
  .ch-item { flex-shrink: 0; }
}
</style>
