<template>
  <div class="md-body" v-html="html" />
</template>

<script setup lang="ts">
// Safe markdown rendering shared by Reports / Qa / Knowledge.
// marked parses GFM (tables, lists); DOMPurify sanitizes — model output is
// untrusted content and reaches the DOM via v-html, so sanitize is a security
// requirement, not a nicety. All deps bundle locally (offline constraint holds).
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ source: string }>()

marked.setOptions({ gfm: true, breaks: true })

const html = computed(() =>
  DOMPurify.sanitize(marked.parse(props.source || '') as string))
</script>
