<script setup lang="ts">
// ToastHost — the Element-free toast surface (replacement for ElMessage; the
// api layer raises notifications via stores/toast.ts, this component renders
// them). Reka Toast in its CONTROLLED form:
//
//   ToastProvider (+ ToastViewport)  → mounts the viewport <ol> bottom-right
//   ToastRoot  :open="h.open"        → one root per store item; a closing entry
//                                      runs a 160ms exit fade, then is evicted
//                                      from `hosted` (unmount → Reka's
//                                      positioner stack drops the entry).
//
// WHY NOT `toast`-prop mode: ToastRoot mounts only while the supplied toast
// object EXISTS, and stores/toast.dismiss() removes items immediately — the
// root would unmount before Reka plays any closed phase, so the exit animation
// would never show. Controlled open + a local evict delay keeps the same
// dismiss semantics WITH the animation.
//
// Visual: hairline panel (--bg1 / 1px --line / --r-s), left border 2px in the
// tone color (info→cyan, warn→amber, crit→red, per stores/toast.ts).
import { ref, watch } from 'vue'
import { ToastProvider, ToastViewport, ToastRoot, ToastClose } from 'reka-ui'
import { X } from '@lucide/vue'
import { useToastStore, type ToastItem, type ToastTone } from '../stores/toast'

const store = useToastStore()

const OPEN_MS = 4000      // keep in step with stores/toast AUTO_DISMISS_MS
const EXIT_MS = 160

/** a store item mirrored into Reka: `open` drives Presence, `closing` guards
 * double-finish (store timeout AND user close can both fire). */
interface Hosted { item: ToastItem; open: boolean; closing: boolean }

const hosted = ref<Hosted[]>([])

// store items ↔ hosted entries: every item gets an open entry; an entry whose
// item left the store (the AUTO_DISMISS timeout in stores/toast.ts) starts the
// closing phase (open=false → Reka plays the exit → evict after EXIT_MS)
watch(
  () => store.items.map((t) => t.id).join(','),
  (ids) => {
    const idList = ids ? ids.split(',') : []
    for (const t of store.items) {
      if (!hosted.value.some((h) => h.item.id === t.id)) {
        hosted.value.push({ item: t, open: true, closing: false })
      }
    }
    for (const h of hosted.value) {
      if (!idList.includes(String(h.item.id)) && !h.closing) finish(h)
    }
  },
  { immediate: true },
)

function finish(h: Hosted) {
  h.closing = true
  h.open = false
  store.dismiss(h.item.id)   // idempotent (dismiss on a missing id is a no-op)
  window.setTimeout(() => {
    const i = hosted.value.findIndex((x) => x.item.id === h.item.id)
    if (i >= 0) hosted.value.splice(i, 1)
  }, EXIT_MS)
}

const TONE_VAR: Record<ToastTone, string> = {
  info: 'var(--cyan)',
  warn: 'var(--warn)',
  crit: 'var(--crit)',
}

const toneVar = (t: ToastTone) => ({ '--tone': TONE_VAR[t] ?? 'var(--cyan)' })
</script>

<template>
  <ToastProvider :duration="OPEN_MS" swipe-direction="right" label="通知">
    <ToastViewport class="th-viewport" />
    <ToastRoot
      v-for="h in hosted"
      :key="h.item.id"
      :open="h.open"
      :duration="OPEN_MS"
      type="foreground"
      class="th-root mono"
      :style="toneVar(h.item.tone)"
      @update:open="(v: boolean) => { if (!v) finish(h) }"
    >
      <span class="th-msg">{{ h.item.msg }}</span>
      <ToastClose class="th-x" aria-label="关闭通知">
        <X :size="13" :stroke-width="1.5" />
      </ToastClose>
    </ToastRoot>
  </ToastProvider>
</template>

<style scoped>
.th-viewport { position: fixed; bottom: 16px; right: 16px; z-index: 90;
  display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
  margin: 0; padding: 0; list-style: none; outline: none;
  max-width: min(360px, calc(100vw - 32px)); }

.th-root { display: flex; align-items: flex-start; gap: 10px;
  background: var(--bg1); border: 1px solid var(--line-2);
  border-left: 2px solid var(--tone, var(--cyan));
  border-radius: var(--r-s); padding: 9px 12px; min-width: 240px; max-width: 360px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.45); cursor: default;
  transition: opacity 160ms ease, transform 160ms ease; }
.th-root[data-state="closed"] { opacity: 0; transform: translateX(10px); }

.th-msg { flex: 1; min-width: 0; font-size: 12px; line-height: 1.5; color: var(--tx-hi);
  word-break: break-word; }

.th-x { flex-shrink: 0; width: 20px; height: 20px; margin: -2px -4px 0 0;
  display: inline-flex; align-items: center; justify-content: center;
  border: none; background: transparent; color: var(--tx-dim); cursor: pointer;
  padding: 0; border-radius: var(--r-s); }
.th-x:hover { color: var(--tx-hi); background: var(--bg2); }

/* swipe-out hint (Reka drives --reka-toast-swipe-move-*) */
.th-root[data-swipe="move"] { transform: translateX(var(--reka-toast-swipe-move-x, 0));
  transition: none; }
</style>

<style>
/* focus ring for the focusable toast root (tokens.css covers button/input but
   not [tabindex] li) */
.th-root:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: 1px; }
</style>
