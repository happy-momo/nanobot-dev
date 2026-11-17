<script setup lang="ts">
// Chat-style Q&A. Key UX rule inherited from the backend's freshness gate: a
// question answered without a fresh snapshot is REFUSED (ok=false) and shown as
// such — never passed to the model to guess. The answer card also shows provider
// + degraded so the operator knows which model wrote it.
import { ref, nextTick } from 'vue'
import { api } from '../api/http'
import { useRuntime } from '../stores/runtime'
import TrustBadge from '../components/TrustBadge.vue'

const rt = useRuntime()

const assetId = ref('')
const messages = ref<{ role: 'user' | 'assistant'; text: string; meta?: any; refused?: boolean }[]>([])
const input = ref('')
const busy = ref(false)
const listEl = ref<HTMLElement>()

async function send() {
  const q = input.value.trim()
  if (!q || busy.value) return
  if (!assetId.value) { assetId.value = rt.overviewCards[0]?.asset_id || '' }
  messages.value.push({ role: 'user', text: q })
  input.value = ''
  busy.value = true
  scroll()
  try {
    const r = await api.post<any>(`/api/assets/${assetId.value}/ask`, { question: q })
    if (r && r.content) {
      messages.value.push({ role: 'assistant', text: r.content, meta: r })
    } else if (r && r.error) {
      messages.value.push({ role: 'assistant', text: r.error, refused: true })
    } else {
      messages.value.push({ role: 'assistant', text: '(空回答)', refused: true })
    }
  } catch (e: any) {
    messages.value.push({ role: 'assistant', text: e?.message || String(e), refused: true })
  } finally {
    busy.value = false
    scroll()
  }
}

function scroll() {
  nextTick(() => { listEl.value?.scrollTo({ top: listEl.value.scrollHeight }) })
}
</script>

<template>
  <div class="page qa-page">
    <div class="toolbar">
      <el-select v-model="assetId" style="width:200px" placeholder="选择设备">
        <el-option v-for="a in rt.overviewCards" :key="a.asset_id" :value="a.asset_id" :label="a.name" />
      </el-select>
      <div class="muted" style="font-size:12px">答案基于确定性快照；无新鲜数据时系统会明确拒绝而非猜测。</div>
    </div>

    <div ref="listEl" class="thread">
      <div v-if="!messages.length" class="muted" style="text-align:center;padding:60px">
        输入设备状态问题，例如「这台泵当前风险如何？建议什么维护动作？」
      </div>
      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble">
          <div class="text">{{ m.text }}</div>
          <div v-if="m.meta && !m.refused" class="meta-row muted">
            <TrustBadge :trust="'full'" />
            ⚙ provider={{ m.meta.used_provider || '—' }}
            <el-tag :type="m.meta.degraded ? 'warning' : 'success'" size="small">
              {{ m.meta.degraded ? '已降级' : '本地/原模型' }}
            </el-tag>
          </div>
          <div v-if="m.refused" class="refused">
            ⛔ 无新鲜快照，已拒绝回答（未调用模型）。请先采集到新鲜数据。
          </div>
        </div>
      </div>
      <div v-if="busy" class="msg assistant"><div class="bubble muted">思考中…</div></div>
    </div>

    <div class="input-row">
      <el-input
        v-model="input"
        placeholder="输入问题，回车发送"
        @keyup.enter="send"
        :disabled="busy"
      />
      <el-button type="primary" @click="send" :loading="busy">发送</el-button>
    </div>
  </div>
</template>

<style scoped>
.qa-page { display: flex; flex-direction: column; height: calc(100vh - 120px); }
.thread { flex: 1; overflow-y: auto; padding: 8px 4px; display: flex; flex-direction: column; gap: 12px; }
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.msg.assistant { justify-content: flex-start; }
.bubble { max-width: 78%; background: var(--ea-panel); border: 1px solid var(--ea-border); border-radius: 12px; padding: 10px 14px; white-space: pre-wrap; line-height: 1.6; }
.msg.user .bubble { background: var(--ea-accent); color: #04121f; border-color: var(--ea-accent); }
.meta-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; }
.refused { margin-top: 8px; color: #f4554f; font-size: 13px; }
.input-row { display: flex; gap: 8px; margin-top: 12px; }
</style>