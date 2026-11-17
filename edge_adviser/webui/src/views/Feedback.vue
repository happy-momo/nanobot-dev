<script setup lang="ts">
// Operator corrections feed the learn loop. Simple: pick asset, write a note,
// submit. This page shows the history of corrections recorded for that asset.
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'

const rt = useRuntime()
const assetId = ref('')
const note = ref('')
const list = ref<any[]>([])
const saving = ref(false)

async function load() {
  if (!assetId.value) return
  list.value = (await api.get<any>(`/api/assets/${assetId.value}/feedback`)) || []
}

async function submit() {
  if (!note.value.trim()) return
  saving.value = true
  try {
    await rt.sendFeedback(assetId.value, note.value.trim())
    note.value = ''
    await load()
    ElMessage.success('反馈已记录，将进入学习闭环')
  } catch (e: any) {
    ElMessage.error(e?.message || '记录失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  if (!assetId.value && rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
  await load()
})
watch(() => assetId.value, load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <div class="muted" style="font-size:12px">记录操作员纠正，历史学习会将正确结论沉淀进该设备 MEMORY.md。</div>
    </div>

    <el-card shadow="never" style="margin-bottom:16px">
      <div class="form">
        <el-input
          v-model="note"
          type="textarea"
          :rows="3"
          placeholder="例如：昨天振动 4.3mm/s 实际是联轴器松动，不是轴承问题"
        />
        <el-button type="primary" @click="submit" :loading="saving">记录反馈</el-button>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header><b>历史反馈（{{ list.length }}）</b></template>
      <el-empty v-if="!list.length" description="暂无反馈" :image-size="60" />
      <div v-else class="items">
        <div v-for="(f, i) in list" :key="i" class="item">
          <div class="text">{{ f.note }}</div>
          <div class="muted small">{{ f.timestamp }}</div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.form { display: flex; gap: 10px; align-items: flex-start; }
.item { padding: 10px 0; border-bottom: 1px solid var(--ea-border); }
.text { line-height: 1.5; }
.small { font-size: 12px; }
</style>