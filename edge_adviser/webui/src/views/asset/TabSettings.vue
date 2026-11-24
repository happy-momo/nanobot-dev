<script setup lang="ts">
// TabSettings — the 设置 tab (spec §5.2, engineer role): read-only asset
// profile (描述/采样频率/分析窗口/数据源) + per-metric threshold editing
// (MetricBand strip + inline form → PATCH /metrics/{key}/thresholds) +
// planned-downtime windows (display) + footer 提交反馈 (textarea → sendFeedback).
//
// COPY RULE (fix F): every field states what it is — nothing is labeled 只读.
// The thresholds ARE editable (that is the point of the tab); the profile and
// downtime windows carry no edit affordance, which is honest on its own.
//
// VALIDATION (client-side, mirrors server/router_assets.py patch_thresholds):
//   * all four values must be finite numbers or empty (empty = keep current)
//   * warn_min ≤ warn_max, crit_min ≤ crit_max (each band ordered)
//   * crit band must not overlap the warn band (high-side trip crit ABOVE warn,
//     or a legitimate low-side trip crit BELOW warn — genuine intersection is
//     rejected because it makes the level derivation ambiguous)
// Errors render inline under the form the moment they are detectable; the
// save button stays enabled so the engineer can read the message while typing.
//
// CRITICAL (Task 6 review): MetricChart watches `spec` BY REFERENCE. After a
// successful PATCH the specs are REPLACED as new objects (re-fetch asset
// metadata + rebuild the array) — never mutated in place — so both this tab's
// MetricBand strips and TabLive's MetricChart bands redraw from the new
// reference (AssetDetail re-mounts TabLive on tab switch; the store card is
// refreshed here so the live tab picks the new bands up too).
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Send } from '@lucide/vue'
import { fetchAsset, patchThresholds, sendFeedback } from '../../api'
import { useRuntime } from '../../stores/runtime'
import { toast } from '../../stores/toast'
import ObsPanel from '../../components/ObsPanel.vue'
import EmptyState from '../../components/EmptyState.vue'
import MetricBand from '../../components/MetricBand.vue'
import type { ThresholdBands } from '../../api'
import type { MetricSpecMeta } from '../../theme/meta'

const props = defineProps<{ assetId: string }>()

const rt = useRuntime()

// ------------------------------------------------------------------ shapes --
interface DowntimeWindow {
  start?: string
  end?: string
  [k: string]: any
}
interface AssetDetail {
  asset_id?: string
  name?: string
  kind?: string
  description?: string
  sample_freq_s?: number
  window_s?: number
  source_kind?: string | null
  asset_md?: string
  downtime_windows?: DowntimeWindow[]
  metrics?: MetricSpecMeta[]
}

const detail = ref<AssetDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
let seq = 0   // stale-response guard

// reactive spec objects: the reference MetricBand renders from. They are
// rebuilt (not mutated) after a PATCH so band re-renders fire by reference.
const specs = ref<MetricSpecMeta[]>([])

const card = computed(() => rt.assetById(props.assetId))
const displayName = computed(() => detail.value?.name || card.value?.name || props.assetId)

/** live value + level per metric (marker on the band) — live tick → card */
const live = computed(() => rt.liveFor(props.assetId))
function currentValue(key: string): number | null {
  const m = live.value?.metrics?.find((x) => x.key === key)
  if (m) return typeof m.max === 'number' ? m.max : m.mean ?? null
  const v = card.value?.runtime?.latest_values?.[key]
  return typeof v === 'number' ? v : null
}
function currentLevel(key: string): string {
  return live.value?.metrics?.find((x) => x.key === key)?.level || ''
}

// ------------------------------------------------------------------ loads --
async function load(): Promise<void> {
  if (!props.assetId) return
  const mySeq = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const d = await fetchAsset(props.assetId)
    if (mySeq !== seq) return
    detail.value = d
    // NEW objects + NEW array — MetricBand sees a fresh spec reference per row
    specs.value = (Array.isArray(d?.metrics) ? d.metrics : [])
      .map((m: MetricSpecMeta) => ({ ...m }))
  } catch (e) {
    if (mySeq !== seq) return
    loadError.value = e instanceof Error ? e.message : '资产档案加载失败'
    detail.value = null
    specs.value = []
  } finally {
    if (mySeq === seq) loading.value = false
  }
}

