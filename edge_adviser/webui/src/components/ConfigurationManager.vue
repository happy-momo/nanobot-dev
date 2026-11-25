<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  createAssetConfiguration, deleteAssetConfiguration, deleteConnectorCredentials, downloadAssetBackup,
  fetchConfiguration, putConnectorCredentials, testConnector,
  updateAssetConfiguration,
  type AssetConfiguration, type AssetConfigurationInput, type ConfigurationView,
  type ConnectorTestResult,
} from '../api'
import { toast } from '../stores/toast'
import ObsPanel from './ObsPanel.vue'

interface MetricForm {
  key: string
  unit: string
  description: string
  norm_min: string
  norm_max: string
  warn_min: string
  warn_max: string
  crit_min: string
  crit_max: string
  persist: string
  min_samples: string
}

interface EditorForm {
  asset_id: string
  name: string
  kind: string
  description: string
  sample_freq_s: string
  window_s: string
  enabled: boolean
  connector: 'simulator' | 'rest' | 'mqtt' | 'opcua' | 'modbus'
  url: string
  timeout_s: string
  start_param: string
  end_param: string
  timestamp_field: string
  response_path: string
  field_map: string
  auth_header: 'authorization' | 'x-api-key'
  auth_value: string
  credential_id: string
}

const view = ref<ConfigurationView | null>(null)
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const testing = ref(false)
const editorOpen = ref(false)
const editingId = ref('')
const formError = ref('')
const testResult = ref<ConnectorTestResult | null>(null)
const pendingCredentialId = ref('')
const deleteTarget = ref<AssetConfiguration | null>(null)
const deleteStep = ref<'confirm' | 'backup' | 'deleting'>('confirm')
const backupRevision = ref('')
const backupSaved = ref(false)
const backupLoading = ref(false)

const form = reactive<EditorForm>({
  asset_id: '', name: '', kind: 'machine', description: '',
  sample_freq_s: '1', window_s: '60', enabled: true,
  connector: 'rest', url: '', timeout_s: '10',
  start_param: 'start', end_param: 'end', timestamp_field: 'ts',
  response_path: '', field_map: '', auth_header: 'authorization',
  auth_value: '', credential_id: '',
})
const metrics = ref<MetricForm[]>([])

const assets = computed(() => view.value?.assets || [])
const credentialStoreReady = computed(() => view.value?.credential_store_available ?? false)
const testPassed = computed(() => testResult.value?.ok === true)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    view.value = await fetchConfiguration()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '配置读取失败'
  } finally {
    loading.value = false
  }
}

function blankMetric(): MetricForm {
  return {
    key: '', unit: '', description: '', norm_min: '', norm_max: '',
    warn_min: '', warn_max: '', crit_min: '', crit_max: '',
    persist: '1', min_samples: '0',
  }
}

function numericOrNull(value: string): number | null {
  const text = value.trim()
  if (!text) return null
  const number = Number(text)
  if (!Number.isFinite(number)) throw new Error(`无效的数字：${text}`)
  return number
}

function numberField(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : ''
}

function resetForm(): void {
  Object.assign(form, {
    asset_id: '', name: '', kind: 'machine', description: '',
    sample_freq_s: '1', window_s: '60', enabled: true,
    connector: 'rest', url: '', timeout_s: '10',
    start_param: 'start', end_param: 'end', timestamp_field: 'ts',
    response_path: '', field_map: '', auth_header: 'authorization',
    auth_value: '', credential_id: '',
  })
  metrics.value = [blankMetric()]
  formError.value = ''
  testResult.value = null
  pendingCredentialId.value = ''
}

function openCreate(): void {
  editingId.value = ''
  resetForm()
  editorOpen.value = true
}

