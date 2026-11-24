<script setup lang="ts">
// ScopeBadge — the DATA-SCOPE provenance badge (spec §5.2 报告 tab). Cyan
// family throughout: this is the machine-generated fact block the LLM was
// TOLD to reference (report/badge.py builds it from the deterministic
// analytics layer) — facts are cyan in this design system, never violet.
//
// `meta` is the report's front-matter (server MetaData, rendered by
// report/badge.py render_frontmatter). Every field is optional/nullable —
// reports persist across schema versions, so missing keys read "—" instead
// of crashing the tab.
//
// Honest copy: the low-trust note states what the data is, not what the
// report concluded — the report's interpretation lives in the markdown body
// below this badge (deterministic provenance vs AI output stay separated).
import { computed } from 'vue'
import { stateMeta, trustMeta, type AssetState, type TrustLevel } from '../theme/meta'

const props = defineProps<{ meta: Record<string, any> | null | undefined }>()

const m = computed<Record<string, any>>(() => props.meta || {})

/** backend AssetState vocabulary → the design-system spelling (meta.ts) */
const STATE_KEY: Record<string, AssetState> = {
  healthy: 'healthy',
  partial: 'insufficient',          // data present but coverage below threshold
  insufficient: 'insufficient',
  unknown: 'unknown',
  offline: 'offline',
  stale: 'stale',
  planned_downtime: 'planned_downtime',
}

const TRUST_KEY: Record<string, TrustLevel> = {
  full: 'high', partial: 'medium', low: 'low', none: 'low',
}

const status = computed<AssetState>(() => STATE_KEY[String(m.value.status || '')] || 'unknown')
const statusLabel = computed(() => stateMeta[status.value].label)
const statusColor = computed(() => stateMeta[status.value].color)

const trustLevel = computed<TrustLevel>(() => TRUST_KEY[String(m.value.trust || '')] || 'low')
const trustLabel = computed(() => trustMeta[trustLevel.value].label)
const trustColor = computed(() => trustMeta[trustLevel.value].color)

/** coverage arrives as a 0–1 fraction (backend); tolerate a percent form too.
 * Missing field renders "—" (a confident 0% would misstate the provenance). */
const coveragePct = computed<string>(() => {
  const raw = m.value.coverage
  if (raw === undefined || raw === null || raw === '') return '—'
  const v = Number(raw)
  if (!Number.isFinite(v)) return '—'
  const p = v <= 1 ? v * 100 : v
  return String(Math.round(Math.min(Math.max(p, 0), 100)))
})

/** samples: actual vs expected — the honest denominator (badge.py carries both) */
const samples = computed(() => {
  const n = m.value.sample_count
  const exp = m.value.expected_samples
  const a = Number.isFinite(Number(n)) ? Number(n) : null
  const b = Number.isFinite(Number(exp)) ? Number(exp) : null
  if (a === null) return '—'
  return b !== null && b > 0 ? `${a} / ${b}` : String(a)
})

/** window: iso strings sliced to second precision, "→" between */
const window = computed(() => {
  const s = String(m.value.window_start || '').slice(0, 19)
  const e = String(m.value.window_end || '').slice(0, 19)
  if (!s && !e) return '—'
  return `${s || '—'} → ${e || '—'}`
})

const provider = computed(() => String(m.value.provider || '') || '—')
const dataSource = computed(() => String(m.value.data_source || '') || '—')

/** generated_at is a UTC iso → local wall clock (same rule as TabLive) */
const generatedAt = computed(() => {
  const t = m.value.generated_at ? new Date(String(m.value.generated_at)).getTime() : NaN
  if (!Number.isFinite(t)) return '—'
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
})

const degraded = computed(() => m.value.degraded === true || m.value.degraded === 'true')

/** the gate verdict word for the corner note — data facts, not conclusions */
const lowTrust = computed(() =>
  status.value !== 'healthy' || trustLevel.value === 'low' || degraded.value)
</script>

<template>
  <div class="sb" role="group" aria-label="数据范围（机器生成）">
    <div class="sb-head">
      <span class="sb-title">DATA-SCOPE · 数据范围</span>
      <span class="sb-machine">机器生成</span>
    </div>

    <div class="sb-grid mono">
      <div class="sb-cell">
        <span class="sb-k">状态</span>
        <span class="sb-v" :style="{ color: statusColor }">{{ statusLabel }}</span>
      </div>
      <div class="sb-cell">
        <span class="sb-k">可信度</span>
        <span class="sb-v" :style="{ color: trustColor }">{{ trustLabel }}</span>
      </div>
      <div class="sb-cell">
        <span class="sb-k">样本数</span>
        <span class="sb-v">{{ samples }}</span>
      </div>
      <div class="sb-cell">
        <span class="sb-k">覆盖率</span>
        <span class="sb-v">{{ coveragePct === '—' ? '—' : coveragePct + '%' }}</span>
      </div>
      <div class="sb-cell sb-wide">
        <span class="sb-k">时间窗</span>
        <span class="sb-v">{{ window }}</span>
      </div>
      <div class="sb-cell">
        <span class="sb-k">Provider</span>
        <span class="sb-v">{{ provider }}</span>
      </div>
      <div class="sb-cell">
        <span class="sb-k">数据源</span>
        <span class="sb-v">{{ dataSource }}</span>
      </div>
      <div class="sb-cell">
        <span class="sb-k">降级</span>
        <span class="sb-v" :style="{ color: degraded ? 'var(--warn)' : undefined }">
          {{ degraded ? '已降级' : '未降级' }}
        </span>
      </div>
      <div class="sb-cell">
        <span class="sb-k">生成时间</span>
        <span class="sb-v">{{ generatedAt }}</span>
      </div>
    </div>

    <p v-if="lowTrust" class="sb-note">
      注意：本报告基于低可信 / 不完整 / 已降级数据，结论需谨慎，建议先核查数据采集。
    </p>
  </div>
</template>

<style scoped>
/* cyan family — deterministic provenance (spec §5.2: DATA-SCOPE 青色系) */
.sb { border: 1px solid var(--cyan-dim); background: var(--cyan-dim);
  border-radius: var(--r-m); overflow: hidden; }
.sb-head { display: flex; align-items: center; gap: 8px;
  padding: 8px 14px; border-bottom: 1px solid var(--cyan-dim); }
.sb-title { font-size: 9px; letter-spacing: 2px; text-transform: uppercase;
  font-weight: 600; color: var(--cyan); font-family: var(--font-mono); }
.sb-machine { margin-left: auto; font-size: 9.5px; letter-spacing: .1em;
  color: var(--tx-dim); }

.sb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px 18px; padding: 10px 14px 12px; }
.sb-cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sb-wide { grid-column: 1 / -1; }
.sb-k { font-size: 9.5px; letter-spacing: .1em; color: var(--tx-dim); }
.sb-v { font-size: 12px; color: var(--tx-hi); font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere; }

.sb-note { margin: 0; padding: 8px 14px 10px; font-size: 11.5px; color: var(--warn);
  border-top: 1px solid var(--cyan-dim); line-height: 1.55; }
</style>