onMounted(() => { load() })
watch(() => props.assetId, (v, o) => {
  if (!v || v === o) return
  detail.value = null
  specs.value = []
  resetEdit()
  load()
})

// ------------------------------------------------- inline threshold editing --
interface EditForm {
  warn_min: string
  warn_max: string
  crit_min: string
  crit_max: string
}
const FIELDS: Array<keyof EditForm> = ['warn_min', 'warn_max', 'crit_min', 'crit_max']
const FIELD_LABEL: Record<keyof EditForm, string> = {
  warn_min: '警告下限', warn_max: '警告上限',
  crit_min: '严重下限', crit_max: '严重上限',
}

const editKey = ref('')
const editForm = reactive<EditForm>({ warn_min: '', warn_max: '', crit_min: '', crit_max: '' })
const saving = ref(false)

function openEdit(m: MetricSpecMeta): void {
  editKey.value = m.key
  for (const f of FIELDS) {
    const v = m[f]
    editForm[f] = typeof v === 'number' && Number.isFinite(v) ? String(v) : ''
  }
}

function resetEdit(): void {
  editKey.value = ''
  for (const f of FIELDS) editForm[f] = ''
}

/** the spec currently being edited (fall back to the working list) */
const editSpec = computed<MetricSpecMeta | null>(() =>
  specs.value.find((m) => m.key === editKey.value) || null)

/** one field's parse: '' = keep current (null), else finite number | invalid */
type Parsed = { kind: 'empty' } | { kind: 'ok'; v: number } | { kind: 'bad' }
function parseField(raw: string): Parsed {
  const s = raw.trim()
  if (!s) return { kind: 'empty' }
  const v = Number(s)
  if (!Number.isFinite(v)) return { kind: 'bad' }
  return { kind: 'ok', v }
}

/** per-field inline error (finite check is per-field) */
function fieldError(f: keyof EditForm): string {
  if (editKey.value && parseField(editForm[f]).kind === 'bad') {
    return `${FIELD_LABEL[f]} 必须是有限数字`
  }
  return ''
}

/** cross-field errors: band ordering + crit/warn overlap (merged with current
 * spec values for the fields left empty — the server validates the merge too,
 * but the engineer should see the reason BEFORE the round trip) */
const formError = computed<string>(() => {
  if (!editSpec.value) return ''
  const spec = editSpec.value
  const get = (f: keyof EditForm): number | null | 'bad' => {
    const p = parseField(editForm[f])
    if (p.kind === 'empty') {
      const cur = spec[f]
      return typeof cur === 'number' && Number.isFinite(cur) ? cur : null
    }
    if (p.kind === 'bad') return 'bad'
    return p.v
  }
  const wm = get('warn_min'), wx = get('warn_max')
  const cm = get('crit_min'), cx = get('crit_max')
  if (wm === 'bad' || wx === 'bad' || cm === 'bad' || cx === 'bad') return ''
  if (wm !== null && wx !== null && wm > wx) return '警告下限必须 ≤ 警告上限'
  if (cm !== null && cx !== null && cm > cx) return '严重下限必须 ≤ 严重上限'
  // genuine overlap: crit band intersects the warn band (either ordering)
  if (wm !== null && wx !== null && cm !== null && cx !== null &&
      cx > wm && cm < wx) {
    return '严重区间与警告区间重叠 — 严重应高于（或低于）警告区间'
  }
  return ''
})

const canSubmit = computed(() =>
  !!editKey.value && !saving.value &&
  FIELDS.every((f) => fieldError(f) === '') && formError.value === '')