function openEdit(asset: AssetConfiguration): void {
  editingId.value = asset.asset_id
  resetForm()
  Object.assign(form, {
    asset_id: asset.asset_id,
    name: asset.name,
    kind: asset.kind || 'machine',
    description: asset.description || '',
    sample_freq_s: numberField(asset.sample_freq_s),
    window_s: numberField(asset.window_s),
    enabled: asset.enabled,
    connector: asset.source?.kind || 'rest',
    url: asset.source?.url || '',
    timeout_s: numberField(asset.source?.params?.timeout_s) || '10',
    start_param: String(asset.source?.params?.start_param || 'start'),
    end_param: String(asset.source?.params?.end_param || 'end'),
    timestamp_field: String(asset.source?.params?.timestamp_field || 'ts'),
    response_path: String(asset.source?.params?.response_path || ''),
    field_map: Object.entries((asset.source?.params?.field_map || {}) as Record<string, string>)
      .map(([target, source]) => `${target}=${source}`).join('\n'),
    credential_id: asset.source?.credential_id || '',
  })
  metrics.value = asset.metrics.map((metric) => ({
    key: String(metric.key || ''),
    unit: String(metric.unit || ''),
    description: String(metric.description || ''),
    norm_min: numberField(metric.norm_min),
    norm_max: numberField(metric.norm_max),
    warn_min: numberField(metric.warn_min),
    warn_max: numberField(metric.warn_max),
    crit_min: numberField(metric.crit_min),
    crit_max: numberField(metric.crit_max),
    persist: numberField(metric.persist) || '1',
    min_samples: numberField(metric.min_samples) || '0',
  }))
  editorOpen.value = true
}

function parseFieldMap(): Record<string, string> {
  const result: Record<string, string> = {}
  for (const line of form.field_map.split('\n')) {
    const text = line.trim()
    if (!text) continue
    const index = text.indexOf('=')
    if (index < 1 || index === text.length - 1) throw new Error('字段映射格式应为：指标键=来源字段，每行一项')
    const target = text.slice(0, index).trim()
    const source = text.slice(index + 1).trim()
    if (result[target]) throw new Error(`指标映射重复：${target}`)
    result[target] = source
  }
  return result
}

function buildConfig(): AssetConfigurationInput {
  const sampleFreq = Number(form.sample_freq_s)
  const window = Number(form.window_s)
  if (!Number.isFinite(sampleFreq) || sampleFreq <= 0) throw new Error('采样间隔必须大于 0')
  if (!Number.isInteger(window) || window < 1) throw new Error('分析窗口必须是正整数')
  if (!metrics.value.length || metrics.value.some((metric) => !metric.key.trim())) {
    throw new Error('至少添加一个指标，并填写所有指标键')
  }
  const params: Record<string, unknown> = {}
  if (form.connector === 'simulator') {
    params.regime = 'clean'
    params.sample_freq_s = sampleFreq
  } else {
    const timeout = Number(form.timeout_s)
    if (!Number.isFinite(timeout) || timeout < 1 || timeout > 30) {
      throw new Error('连接超时须在 1 到 30 秒之间')
    }
    params.timeout_s = timeout
    params.start_param = form.start_param.trim()
    params.end_param = form.end_param.trim()
    params.timestamp_field = form.timestamp_field.trim()
    params.response_path = form.response_path.trim()
    params.field_map = parseFieldMap()
  }
  return {
    asset_id: form.asset_id.trim(),
    name: form.name.trim(),
    kind: form.kind.trim() || 'machine',
    description: form.description.trim(),
    sample_freq_s: sampleFreq,
    window_s: window,
    enabled: form.enabled,
    metrics: metrics.value.map((metric) => ({
      key: metric.key.trim(),
      unit: metric.unit.trim(),
      description: metric.description.trim(),
      norm_min: numericOrNull(metric.norm_min),
      norm_max: numericOrNull(metric.norm_max),
      warn_min: numericOrNull(metric.warn_min),
      warn_max: numericOrNull(metric.warn_max),
      crit_min: numericOrNull(metric.crit_min),
      crit_max: numericOrNull(metric.crit_max),
      persist: integerField(metric.persist, '持续样本数', 1),
      min_samples: integerField(metric.min_samples, '最少样本数', 0),
    })),
    source: {
      kind: form.connector,
      url: form.connector === 'rest' ? form.url.trim() : '',
      poll_interval_s: 1,
      credential_id: form.connector === 'rest' ? form.credential_id : '',
      params,
    },
  }
}

function integerField(value: string, label: string, minimum: number): number {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < minimum) {
    throw new Error(`${label}必须是大于或等于 ${minimum} 的整数`)
  }
  return parsed
}

watch(() => [form, metrics.value], () => {
  testResult.value = null
}, { deep: true })
watch(() => form.connector, (connector) => {
  if (connector !== 'rest') form.auth_value = ''
})

async function buildRequestConfig(): Promise<AssetConfigurationInput> {
  const config = buildConfig()
  await saveNewCredential()
  config.source.credential_id = form.connector === 'rest' ? form.credential_id : ''
  return config
}

