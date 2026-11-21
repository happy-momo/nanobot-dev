<script setup lang="ts">
// Operator corrections feed the learn loop: pick an asset, optionally prefix a
// quick category, write a note, submit. History is formatted and validated.
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import { formatTs } from '../theme'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const rt = useRuntime()
const assetId = ref('')
const note = ref('')
const list = ref<any[]>([])
const saving = ref(false)
const loading = ref(false)
const loadError = ref('')
// request-sequence guard against the A→B switch race
let seq = 0

const categories = ['设备状态纠正', '阈值建议', '维护记录', '数据质量', '其他']
const category = ref('')

async function load() {
  if (!assetId.value) return
  const mySeq = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const l = (await api.get<any>(`/api/assets/${assetId.value}/feedback`)) || []
    if (mySeq !== seq) return
    list.value = l
  } catch (e: any) {
    if (mySeq !== seq) return
    loadError.value = e?.message || '反馈历史加载失败'
  } finally {
    if (mySeq === seq) loading.value = false
  }
}

async function submit() {
  const text = note.value.trim()
  if (text.length < 2) {
    ElMessage.warning('请填写反馈内容（至少 2 个字符）')
    return
  }
  if (!assetId.value) {
    if (rt.overviewCards.length) assetId.value = rt.overviewCards[0].asset_id
    else { ElMessage.warning('尚无可用设备'); return }
  }
  saving.value = true
  try {
    const full = category.value ? `【${category.value}】${text}` : text
    await rt.sendFeedback(assetId.value, full)
    note.value = ''
    category.value = ''
    // reload under a fresh sequence; a failure AFTER a successful POST must
    // not read as "记录失败" (the note was already persisted)
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
    <PageHeader title="反馈" subtitle="记录操作员纠正 — 历史学习会将正确结论沉淀进该设备 MEMORY.md">
      <el-select v-model="assetId" style="width:200px">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
    </PageHeader>

    <EmptyState v-if="rt.ready && !rt.overviewCards.length" text="尚无可用设备"
                hint="先在配置中添加资产" />

    <template v-else>
      <el-card shadow="never" style="margin-bottom:16px">
        <div class="cats">
          <el-check-tag v-for="c in categories" :key="c" :checked="category === c"
                        @change="category = category === c ? '' : c" size="small">
            {{ c }}
          </el-check-tag>
        </div>
        <div class="form">
          <el-input
            v-model="note"
            type="textarea"
            :rows="3"
            placeholder="例如：昨天振动 4.3mm/s 实际是联轴器松动，不是轴承问题"
          />
          <el-button type="primary" @click="submit" :loading="saving" style="align-self:flex-start">
            记录反馈
          </el-button>
        </div>
      </el-card>

      <el-card shadow="never" v-loading="loading">
        <template #header><b>历史反馈（{{ list.length }}）</b></template>
        <div v-if="loadError" style="padding:20px;text-align:center">
          <div style="color:#f4554f;margin-bottom:8px" class="small">⚠ 反馈历史加载失败</div>
          <el-button size="small" @click="load">重试</el-button>
        </div>
        <el-empty v-else-if="!list.length" description="暂无反馈" :image-size="60" />
        <div v-else class="items">
          <div v-for="(f, i) in list" :key="i" class="item">
            <div class="text">{{ f.note }}</div>
            <div class="muted small mono">{{ formatTs(f.timestamp) }}</div>
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.cats { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.form { display: flex; gap: 10px; align-items: flex-start; }
.item { padding: 10px 0; border-bottom: 1px solid var(--ea-border); }
.item:last-child { border-bottom: none; }
.text { line-height: 1.5; }
.small { font-size: 12px; margin-top: 4px; }
</style>