async function saveEdit(): Promise<void> {
  if (!editKey.value || !props.assetId || saving.value) return
  // payload: only the touched fields (empty = keep current), mirroring the
  // old Assets.vue behavior and the server's partial-patch contract
  const body: ThresholdBands = {}
  for (const f of FIELDS) {
    const p = parseField(editForm[f])
    if (p.kind === 'ok') body[f] = p.v
  }
  if (!Object.keys(body).length) {
    toast('没有修改的值', 'warn')
    resetEdit()
    return
  }
  saving.value = true
  try {
    const r = await patchThresholds(props.assetId, editKey.value, body)
    const changed = r?.changed && Object.keys(r.changed).length
    if (changed) {
      toast(`阈值已更新（${editKey.value}）并写入审计`)
      // CRITICAL: replace the spec OBJECTS (new references) so the bands —
      // MetricBand here AND MetricChart in 实时 — redraw without a data change.
      // The server returns the merged metric; prefer it over a re-fetch, and
      // refresh the store card so TabLive's spec source updates too.
      const merged: MetricSpecMeta | undefined = r?.metric
      specs.value = specs.value.map((m) =>
        m.key === editKey.value && merged ? { ...m, ...merged } : { ...m })
      syncStoreSpecs()
      resetEdit()
    } else {
      toast('没有实际变更', 'warn')
      resetEdit()
    }
  } catch (e) {
    // server-side rejection (race, config file unavailable) — surface inline
    toast(e instanceof Error ? `阈值更新失败：${e.message}` : '阈值更新失败', 'crit')
  } finally {
    saving.value = false
  }
}

/** push the new specs into the store card as a NEW array (TabLive reads
 * card.config.metrics; a mutation there wouldn't retrigger its chart watch).
 * The store's MetricSpec types `unit` as required — the theme's MetricSpecMeta
 * leaves it optional (specs come from several payload shapes), so the merge
 * keeps the card's existing unit as the fallback. */
function syncStoreSpecs(): void {
  const c = card.value
  if (!c) return
  const toStore = (m: MetricSpecMeta) => ({ ...m, unit: m.unit || '' })
  if (c.config?.metrics) c.config.metrics = specs.value.map(toStore)
  if (c.metrics) c.metrics = specs.value.map(toStore)
}

// --------------------------------------------------------- planned downtime --
const downtime = computed<DowntimeWindow[]>(() =>
  Array.isArray(detail.value?.downtime_windows) ? detail.value!.downtime_windows! : [])

// ----------------------------------------------------------------- feedback --
const feedback = ref('')
const feedbackSending = ref(false)

async function submitFeedback(): Promise<void> {
  const note = feedback.value.trim()
  if (!note || feedbackSending.value) return
  feedbackSending.value = true
  try {
    await sendFeedback(props.assetId, note)
    toast('反馈已提交，将进入学习参考')
    feedback.value = ''
  } catch (e) {
    toast(e instanceof Error ? `反馈提交失败：${e.message}` : '反馈提交失败', 'crit')
  } finally {
    feedbackSending.value = false
  }
}
</script>

