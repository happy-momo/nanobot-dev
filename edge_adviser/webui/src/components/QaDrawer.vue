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

const props = defineProps<{ open: boolean; assetId?: string }>()

const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <DialogRoot :open="props.open" @update:open="(v: boolean) => { if (!v) emit('close') }">
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
          <div class="qd-empty">
            <span class="eyebrow">问答（Task 8 接入）</span>
            <span class="qd-empty-hint">QaPanel 就位后此处渲染对话流与输入行。</span>
          </div>
          <!-- Task 8 mounts QaPanel here -->
          <slot />
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.qd-ov { position: fixed; inset: 0; background: rgba(2, 5, 10, 0.6);
  backdrop-filter: blur(2px); z-index: 60; }

.qd-panel { position: fixed; top: 0; right: 0; bottom: 0; width: 420px; max-width: 100vw;
  display: flex; flex-direction: column; background: var(--bg1);
  border-left: 1px solid var(--line-2); outline: none; z-index: 61;
  animation: qd-in 260ms cubic-bezier(0.2, 0.8, 0.2, 1); }

@keyframes qd-in { from { transform: translateX(24px); opacity: 0; }
  to { transform: none; opacity: 1; } }

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
/* data-state driven entrance/exit; the scoped keyframe covers the open case,
   this global block handles Reka's closed phase + reduced-motion */
.qd-panel[data-state="closed"] { display: none; }
@media (prefers-reduced-motion: reduce) {
  .qd-panel { animation: none !important; }
}
</style>