async function saveNewCredential(): Promise<void> {
  if (form.connector !== 'rest') {
    if (pendingCredentialId.value) {
      await deleteUnusedCredential(pendingCredentialId.value)
      pendingCredentialId.value = ''
    }
    form.auth_value = ''
    return
  }
  if (!form.auth_value.trim()) return
  if (!credentialStoreReady.value) {
    throw new Error('凭据加密未启用，请先由管理员配置 EDGE_ADVISER_CREDENTIAL_KEY')
  }
  if (!pendingCredentialId.value) {
    pendingCredentialId.value = `ea-${crypto.randomUUID().replace(/-/g, '')}`
  }
  await putConnectorCredentials(pendingCredentialId.value, {
    [form.auth_header]: form.auth_value,
  })
  form.credential_id = pendingCredentialId.value
  form.auth_value = ''
}

async function runTest(): Promise<void> {
  formError.value = ''
  testResult.value = null
  testing.value = true
  try {
    const config = await buildRequestConfig()
    testResult.value = await testConnector(config)
    if (testResult.value.ok) toast('连续采集测试通过')
    else toast('测试完成，存在需要处理的问题', 'warn')
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '连接测试失败'
  } finally {
    testing.value = false
  }
}

async function save(): Promise<void> {
  formError.value = ''
  saving.value = true
  try {
    const config = await buildRequestConfig()
    if (config.enabled && !testPassed.value) {
      throw new Error('启用设备前请先完成并通过连续采集测试')
    }
    const result = editingId.value
      ? await updateAssetConfiguration(editingId.value, config)
      : await createAssetConfiguration(config)
    editorOpen.value = false
    if (result.warnings.length) toast(result.warnings.join('；'), 'warn')
    else toast(editingId.value ? '设备配置已更新' : '设备已添加')
    await load()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function discardEditor(): Promise<void> {
  if (saving.value || testing.value) return
  if (pendingCredentialId.value) {
    try {
      await deleteUnusedCredential(pendingCredentialId.value)
    } catch (error) {
      toast(error instanceof Error ? `未使用凭据清理失败：${error.message}` : '未使用凭据清理失败', 'warn')
    }
  }
  editorOpen.value = false
}

async function deleteUnusedCredential(id: string): Promise<void> {
  await deleteConnectorCredentials(id)
}

function confirmDelete(asset: AssetConfiguration): void {
  formError.value = ''
  deleteTarget.value = asset
  deleteStep.value = 'confirm'
  backupRevision.value = ''
  backupSaved.value = false
  backupLoading.value = false
}

function closeDelete(): void {
  if (backupLoading.value || deleteStep.value === 'deleting') return
  deleteTarget.value = null
}

async function createBackup(): Promise<void> {
  if (!deleteTarget.value) return
  formError.value = ''
  deleteStep.value = 'backup'
  backupSaved.value = false
  backupLoading.value = true
  try {
    const backup = await downloadAssetBackup(deleteTarget.value.asset_id)
    backupRevision.value = backup.config_revision
  } catch (error) {
    deleteStep.value = 'confirm'
    formError.value = error instanceof Error ? error.message : '备份下载失败，未执行删除'
  } finally {
    backupLoading.value = false
  }
}

async function deleteAsset(): Promise<void> {
  if (!deleteTarget.value || !backupRevision.value || !backupSaved.value) return
  deleteStep.value = 'deleting'
  try {
    const result = await deleteAssetConfiguration(deleteTarget.value.asset_id, backupRevision.value)
    if (result.warnings.length) toast(result.warnings.join('；'), 'warn')
    else toast('设备配置已删除，历史记录已保留')
    deleteTarget.value = null
    await load()
  } catch (error) {
    deleteStep.value = 'confirm'
    backupRevision.value = ''
    backupSaved.value = false
    formError.value = error instanceof Error ? error.message : '删除失败'
  }
}

onMounted(load)
</script>

<template>
  <ObsPanel title="资产与数据接入" :meta="`${assets.length} 台设备`">
    <div class="cm-toolbar">
      <p class="cm-note">在这里维护设备档案和数据接入；保存前需通过连续采集与数据质量测试。</p>
      <button type="button" class="cm-primary" :disabled="loading || !!loadError" @click="openCreate">
        添加设备
      </button>
    </div>
    <p v-if="!credentialStoreReady && !loadError" class="cm-warning">
      后端凭据加密尚未配置。可编辑无认证数据源；保存认证凭据前需配置服务器环境变量。
    </p>
    <p v-if="loadError" class="cm-error">{{ loadError }}</p>
    <div v-if="loading" class="cm-empty">正在读取配置…</div>
    <div v-else-if="assets.length" class="cm-list">
      <article v-for="asset in assets" :key="asset.asset_id" class="cm-row">
        <div class="cm-identity">
          <strong>{{ asset.name }}</strong>
          <span class="cm-mono">{{ asset.asset_id }}</span>
          <span class="cm-detail">{{ asset.kind }} · {{ asset.metrics.length }} 项指标</span>
        </div>
        <div class="cm-state">
          <span :class="['cm-badge', asset.enabled ? 'is-on' : 'is-off']">
            {{ asset.enabled ? '已启用' : '已停用' }}
          </span>
          <span class="cm-detail">{{ asset.source?.kind?.toUpperCase() || '未配置数据源' }}</span>
          <span v-if="asset.credential_configured" class="cm-detail">凭据已保管</span>
        </div>
        <div class="cm-actions">
          <button type="button" @click="openEdit(asset)">编辑</button>
          <button type="button" class="is-danger" @click="confirmDelete(asset)">删除</button>
        </div>
      </article>
    </div>
    <div v-else class="cm-empty">还没有配置设备。添加设备后，可在此设置指标和数据接入。</div>
  </ObsPanel>

  <Teleport to="body">
    <div v-if="editorOpen" class="cm-overlay" @click.self="discardEditor">
      <section class="cm-dialog" role="dialog" aria-modal="true" aria-labelledby="cm-title">
        <header class="cm-dialog-head">
          <div>
            <span class="cm-eyebrow">设备配置</span>
            <h2 id="cm-title">{{ editingId ? '编辑设备' : '添加设备' }}</h2>
          </div>
          <button type="button" aria-label="关闭配置" :disabled="saving || testing"
            @click="discardEditor">关闭</button>
        </header>

        <form class="cm-form" @submit.prevent="save">
          <fieldset :disabled="saving || testing">
            <legend>基本信息</legend>
            <div class="cm-fields">
              <label>设备 ID
                <input v-model="form.asset_id" :disabled="!!editingId" required maxlength="64" autocomplete="off">
              </label>
              <label>设备名称
                <input v-model="form.name" required maxlength="120">
              </label>
              <label>设备类型
                <input v-model="form.kind" maxlength="64">
              </label>
              <label>采样间隔（秒）
                <input v-model="form.sample_freq_s" type="number" min="0.01" step="any" required>
              </label>
              <label>分析窗口（秒）
                <input v-model="form.window_s" type="number" min="1" step="1" required>
              </label>
              <label class="cm-check">
                <input v-model="form.enabled" type="checkbox">
                保存后启用并加入巡检
              </label>
              <label class="cm-wide">设备说明
                <textarea v-model="form.description" rows="2" maxlength="1000" />
              </label>
            </div>
          </fieldset>

          <fieldset :disabled="saving || testing">
            <legend>监测指标</legend>
            <div v-for="(metric, index) in metrics" :key="index" class="cm-metric">
              <div class="cm-metric-head">
                <strong>指标 {{ index + 1 }}</strong>
                <button v-if="metrics.length > 1" type="button" class="is-danger"
                  @click="metrics.splice(index, 1)">移除</button>
              </div>
              <div class="cm-fields">
                <label>指标键<input v-model="metric.key" required placeholder="例如 temperature"></label>
                <label>单位<input v-model="metric.unit" placeholder="例如 °C"></label>
                <label>正常下限<input v-model="metric.norm_min" type="number" step="any"></label>
                <label>正常上限<input v-model="metric.norm_max" type="number" step="any"></label>
                <label>警告下限<input v-model="metric.warn_min" type="number" step="any"></label>
                <label>警告上限<input v-model="metric.warn_max" type="number" step="any"></label>
                <label>严重下限<input v-model="metric.crit_min" type="number" step="any"></label>
                <label>严重上限<input v-model="metric.crit_max" type="number" step="any"></label>
                <label>持续样本数<input v-model="metric.persist" type="number" min="1" step="1"></label>
                <label>最少样本数<input v-model="metric.min_samples" type="number" min="0" step="1"></label>
                <label class="cm-wide">指标说明<input v-model="metric.description"></label>
              </div>
            </div>
            <button type="button" class="cm-secondary" @click="metrics.push(blankMetric())">添加指标</button>
          </fieldset>

          <fieldset :disabled="saving || testing">
            <legend>数据接入</legend>
            <div class="cm-fields">
              <label>接入方式
                <select v-model="form.connector">
                  <option value="rest">REST 接口</option>
                  <option value="simulator">模拟数据</option>
                  <option value="mqtt" disabled>MQTT（后续版本）</option>
                  <option value="opcua" disabled>OPC UA（后续版本）</option>
                  <option value="modbus" disabled>Modbus（后续版本）</option>
                </select>
              </label>
              <label v-if="form.connector === 'rest'" class="cm-wide">接口地址
                <input v-model="form.url" type="url" required placeholder="https://host/api/telemetry">
              </label>
              <label v-if="form.connector === 'rest'">连接超时（秒）
                <input v-model="form.timeout_s" type="number" min="1" max="30" step="1">
              </label>
              <label v-if="form.connector === 'rest'">开始时间参数
                <input v-model="form.start_param" required>
              </label>
              <label v-if="form.connector === 'rest'">结束时间参数
                <input v-model="form.end_param" required>
              </label>
              <label v-if="form.connector === 'rest'">时间戳字段
                <input v-model="form.timestamp_field" required>
              </label>
              <label v-if="form.connector === 'rest'">响应数据路径
                <input v-model="form.response_path" placeholder="留空表示响应本身是数组">
              </label>
              <label v-if="form.connector === 'rest'" class="cm-wide">指标字段映射
                <textarea v-model="form.field_map" rows="3" placeholder="温度=temperature&#10;振动=vibration&#10;留空时按指标键读取" />
              </label>
              <label v-if="form.connector === 'rest'">认证请求头
                <select v-model="form.auth_header">
                  <option value="authorization">Authorization</option>
                  <option value="x-api-key">X-API-Key</option>
                </select>
              </label>
              <label v-if="form.connector === 'rest'" class="cm-wide">认证凭据
                <input v-model="form.auth_value" type="password" autocomplete="new-password"
                  :placeholder="form.credential_id ? '已保存；留空保持不变，填写新值将轮换凭据' : '留空表示该接口无需认证'">
              </label>
              <p v-if="form.connector === 'simulator'" class="cm-note cm-wide">
                模拟数据仅支持 temp、vibration、rpm 指标，用于开发和演示。
              </p>
              <p v-if="form.connector === 'rest'" class="cm-note cm-wide">
                数据采集频率由系统巡检间隔统一控制。
              </p>
              <p v-if="form.connector === 'rest' && form.credential_id" class="cm-note cm-wide">
                此连接器已绑定后端凭据。凭据值不会回显；留空可保持现有凭据。
              </p>
            </div>
          </fieldset>

          <div v-if="testResult" class="cm-test" :class="{ 'is-good': testResult.ok, 'is-bad': !testResult.ok }">
            <strong>{{ testResult.ok ? '连续采集测试通过' : '测试未通过' }}</strong>
            <span>共 {{ testResult.total_samples }} 个样本，完成 {{ testResult.runs.length }} 次采集</span>
            <ul v-if="testResult.issues.length"><li v-for="issue in testResult.issues" :key="issue">{{ issue }}</li></ul>
            <pre v-if="testResult.sample_preview.length">{{ JSON.stringify(testResult.sample_preview, null, 2) }}</pre>
          </div>
          <p v-if="formError" class="cm-error">{{ formError }}</p>

          <footer class="cm-dialog-foot">
            <button type="button" class="cm-secondary" :disabled="saving || testing"
              @click="runTest">{{ testing ? '连续采集中…' : '测试连接与数据' }}</button>
            <span class="cm-spacer" />
            <button type="button" class="cm-secondary" :disabled="saving || testing"
              @click="discardEditor">取消</button>
            <button type="submit" class="cm-primary" :disabled="saving || testing || (form.enabled && !testPassed)">
              {{ saving ? '保存中…' : '保存配置' }}
            </button>
          </footer>
        </form>
      </section>
    </div>

    <div v-if="deleteTarget" class="cm-overlay" @click.self="closeDelete">
      <section class="cm-dialog cm-delete" role="dialog" aria-modal="true" aria-labelledby="cm-delete-title">
        <header class="cm-dialog-head">
          <div>
            <span class="cm-eyebrow">删除设备配置</span>
            <h2 id="cm-delete-title">{{ deleteStep === 'confirm' ? '确认删除设备？' : '备份已生成' }}</h2>
          </div>
          <button type="button" aria-label="关闭删除确认"
            :disabled="backupLoading || deleteStep === 'deleting'" @click="closeDelete">关闭</button>
        </header>
        <p>将移除 <strong>{{ deleteTarget.name }}</strong>（{{ deleteTarget.asset_id }}）的活动配置并关闭数据连接。</p>
        <p>历史快照、告警、报告、知识记录和审计记录会保留；下载的 JSON 不包含凭据，恢复时需重新绑定认证信息。</p>
        <p v-if="formError" class="cm-error">{{ formError }}</p>
        <template v-if="deleteStep === 'confirm'">
          <footer class="cm-dialog-foot">
            <button type="button" class="cm-secondary" :disabled="backupLoading"
              @click="closeDelete">取消</button>
            <button type="button" class="cm-danger" :disabled="backupLoading"
              @click="createBackup">{{ backupLoading ? '正在准备备份…' : '下载备份并继续' }}</button>
          </footer>
        </template>
        <template v-else>
          <label class="cm-check cm-backup-check">
            <input v-model="backupSaved" type="checkbox" :disabled="backupLoading">
            我已保存下载的脱敏配置备份，并确认继续删除
          </label>
          <footer class="cm-dialog-foot">
            <button type="button" class="cm-secondary"
              :disabled="backupLoading || deleteStep === 'deleting'" @click="deleteStep = 'confirm'">返回</button>
            <button type="button" class="cm-danger"
              :disabled="backupLoading || !backupSaved || deleteStep === 'deleting'"
              @click="deleteAsset">{{ deleteStep === 'deleting' ? '删除中…' : '最终确认删除' }}</button>
          </footer>
        </template>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.cm-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 14px; border-bottom: 1px solid var(--line); }
.cm-note { margin: 0; color: var(--tx-dim); font-size: 11px; line-height: 1.55; }
.cm-warning { margin: 10px 14px; padding: 9px 11px; border: 1px solid var(--warn);
  color: var(--warn); font-size: 11px; line-height: 1.5; }
.cm-error { margin: 8px 0; color: var(--crit); font-size: 11px; line-height: 1.5; }
.cm-list { display: flex; flex-direction: column; }
.cm-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(100px, auto) auto;
  align-items: center; gap: 14px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.cm-row:last-child { border-bottom: 0; }
.cm-identity, .cm-state { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; min-width: 0; }
.cm-identity strong { color: var(--tx-hi); font-size: 12px; }
.cm-mono, .cm-detail { color: var(--tx-dim); font: 10px var(--font-mono); }
.cm-detail { font-family: var(--font-sans); }
.cm-badge { border: 1px solid var(--line-2); border-radius: 2px; padding: 2px 6px; font-size: 10px; }
.cm-badge.is-on { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 40%, var(--line)); }
.cm-badge.is-off { color: var(--tx-dim); }
.cm-actions { display: flex; justify-content: flex-end; gap: 6px; }
.cm-actions button, .cm-dialog-head > button, .cm-metric-head button {
  min-height: 34px; padding: 5px 10px; border: 1px solid var(--line-2);
  border-radius: var(--r-s); background: var(--bg2); color: var(--tx); cursor: pointer; font-size: 11px;
}
.cm-actions button:hover, .cm-dialog-head > button:hover { border-color: var(--cyan-dim); color: var(--tx-hi); }
.cm-actions .is-danger, .cm-metric-head .is-danger { color: var(--crit); }
.cm-empty { padding: 20px 14px; color: var(--tx-dim); font-size: 11px; }
.cm-primary, .cm-secondary, .cm-danger {
  min-height: 40px; padding: 7px 14px; border: 1px solid var(--line-2);
  border-radius: var(--r-s); background: var(--bg2); color: var(--tx-hi);
  cursor: pointer; font-size: 11.5px; white-space: nowrap;
}
.cm-primary { border-color: var(--cyan-dim); color: var(--cyan); }
.cm-danger { border-color: color-mix(in srgb, var(--crit) 55%, var(--line)); color: var(--crit); }
.cm-primary:disabled, .cm-secondary:disabled, .cm-danger:disabled { opacity: .48; cursor: not-allowed; }
.cm-overlay { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center;
  padding: 14px; background: rgba(0, 0, 0, .72); }
