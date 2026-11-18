<template>
  <div class="page-header">
    <div class="left">
      <h2 class="title">{{ title }}</h2>
      <span v-if="subtitle" class="subtitle muted">{{ subtitle }}</span>
    </div>
    <div class="right"><slot /></div>
  </div>
</template>

<script setup lang="ts">
// Consistent page scaffolding: title + subtitle on the left, actions/filters on the right.
defineProps<{ title: string; subtitle?: string }>()
</script>

<style scoped>
.page-header {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 12px; margin-bottom: 14px; flex-wrap: wrap;
}
.title { margin: 0; font-size: 18px; font-weight: 700; }
.subtitle { font-size: 12px; margin-left: 10px; }
.right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
</style>
