<script setup>
import { ref, computed } from 'vue'
import { useEndpoint } from '../api.js'
import { useLive } from '../live.js'
import NodeCard from '../components/NodeCard.vue'
import NodeDrawer from '../components/NodeDrawer.vue'

// useEndpoint('health') is memoised: this returns the SAME data/refresh as
// App.vue's own call, so registering here just adds this view's own
// mount/unmount lifetime to the shared refcount - it does not open a second
// stream or double the fetch rate (live.js, useEndpoint doc comments).
const { data, refresh } = useEndpoint('health')
// Controller amendment C: the "numbers are old" banner and the
// dbUnreadable check both live in App.vue now, page-wide (Task 12) - this
// view must not grow its own copy of either. useLive(refresh) is still
// called so this view's mount/unmount participates in the shared refcount
// the way every other caller does.
useLive(refresh)
const open = ref(null)

const fleet = computed(() => data.value?.fleet ?? null)
const nodes = computed(() => data.value?.nodes ?? [])
</script>

<template>
  <div>
    <p v-if="fleet">
      {{ fleet.nodes }} node(s): {{ fleet.ok }} ok, {{ fleet.degraded }} degraded,
      {{ fleet.unknown }} unknown.
    </p>

    <p v-if="data && !nodes.length">
      No nodes enrolled. Go to Settings → Utilities → Unraid-Manager to add one.
    </p>

    <div class="um-grid">
      <NodeCard v-for="node in nodes" :key="node.id" :node="node" @open="open = $event" />
    </div>

    <NodeDrawer :node-id="open" @close="open = null" />
  </div>
</template>

<style>
.um-grid { display: grid; gap: .75rem;
           grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr)); }
</style>
