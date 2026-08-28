<script setup>
import { ref, computed } from 'vue'
import { useEndpoint } from '../api.js'
import { useLive } from '../live.js'
import NodeCard from '../components/NodeCard.vue'
import NodeDrawer from '../components/NodeDrawer.vue'

// useEndpoint('health') is memoised: this returns the SAME data/refresh as
// App.vue's own call, so registering here just adds this view's own
// mount/unmount lifetime to the shared refcount - it does not open a second
// stream or double the fetch rate (live.js, useEndpoint doc comments). Taking
// loading and dbUnreadable off this same call (fix round 1, item 7) is a
// second READ of an already-shared object, not a second check or a second
// fetch.
const { data, refresh, loading, error, dbUnreadable } = useEndpoint('health')
// Controller amendment C: the "numbers are old" banner lives in App.vue now,
// page-wide (Task 12) - this view must not grow its own copy of it.
// useLive(refresh) is still called so this view's mount/unmount participates
// in the shared refcount the way every other caller does. The dbUnreadable
// short-circuit amendment C assumed App.vue already provided did not exist
// (App.vue renders <component :is> unconditionally) - fixed below instead by
// reading dbUnreadable here and gating on it directly.
useLive(refresh)
const open = ref(null)

const fleet = computed(() => data.value?.fleet ?? null)
const nodes = computed(() => data.value?.nodes ?? [])
</script>

<template>
  <div>
    <!-- fleet.js showed "Loading fleet..." from first paint; without this,
         a down managerd renders nothing at all here until App.vue's
         numbers-are-old banner fires at 180s (fix round 1, item 6). -->
    <!-- v-if="!data", not "!data && loading": loading flips false the moment
         the first refresh rejects, so gating on it left the pane rendering
         nothing at all from ~t+200ms until the 180s banner - the exact symptom
         item 6 named (round 2). error carries the reason when there is one. -->
    <p v-if="!data" class="um-hint">
      {{ error ? 'Could not reach the manager: ' + error : 'Loading…' }}
    </p>

    <!-- An unreadable database still returns {fleet: {nodes:0, ...}, nodes: [],
         db: false} (um_fleet_health(null)) - without the dbUnreadable guard
         this prints "0 node(s): 0 ok..." next to App.vue's "could not be
         read" banner, which is a second, wrong claim (fix round 1, item 7). -->
    <p v-if="fleet && !dbUnreadable">
      {{ fleet.nodes }} node(s): {{ fleet.ok }} ok, {{ fleet.degraded }} degraded,
      {{ fleet.unknown }} unknown.
    </p>

    <!-- Same guard: "no nodes enrolled, go add one" is wrong advice when the
         real problem is that the database can't be read - nodes may well be
         enrolled (fix round 1, item 7). -->
    <p v-if="data && !dbUnreadable && !nodes.length">
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