.cm-dialog { width: min(760px, 100%); max-height: min(90vh, 900px); display: flex; flex-direction: column;
  overflow: hidden; border: 1px solid var(--line-2); border-radius: var(--r-s); background: var(--bg0); }
.cm-delete { width: min(520px, 100%); padding-bottom: 6px; }
.cm-dialog-head { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 14px 18px; border-bottom: 1px solid var(--line); }
.cm-eyebrow { color: var(--tx-dim); font-size: 9px; letter-spacing: .12em; }
.cm-dialog-head h2 { margin: 3px 0 0; color: var(--tx-hi); font-size: 17px; font-weight: 600; }
.cm-form { overflow: auto; padding: 12px 18px 16px; }
.cm-form fieldset { margin: 0 0 14px; padding: 12px; border: 1px solid var(--line); min-width: 0; }
.cm-form legend { padding: 0 6px; color: var(--cyan); font-size: 11px; }
.cm-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.cm-fields label { display: flex; flex-direction: column; gap: 5px; color: var(--tx-dim); font-size: 10.5px; min-width: 0; }
.cm-fields input, .cm-fields textarea, .cm-fields select {
  width: 100%; min-width: 0; min-height: 36px; box-sizing: border-box;
  border: 1px solid var(--line-2); border-radius: var(--r-s); background: var(--bg1);
  color: var(--tx-hi); padding: 7px 9px; font: 12px var(--font-sans);
}
.cm-fields textarea { resize: vertical; }
.cm-fields input:focus, .cm-fields textarea:focus, .cm-fields select:focus { outline: 1px solid var(--cyan-dim); }
.cm-fields input:disabled { opacity: .55; }
.cm-fields .cm-wide { grid-column: 1 / -1; }
.cm-fields .cm-check, .cm-check { flex-direction: row; align-items: center; gap: 8px; min-height: 36px; }
.cm-check input { width: 16px; min-height: 16px; accent-color: var(--cyan); }
.cm-metric { margin: 0 0 10px; padding: 10px; border: 1px solid var(--line); }
.cm-metric-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.cm-metric-head strong { color: var(--tx-hi); font-size: 11px; }
.cm-test { display: flex; flex-direction: column; gap: 5px; padding: 10px 12px;
  margin: 10px 0; border: 1px solid var(--line-2); font-size: 11px; }
