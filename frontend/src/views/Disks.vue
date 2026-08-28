<script setup>
import { ref, computed } from 'vue'
import { useEndpoint } from '../api.js'
import { useLive } from '../live.js'

// Same memoised object App.vue and Overview.vue read (api.js): reading
// error/dbUnreadable here is a second READ of a shared endpoint, not a second
// fetch. useLive(refresh) adds this view's mount/unmount to the shared
// refcount - it does not open a second stream (live.js).
const { data, error, dbUnreadable, refresh } = useEndpoint('disks')
useLive(refresh)

const sortKey = ref('node')
const sortAsc = ref(true)
const nodeFilter = ref('')
const smartFilter = ref('')

function bytes (n) {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let value = Number(n)
  let i = 0
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++ }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

// null is "the array does not track this", never zero. 0 errors is a fact
// worth printing; a missing count is not a clean disk (NodeCard's unread
// null-vs-zero, one screen over).
function dash (v) { return v === null || v === undefined ? '—' : v }

// Controller amendment B: model === null is an array slot with no physical
// disk behind it - a drive that fell off the bus. It has no SMART status
// because there is nothing to ask, which is not the same as a disk that
// answered UNKNOWN. One spelling, used by both the cell and the filter button:
// with the literal repeated in the template, changing this function left the
// "No disk" filter selecting nothing and the whole suite green.
const NO_DISK = 'no disk'
function smartOf (disk) {
  if (disk.model === null) return NO_DISK
  return disk.smart_status || 'UNKNOWN'
}

function sortBy (key) {
  if (sortKey.value === key) sortAsc.value = !sortAsc.value
  else { sortKey.value = key; sortAsc.value = true }
}

const rows = computed(() => {
  const all = (data.value?.disks ?? [])
    .filter(d => !nodeFilter.value || d.node === nodeFilter.value)
    .filter(d => !smartFilter.value || smartOf(d) === smartFilter.value)
  const key = sortKey.value
  return [...all].sort((a, b) => {
    const x = a[key] ?? ''
    const y = b[key] ?? ''
    if (x === y) return 0
    return (x < y ? -1 : 1) * (sortAsc.value ? 1 : -1)
  })
})

const nodes = computed(() => [...new Set((data.value?.disks ?? []).map(d => d.node))])
const stale = computed(() => data.value?.stale ?? [])
const spares = computed(() => data.value?.spares ?? [])
</script>

<template>
  <div>
    <!-- Overview's item 6: gated on !data alone, never on loading, which flips
         false the moment the first refresh rejects and left the pane blank. -->
    <p v-if="!data" class="um-hint">
      {{ error ? 'Could not reach the manager: ' + error : 'Loading…' }}
    </p>

    <!-- Everything below is suppressed when the database cannot be read: the
         payload is then an empty fleet, and "no disks reported" next to the
         shell's "database could not be read" banner is a second, wrong claim
         (Task 13, item 7). -->
    <template v-if="data && !dbUnreadable">
      <!-- The operator should never have to wonder why there is no verdict
           column. Unraid reports OK|UNKNOWN and nothing else at Tier 0. -->
      <p class="um-hint">
        Unraid's API reports SMART health as OK or UNKNOWN only. Full SMART
        attributes, and the disk assessment they support, need a Tier 1 agent
        on each node.
      </p>

      <!-- Controller amendment C: two different facts, so two different
           sentences. fetched_at is null only for a node that has never been
           polled - on a freshly enrolled fleet that is EVERY node for up to
           ten minutes, which is expected and must not read as a failure. -->
      <p v-for="entry in stale" :key="entry.node_id" class="um-node-stale">
        <template v-if="entry.fetched_at">
          {{ entry.node }}: showing the disk list collected
          {{ entry.fetched_at }} — the latest poll did not complete
          ({{ entry.error }}).
        </template>
        <template v-else>
          {{ entry.node }}: no disk list yet — this node has not been polled
          since it was enrolled. The inventory poll is slow; give it ten
          minutes.
        </template>
      </p>

      <p v-if="nodes.length > 1">
        <button type="button" @click="nodeFilter = ''">All nodes</button>
        <button v-for="name in nodes" :key="name" type="button"
                @click="nodeFilter = name">{{ name }}</button>
      </p>

      <p>
        <button type="button" @click="smartFilter = ''">Any SMART</button>
        <button type="button" @click="smartFilter = 'OK'">OK</button>
        <button type="button" @click="smartFilter = 'UNKNOWN'">UNKNOWN</button>
        <button type="button" @click="smartFilter = NO_DISK">No disk</button>
      </p>

      <table class="tablesorter">
        <thead>
          <tr>
            <th @click="sortBy('node')">Node</th>
            <th @click="sortBy('slot')">Slot</th>
            <th @click="sortBy('model')">Model</th>
            <th @click="sortBy('device')">Device</th>
            <th @click="sortBy('vendor')">Vendor</th>
            <th @click="sortBy('size')">Size</th>
            <th @click="sortBy('temp')">Temp</th>
            <th @click="sortBy('array_status')">Array</th>
            <th @click="sortBy('errors')">Errors</th>
            <th @click="sortBy('smart_status')">SMART</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length"><td colspan="10">No disks reported yet.</td></tr>
          <!-- device is unique per node and present on every row; model
               repeats across identical drives and is null on the orphans
               (controller amendment A). -->
          <tr v-for="disk in rows" :key="disk.node_id + ':' + disk.device">
            <td>{{ disk.node }}</td>
            <td>{{ dash(disk.slot) }}</td>
            <!-- A word, not only a colour: the array claims this slot and no
                 physical disk answered for it. -->
            <td v-if="disk.model === null" class="um-warn">no disk present</td>
            <td v-else>{{ disk.model }}</td>
            <td>{{ dash(disk.device) }}</td>
            <td>{{ dash(disk.vendor) }}</td>
            <td>{{ bytes(disk.size) }}</td>
            <td>{{ dash(disk.temp) }}</td>
            <td>{{ dash(disk.array_status) }}</td>
            <td :class="{ 'um-warn': disk.errors > 0,
                          'um-unknown': disk.errors === null || disk.errors === undefined }">
              {{ dash(disk.errors) }}
            </td>
            <td :class="smartOf(disk) === 'OK' ? 'um-ok' : 'um-unknown'">
              {{ smartOf(disk) }}
            </td>
          </tr>
        </tbody>
      </table>

      <h3>Spares</h3>
      <table class="tablesorter">
        <thead><tr><th>Node</th><th>Model</th><th>Device</th><th>Vendor</th><th>Size</th></tr></thead>
        <tbody>
          <tr v-if="!spares.length">
            <td colspan="5">No unassigned disks anywhere in the fleet.</td>
          </tr>
          <tr v-for="spare in spares" :key="spare.node_id + ':' + spare.device">
            <td>{{ spare.node }}</td>
            <td>{{ dash(spare.model) }}</td>
            <td>{{ dash(spare.device) }}</td>
            <td>{{ dash(spare.vendor) }}</td>
            <td>{{ bytes(spare.size) }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </div>
</template>

<style>
.um-node-stale { padding: .35rem .5rem; margin-bottom: .5rem;
                 border: 1px solid var(--um-border); }
</style>
