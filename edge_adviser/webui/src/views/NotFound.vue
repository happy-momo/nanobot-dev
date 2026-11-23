<!-- Legacy deep-link landing (router catchall /:pathMatch(.*)*): the old route
     table had /asset-hub /monitor /reports /knowledge /feedback /assets /runlog
     — bookmarks into those paths now land here instead of a router warning.
     Renders a blank panel; the rail stays usable. -->
<script setup lang="ts">
</script>

<template>
  <div class="stub">
    <span class="eyebrow">PAGE NOT FOUND</span>
  </div>
</template>

<style scoped>
.stub { display: flex; align-items: center; justify-content: center; padding: 60px 0; }
</style>
