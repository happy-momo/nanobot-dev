<script setup lang="ts">
// QaDrawer — the global right-side QA drawer (spec §4/§5.4, root fix A: it
// carries the current asset context so QA never loses `?asset=` again).
// Reka Dialog hosts it; 420px panel slides in from the right with a dimmed
// backdrop (the one place backdrop-filter is allowed, spec §1.1).
//
// Task 4 owns the container only: the body is a placeholder until QaPanel
// lands (Task 8) — the <slot> below is where it mounts.
import { DialogPortal, DialogRoot, DialogContent, DialogOverlay, DialogTitle,
  DialogDescription, DialogClose } from 'reka-ui'
import { X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import QaPanel from './QaPanel.vue'

const props = defineProps<{ open: boolean; assetId?: string }>()

const emit = defineEmits<{ (e: 'close'): void }>()

// ------------------------------------------------------- slide-out on close --
// App keeps this drawer mounted (v-if-less) and unmounts only on `close`, so we
// own the exit here: intercept the close request, flip the Reka root to closed
// (data-state=closed → qd-out plays), and only then tell App to unmount. Emitting
// `close` immediately was a hard cut — Presence reads the computed animation-name
// while the tree is already tearing down, so the 260ms exit never rendered.
const EXIT_MS = 260
const closing = ref(false)
let exitTimer = 0

const rootOpen = computed(() => props.open && !closing.value)

// reduced motion: the stylesheet drops the animation, so there is nothing to
// wait for — unmount on the spot (no flash of a static panel).
const exitMs = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  ? 0
  : EXIT_MS

watch(() => props.open, (open) => {
  if (open) {
    // reopening mid-exit (e.g. openQa from a view while it slides out)
    window.clearTimeout(exitTimer)
    closing.value = false
    return
  }
  if (closing.value) return   // exit already in flight
  requestClose()
})

/** begin the exit, then hand the unmount to App once the 260ms slide-out is done */
function requestClose() {
  closing.value = true
  exitTimer = window.setTimeout(() => {
    closing.value = false
    emit('close')
  }, exitMs)
}

onBeforeUnmount(() => window.clearTimeout(exitTimer))
</script>

<template>
  <DialogRoot :open="rootOpen" @update:open="(v: boolean) => { if (!v) requestClose() }">
    <DialogPortal>
      <DialogOverlay class="qd-ov" />
      <DialogContent class="qd-panel" :aria-describedby="undefined">
        <header class="qd-head">
          <div class="qd-titles">
            <span class="eyebrow">QA — ASK ADVISER</span>
            <DialogTitle class="qd-title">
              {{ props.assetId || '选择资产' }}
            </DialogTitle>
            <DialogDescription class="qd-desc">
              问答会话按资产分线程保存；门禁拒绝时提示可“允许旧数据”。
            </DialogDescription>
          </div>
          <DialogClose class="qd-x" aria-label="关闭问答抽屉">
            <X :size="16" :stroke-width="1.5" />
          </DialogClose>
        </header>

        <div class="qd-body">
          <QaPanel :asset-id="props.assetId" />
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.qd-ov { position: fixed; inset: 0; background: rgba(2, 5, 10, 0.6);
  backdrop-filter: blur(2px); z-index: 60;
  animation: qd-ov-in 260ms cubic-bezier(0.2, 0.8, 0.2, 1); }

@keyframes qd-ov-in { from { opacity: 0; } to { opacity: 1; } }

.qd-panel { position: fixed; top: 0; right: 0; bottom: 0; width: 420px; max-width: 100vw;
  display: flex; flex-direction: column; background: var(--bg1);
  border-left: 1px solid var(--line-2); outline: none; z-index: 61;
  animation: qd-in 260ms cubic-bezier(0.2, 0.8, 0.2, 1); }

@keyframes qd-in { from { transform: translateX(24px); opacity: 0; }
  to { transform: none; opacity: 1; } }

@keyframes qd-out { from { transform: none; opacity: 1; }
  to { transform: translateX(24px); opacity: 0; } }

.qd-head { display: flex; align-items: flex-start; justify-content: space-between;
  gap: 10px; padding: 14px; border-bottom: 1px solid var(--line); flex-shrink: 0; }
.qd-titles { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.qd-title { margin: 0; font-size: 15px; font-weight: 600; color: var(--tx-hi);
  font-family: var(--font-mono); letter-spacing: 0.5px; }
.qd-desc { font-size: 11px; color: var(--tx-dim); }

.qd-x { flex-shrink: 0; width: 30px; height: 30px; display: inline-flex;
  align-items: center; justify-content: center; border: 1px solid var(--line);
  border-radius: var(--r-s); background: transparent; color: var(--tx-dim);
  cursor: pointer; padding: 0; }
.qd-x:hover { background: var(--bg2); color: var(--tx-hi); }

.qd-body { flex: 1; min-height: 0; overflow: hidden auto; padding: 14px; }
.qd-empty { display: flex; flex-direction: column; gap: 6px; align-items: center;
  padding: 40px 12px; text-align: center; }
.qd-empty-hint { font-size: 11px; color: var(--tx-dim); }
</style>

<style>
/* data-state driven exit phase: the scoped block owns the open-state styles,
   this global block handles Reka's closed phase + reduced motion. */
.qd-panel[data-state="closed"] { animation: qd-out 260ms cubic-bezier(0.2, 0.8, 0.2, 1) forwards; }

.qd-ov[data-state="closed"] { animation: qd-ov-out 260ms cubic-bezier(0.2, 0.8, 0.2, 1) forwards; }
@keyframes qd-ov-out { from { opacity: 1; } to { opacity: 0; } }

@media (prefers-reduced-motion: reduce) {
  .qd-panel, .qd-ov { animation: none !important; }
}
</style>
