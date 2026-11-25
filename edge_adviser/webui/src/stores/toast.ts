// Toast store — the Element-free replacement for ElMessage (element-plus was
// dropped in the rewrite). `toast()` is a plain function callable from anywhere
// (views, stores, the api layer); messages land in `items`, which the
// ToastHost component (Task 4) renders. Until a host exists (and before pinia
// is installed) the message falls back to the console so nothing is swallowed.
//
// Tones map onto the design tokens: info → --cyan (deterministic fact),
// warn → --warn, crit → --crit. Success copy keeps tone 'info' (cyan reads as
// "确定/完成" in this design system, green is reserved for state dots).

import { defineStore } from 'pinia'
import { ref } from 'vue'

export type ToastTone = 'info' | 'warn' | 'crit'

export interface ToastItem {
  id: number
  msg: string
  tone: ToastTone
  ts: number
}

const AUTO_DISMISS_MS = 4000
// a burst of failures (e.g. every asset's alerts 401 at once) must not stack
// a wall of toasts — keep the newest few
const MAX_VISIBLE = 6

let _seq = 0

export const useToastStore = defineStore('toast', () => {
  const items = ref<ToastItem[]>([])

  function dismiss(id: number) {
    const i = items.value.findIndex((t) => t.id === id)
    if (i >= 0) items.value.splice(i, 1)
  }

  function push(msg: string, tone: ToastTone = 'info'): number {
    const id = ++_seq
    items.value.push({ id, msg, tone, ts: Date.now() })
    if (items.value.length > MAX_VISIBLE) {
      items.value.splice(0, items.value.length - MAX_VISIBLE)
    }
    window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
    return id
  }

  return { items, push, dismiss }
})

/** Fire-and-forget notification. Safe before pinia is installed (console fallback). */
export function toast(msg: string, tone: ToastTone = 'info'): void {
  try {
    useToastStore().push(msg, tone)
  } catch {
    console.warn(`[toast:${tone}] ${msg}`)
  }
}
