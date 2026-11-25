<template>
  <div class="md-body" v-html="html" />
</template>

<script setup lang="ts">
// Safe markdown rendering (reports / knowledge — AI content). marked parses
// GFM (tables, lists); DOMPurify sanitizes — model output is untrusted content
// reaching the DOM via v-html, so the sanitize step is a security requirement,
// not a nicety. Both deps bundle locally (offline constraint holds).
// Carried over verbatim from the old Reports.vue implementation (git 69259d21).
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ source: string }>()

marked.setOptions({ gfm: true, breaks: true })

const html = computed(() =>
  DOMPurify.sanitize(marked.parse(props.source || '') as string))
</script>

<style scoped>
/* markdown typography on the observatory palette: hairline tables, cyan
   headings, violet ONLY on blockquote lines (AI voice quoting itself) */
.md-body { font-size: 13px; line-height: 1.7; color: var(--tx); min-width: 0;
  overflow-wrap: break-word; }
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) { color: var(--tx-hi); font-size: 13px; margin: 18px 0 8px;
  letter-spacing: .04em; font-weight: 600; }
.md-body :deep(h1) { font-size: 15px; }
.md-body :deep(h2) { border-bottom: 1px solid var(--line); padding-bottom: 5px; }
.md-body :deep(p) { margin: 0 0 10px; }
.md-body :deep(ul),
.md-body :deep(ol) { margin: 0 0 10px; padding-left: 20px; }
.md-body :deep(li) { margin: 3px 0; }
.md-body :deep(code) { font-family: var(--font-mono); font-size: 11.5px;
  background: var(--bg3); border-radius: 3px; padding: 1px 5px;
  color: var(--cyan); }
.md-body :deep(pre) { background: var(--bg2); border: 1px solid var(--line);
  border-radius: var(--r-s); padding: 10px 12px; overflow-x: auto; margin: 0 0 10px; }
.md-body :deep(pre code) { background: none; padding: 0; color: var(--tx); }
.md-body :deep(table) { border-collapse: collapse; width: 100%; margin: 0 0 12px;
  font-variant-numeric: tabular-nums; font-size: 12px; }
.md-body :deep(th),
.md-body :deep(td) { border: 1px solid var(--line); padding: 5px 9px;
  text-align: left; }
.md-body :deep(th) { background: var(--bg2); color: var(--tx-dim);
  font-weight: 600; font-size: 11px; letter-spacing: .05em; }
.md-body :deep(blockquote) { margin: 0 0 10px; padding: 2px 0 2px 12px;
  border-left: 2px solid var(--violet); color: var(--tx-hi); }
.md-body :deep(hr) { border: 0; border-top: 1px solid var(--line); margin: 14px 0; }
.md-body :deep(a) { color: var(--cyan); }
.md-body :deep(strong) { color: var(--tx-hi); }
.md-body :deep(img) { max-width: 100%; }
</style>
