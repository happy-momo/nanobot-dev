<script setup lang="ts">
// /qa — fullscreen QA (spec §5.4: the same QaPanel the drawer hosts, laid out
// full-width). No assetId prop → QaPanel shows its Reka Select asset picker.
import QaPanel from '../components/QaPanel.vue'
</script>

<template>
  <div class="page qa-page">
    <QaPanel fullscreen />
  </div>
</template>

<style scoped>
.qa-page { display: flex; flex-direction: column; min-height: 0; flex: 1;
  padding: 16px 18px; overflow: hidden auto; }
</style>
