<script setup>
import { ref, computed } from 'vue'
import { useEndpoint } from '../api.js'
import { useLive } from '../live.js'
import { arrangeNodes } from '../fleet.js'
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

// P2-5. The controls are the summary line that was already here: the counts
// become the state filter, so there is no second vocabulary to learn and no
// second rule to keep in sync. health.php assigns `state` and increments
// `counts[state]` from the same value, so the number clicked and the cards it
// isolates cannot disagree - that agreement was itself a fixed defect ("0
// unknown" beside a card reading "? Unknown").
const STATES = ['ok', 'degraded', 'unknown']
const stateFilter = ref(null)
const query = ref('')

const shown = computed(() => arrangeNodes(nodes.value,
  { state: stateFilter.value, query: query.value }))

function toggleState (key) {
  stateFilter.value = stateFilter.value === key ? null : key
}

function clearFilters () {
  stateFilter.value = null
  query.value = ''
}

// Nodes exist, and the operator is looking at none of them. Distinct from "no
// nodes enrolled", which is wrong advice when the fleet is merely hidden.
const hiddenByFilters = computed(() => nodes.value.length > 0 && shown.value.length === 0)
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
    <!-- The counts are the filter (P2-5). A count of zero is not clickable -
         isolating a state with no members gives a blank grid and no way to tell
         it from a broken pane - but the ACTIVE one stays clickable whatever it
         reads, or a refresh that empties it would strand the operator inside a
         filter with nothing left to press to leave it. -->
    <div v-if="fleet && !dbUnreadable" class="um-fleetbar">
      <span>{{ fleet.nodes }} node(s):</span>
      <button v-for="key in STATES" :key="key" type="button"
              class="um-count" :class="{ 'um-count-on': stateFilter === key }"
              :aria-pressed="stateFilter === key"
              :disabled="fleet[key] === 0 && stateFilter !== key"
              @click="toggleState(key)">{{ fleet[key] }} {{ key }}</button>
      <input v-model="query" type="search" class="um-search"
             placeholder="Filter by name" aria-label="Filter nodes by name">
    </div>

    <!-- Same guard: "no nodes enrolled, go add one" is wrong advice when the
         real problem is that the database can't be read - nodes may well be
         enrolled (fix round 1, item 7). -->
    <p v-if="data && !dbUnreadable && !nodes.length">
      No nodes enrolled. Go to Settings → Utilities → Unraid-Manager to add one.
    </p>

    <p v-if="hiddenByFilters" class="um-hint">
      No node matches this filter.
      <button type="button" class="um-count" @click="clearFilters">
        Show all {{ nodes.length }}
      </button>
    </p>

    <div class="um-grid">
      <NodeCard v-for="node in shown" :key="node.id" :node="node" @open="open = $event" />
    </div>

    <NodeDrawer :node-id="open" @close="open = null" />
  </div>
</template>

<style>
.um-grid { display: grid; gap: .75rem;
           grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr)); }

/* Every colour here is a token, so the bar inherits whichever of Unraid's four
   themes is active (tokens.css). A literal would be a bug. */
.um-fleetbar { display: flex; flex-wrap: wrap; align-items: center;
               gap: .4rem; margin: 0 0 .75rem; }
.um-count { font: inherit; cursor: pointer; padding: .1rem .5rem;
            color: var(--um-fg); background: var(--um-surface);
            border: 1px solid var(--um-border); border-radius: .25rem; }
.um-count:disabled { cursor: default; opacity: .5; }
/* The active filter is marked by more than colour - a border weight and the
   accent together, so it is not the only signal for anyone who cannot see it. */
.um-count-on { border-color: var(--um-accent); box-shadow: inset 0 -2px var(--um-accent); }
.um-search { font: inherit; margin-left: auto; padding: .1rem .4rem;
             color: var(--um-fg); background: var(--um-bg);
             border: 1px solid var(--um-border); border-radius: .25rem; }
</style>