<template>
  <div class="ts">
    <!-- ─── profile + downtime ───────────────────────────────────────────── -->
    <div class="ts-left">
      <ObsPanel title="ASSET — 设备档案">
        <EmptyState v-if="loadError" label="档案加载失败" :hint="loadError" />
        <template v-else-if="detail">
          <div class="ts-profile">
            <div class="ts-cell ts-cell-wide">
              <span class="ts-k">描述</span>
              <span class="ts-v">{{ detail.description || '—' }}</span>
            </div>
            <div class="ts-cell">
              <span class="ts-k">采样频率</span>
              <span class="ts-v mono">{{ detail.sample_freq_s ?? '—' }}s</span>
            </div>
            <div class="ts-cell">
              <span class="ts-k">分析窗口</span>
              <span class="ts-v mono">{{ detail.window_s ?? '—' }}s</span>
            </div>
            <div class="ts-cell">
              <span class="ts-k">数据源</span>
              <span class="ts-v">{{ detail.source_kind || '—' }}</span>
            </div>
            <div class="ts-cell">
              <span class="ts-k">设备类型</span>
              <span class="ts-v">{{ detail.kind || '—' }}</span>
            </div>
          </div>
          <pre v-if="detail.asset_md" class="ts-asset-md mono">{{ detail.asset_md }}</pre>
        </template>
        <div v-else class="ts-skeleton" aria-hidden="true" />
      </ObsPanel>

      <!-- 计划停机窗 (display only — no edit affordance, copy stays honest) -->
      <ObsPanel title="计划停机窗">
        <div v-if="downtime.length" class="ts-dt">
          <div v-for="(w, i) in downtime" :key="i" class="ts-dt-row mono">
            <span class="ts-dt-start">{{ (w.start || '—').replace('T', ' ').slice(0, 19) }}</span>
            <span class="ts-dt-arrow">→</span>
            <span class="ts-dt-end">{{ (w.end || '—').replace('T', ' ').slice(0, 19) }}</span>
          </div>
          <p class="ts-dt-note">窗内空窗口不计告警（计划停机豁免）。</p>
        </div>
        <EmptyState v-else label="未配置停机窗" hint="窗内空样本会被判定为离线而非计划停机" />
      </ObsPanel>

      <!-- ─── footer: 提交反馈 ──────────────────────────────────────────────── -->
      <ObsPanel title="提交反馈">
        <div class="ts-fb">
          <textarea
            v-model="feedback"
            class="ts-fb-input"
            rows="3"
            placeholder="运行观察、阈值建议、误报/漏报 — 提交后进入学习参考"
            :maxlength="2000"
          />
          <div class="ts-fb-foot">
            <span class="ts-fb-hint mono">{{ feedback.trim().length }} / 2000</span>
            <button
              type="button"
              class="ts-fb-btn"
              :disabled="!feedback.trim() || feedbackSending"
              @click="submitFeedback"
            >
              <Send :size="13" :stroke-width="1.5" />
              <span>{{ feedbackSending ? '提交中…' : '提交反馈' }}</span>
            </button>
          </div>
        </div>
      </ObsPanel>
    </div>

    <!-- ─── thresholds: band + inline editor per metric ──────────────────── -->
    <div class="ts-right">
      <ObsPanel title="指标阈值" :meta="specs.length ? `${specs.length} 项` : undefined">
        <EmptyState
          v-if="!loading && !specs.length"
          label="无指标配置"
          hint="资产未定义 metrics，无从编辑阈值"
        />
        <div v-else class="ts-metrics">
          <div
            v-for="m in specs"
            :key="m.key"
            class="ts-metric"
            :class="{ 'ts-metric-open': editKey === m.key }"
          >
            <div class="ts-band-row">
              <MetricBand
                :metric="m"
                :value="currentValue(m.key)"
                :level="currentLevel(m.key)"
              />
              <button
                type="button"
                class="ts-edit-btn"
                @click="editKey === m.key ? resetEdit() : openEdit(m)"
              >
                {{ editKey === m.key ? '收起' : '编辑' }}
              </button>
            </div>

            <!-- inline editor (client validation, errors inline) -->
            <div v-if="editKey === m.key" class="ts-editor">
              <div class="ts-fields">
                <label v-for="f in FIELDS" :key="f" class="ts-field">
                  <span class="ts-field-label">{{ FIELD_LABEL[f] }}</span>
                  <input
                    v-model="editForm[f]"
                    class="ts-input mono"
                    type="number"
                    step="any"
                    inputmode="decimal"
                    :aria-label="`${m.key} ${FIELD_LABEL[f]}`"
                    :placeholder="'当前 ' + (typeof m[f] === 'number' ? m[f] : '未设')"
                  >
                  <span v-if="fieldError(f)" class="ts-err">{{ fieldError(f) }}</span>
                </label>
              </div>
              <p class="ts-rule">
                校验：留空保持当前值；warn 下限 ≤ 上限、crit 下限 ≤ 上限；严重与警告区间不得重叠。
              </p>
              <p v-if="formError" class="ts-err ts-err-form" role="alert">{{ formError }}</p>
              <div class="ts-editor-foot">
                <button type="button" class="ts-btn" @click="resetEdit">取消</button>
                <button
                  type="button"
                  class="ts-btn ts-btn-primary"
                  :disabled="!canSubmit"
                  @click="saveEdit"
                >{{ saving ? '保存中…' : '保存' }}</button>
              </div>
            </div>
          </div>
        </div>
      </ObsPanel>
    </div>
  </div>
</template>

<style scoped>
.ts {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(0, 1.4fr);
  gap: 14px;
  align-items: start;
}
.ts-left, .ts-right { display: flex; flex-direction: column; gap: 14px; min-width: 0; }