.cm-test.is-good { border-color: var(--ok); color: var(--ok); }
.cm-test.is-bad { border-color: var(--warn); color: var(--warn); }
.cm-test ul { margin: 2px 0; padding-left: 18px; }
.cm-test pre { max-height: 160px; overflow: auto; margin: 4px 0 0; padding: 8px;
  background: var(--bg1); color: var(--tx); font: 10px var(--font-mono); }
.cm-dialog-foot { display: flex; align-items: center; gap: 8px; padding-top: 12px; }
.cm-spacer { flex: 1; }
.cm-delete > p { margin: 12px 18px 0; color: var(--tx); font-size: 12px; line-height: 1.6; }
.cm-delete > .cm-error { margin: 10px 18px 0; }
.cm-backup-check { margin: 14px 18px 0; color: var(--tx-hi); font-size: 11px; }
.cm-delete .cm-dialog-foot { padding: 14px 18px 8px; justify-content: flex-end; }
@media (max-width: 620px) {
  .cm-toolbar { align-items: flex-start; flex-direction: column; }
  .cm-row { grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
  .cm-state { grid-column: 1; }
  .cm-actions { grid-column: 2; grid-row: 1 / span 2; flex-direction: column; }
  .cm-overlay { padding: 8px; }
  .cm-dialog { max-height: 94vh; }
  .cm-dialog-head, .cm-form { padding-left: 12px; padding-right: 12px; }
  .cm-form fieldset { padding: 9px; }
  .cm-fields { grid-template-columns: minmax(0, 1fr); }
  .cm-fields .cm-wide { grid-column: auto; }
  .cm-dialog-foot { flex-wrap: wrap; }
  .cm-spacer { display: none; }
}
</style>
