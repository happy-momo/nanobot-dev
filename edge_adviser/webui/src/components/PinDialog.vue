<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogRoot,
  AlertDialogTitle,
} from 'reka-ui'

const props = defineProps<{
  open: boolean
  busy?: boolean
  authError?: string
}>()
const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'submit', pin: string): void
}>()

const pin = ref('')
const error = ref('')

watch(() => props.open, (open) => {
  if (open) {
    pin.value = ''
    error.value = ''
  }
})

function submit(): void {
  if (props.busy) return
  const value = pin.value.trim()
  if (!value) {
    error.value = '请输入 PIN 码'
    return
  }
  emit('submit', value)
}
</script>

<template>
  <AlertDialogRoot :open="open" @update:open="(v: boolean) => { if (!v) emit('cancel') }">
    <AlertDialogPortal>
      <AlertDialogOverlay class="pin-ov" />
      <AlertDialogContent class="pin-dialog" :aria-describedby="undefined">
        <AlertDialogTitle class="pin-title">请输入现场访问 PIN</AlertDialogTitle>
        <AlertDialogDescription class="pin-desc">
          PIN 用于解锁当前服务；取消后可在需要时再次重试。
        </AlertDialogDescription>
        <label class="pin-label">
          <span class="eyebrow">PIN</span>
          <input
            v-model="pin"
            class="pin-input mono"
            type="password"
            autocomplete="current-password"
            inputmode="numeric"
            autofocus
            @input="error = ''"
            @keydown.enter.prevent="submit"
          />
        </label>
        <p v-if="error || authError" class="pin-error">{{ error || authError }}</p>
        <div class="pin-actions">
          <AlertDialogCancel as-child>
            <button type="button" class="pin-btn" :disabled="busy" @click="emit('cancel')">取消</button>
          </AlertDialogCancel>
          <button type="button" class="pin-btn pin-btn-primary" :disabled="busy" @click="submit">
            {{ busy ? '验证中…' : '确认' }}
          </button>
        </div>
      </AlertDialogContent>
    </AlertDialogPortal>
  </AlertDialogRoot>
</template>

<style scoped>
.pin-ov { position: fixed; inset: 0; z-index: 79;
  background: rgba(2, 5, 10, 0.72); }
.pin-dialog { position: fixed; z-index: 80; left: 50%; top: 50%;
  width: 390px; max-width: calc(100vw - 32px); transform: translate(-50%, -50%);
  display: flex; flex-direction: column; gap: 10px; padding: 16px;
  background: var(--bg1); border: 1px solid var(--line-2); border-radius: var(--r-m);
  outline: none; }
.pin-title { margin: 0; font-size: 14px; color: var(--tx-hi); }
.pin-desc { margin: 0; font-size: 11.5px; line-height: 1.5; color: var(--tx-dim); }
.pin-label { display: flex; flex-direction: column; gap: 5px; }
.pin-input { width: 100%; height: 36px; box-sizing: border-box;
  border: 1px solid var(--line-2); border-radius: var(--r-s);
  background: var(--bg2); color: var(--tx-hi); padding: 7px 10px; }
.pin-input:focus-visible { outline: 2px solid var(--cyan-dim); outline-offset: 1px; }
.pin-error { margin: 0; color: var(--crit); font-size: 11px; }
.pin-actions { display: flex; justify-content: flex-end; gap: 8px; }
.pin-btn { min-height: 32px; padding: 6px 12px; border: 1px solid var(--line);
  border-radius: var(--r-s); background: transparent; color: var(--tx); cursor: pointer; }
.pin-btn:disabled { opacity: .55; cursor: wait; }
.pin-btn-primary { border-color: rgba(59, 224, 216, .35); background: var(--cyan-dim);
  color: var(--cyan); }
</style>