/* ── profile hairline grid ── */
.ts-profile { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
.ts-cell { display: flex; flex-direction: column; gap: 3px; padding: 10px 14px;
  border-bottom: 1px solid var(--line); border-right: 1px solid var(--line); min-width: 0; }
.ts-cell-wide { grid-column: 1 / -1; border-right: 0; }
.ts-cell:nth-last-child(-n+1) { border-bottom: 0; }
.ts-k { font-size: 9.5px; letter-spacing: .1em; color: var(--tx-dim); }
.ts-v { font-size: 12.5px; color: var(--tx-hi); overflow-wrap: anywhere; }
.ts-asset-md { margin: 0; padding: 10px 14px; font-size: 11px; line-height: 1.65;
  color: var(--tx-dim); white-space: pre-wrap; max-height: 260px; overflow-y: auto;
  border-top: 1px solid var(--line); }
.ts-skeleton { height: 120px; }

/* ── downtime rows ── */
.ts-dt { padding: 4px 0; }
.ts-dt-row { display: flex; align-items: center; gap: 10px; padding: 7px 14px;
  border-bottom: 1px solid var(--line); font-size: 11.5px; color: var(--tx);
  font-variant-numeric: tabular-nums; }
.ts-dt-row:last-of-type { border-bottom: 0; }
.ts-dt-arrow { color: var(--tx-dim); }
.ts-dt-note { margin: 8px 14px 10px; font-size: 10.5px; color: var(--tx-dim); }

/* ── feedback ── */
.ts-fb { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.ts-fb-input {
  width: 100%; background: var(--bg2); border: 1px solid var(--line-2);
  border-radius: var(--r-s); color: var(--tx-hi); font-size: 12px;
  font-family: inherit; padding: 8px 10px; resize: vertical; min-height: 64px;
}
.ts-fb-input::placeholder { color: var(--tx-dim); }
.ts-fb-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ts-fb-hint { font-size: 10px; color: var(--tx-dim); }
.ts-fb-btn {
  display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 12px; padding: 7px 14px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px;
}
.ts-fb-btn:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.ts-fb-btn:disabled { opacity: .55; cursor: default; }

/* ── metrics + bands ── */
.ts-metrics { display: flex; flex-direction: column; }
.ts-metric { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.ts-metric:last-child { border-bottom: 0; }
.ts-metric-open { background: var(--bg2); }
.ts-band-row { display: flex; align-items: flex-start; gap: 12px; }
.ts-band-row > :first-child { flex: 1; min-width: 0; }
.ts-edit-btn {
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11px; padding: 5px 14px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px; flex-shrink: 0;
}
.ts-edit-btn:hover { border-color: var(--cyan-dim); color: var(--tx-hi); }

/* editor: inline under the band; errors immediate */
.ts-editor { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 10px; }
.ts-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px 14px; }
.ts-field { display: flex; flex-direction: column; gap: 4px; }
.ts-field-label { font-size: 10px; color: var(--tx-dim); letter-spacing: .06em; }
.ts-input {
  width: 100%; background: var(--bg1); border: 1px solid var(--line-2);
  border-radius: var(--r-s); color: var(--tx-hi); font-size: 12px; padding: 6px 9px;
  min-height: 32px;
}
.ts-input:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: 1px; }
.ts-err { font-size: 10.5px; color: var(--warn); line-height: 1.4; }
.ts-err-form { margin: 8px 0 0; }
.ts-rule { margin: 10px 0 0; font-size: 10.5px; color: var(--tx-dim); line-height: 1.5; }
.ts-editor-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.ts-btn {
  border: 1px solid var(--line-2); background: var(--bg2); color: var(--tx);
  font-size: 11.5px; padding: 6px 16px; border-radius: var(--r-s); cursor: pointer;
  min-height: 32px;
}
.ts-btn:hover:not(:disabled) { border-color: var(--cyan-dim); color: var(--tx-hi); }
.ts-btn:disabled { opacity: .5; cursor: default; }
.ts-btn-primary { color: var(--cyan); }
.ts-btn-primary:hover:not(:disabled) { color: var(--cyan); }

@media (max-width: 999.98px) {
  .ts { grid-template-columns: 1fr; }
}
</style>
