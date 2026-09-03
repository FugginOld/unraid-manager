<script setup>
import { ref, computed } from 'vue'
import { useEndpoint } from '../api.js'
import { useLive } from '../live.js'
import { localTime } from '../time.js'
import { sortRows } from '../sort.js'

// Same memoised object App.vue and Overview.vue read (api.js): reading
// error/dbUnreadable here is a second READ of a shared endpoint, not a second
// fetch. useLive(refresh) adds this view's mount/unmount to the shared
// refcount - it does not open a second stream (live.js).
const { data, error, dbUnreadable, refresh } = useEndpoint('disks')
// The box's zone, straight off this endpoint's own payload - the Disks screen
// is reachable without Overview ever having loaded.
const tz = computed(() => data.value?.tz ?? null)
const clock12 = computed(() => data.value?.clock12 ?? false)
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
// disk behind it. It has no SMART status because there is nothing to ask,
// which is not the same as a disk that answered UNKNOWN. One spelling, used by
// both the cell and the filter button: with the literal repeated in the
// template, changing this function left the "No disk" filter selecting nothing
// and the whole suite green.
const NO_DISK = 'no disk'
const LIMITED = 'limited'
const NOT_YET = 'not assessed yet'

// The key the filters compare, so display text and filter value can never
// drift apart the way they did once already.
function verdictKey (disk) {
  if (disk.model === null) return NO_DISK
  if (disk.smart_tier !== 1) return LIMITED
  return disk.verdict || NOT_YET
}

// What the cell shows. A tier 0 node reports Unraid's OK|UNKNOWN and nothing
// behind it; the suffix exists so a tier 0 OK can never be read as an assessed
// one. A tier 1 row with no verdict yet gets a dash instead - that node CAN be
// assessed and simply has not been, and rendering the two alike would be the
// same absent-versus-unable defect this pane keeps closing.
function verdictText (disk) {
  const key = verdictKey(disk)
  if (key === LIMITED) return `${disk.smart_status || 'UNKNOWN'} (limited)`
  if (key === NOT_YET) return '—'
  return key
}

const VERDICT_CLASS = { OK: 'um-ok', WATCH: 'um-watch', FAIL: 'um-crit' }
function verdictClass (disk) {
  return VERDICT_CLASS[verdictKey(disk)] || 'um-unknown'
}

// Fix round 1, blocking 3: triage priority for the column's header click, the
// same asymmetry fleet.js's stateRank applies to node states. A real finding
// leads (FAIL before WATCH, since a failure outranks a warning); UNKNOWN is a
// claim the assessment could not make; OK is a clean result. Alphabetical
// order puts WATCH last, which is wrong for the one sort an operator actually
// wants on this column. Everything with no real verdict at all - tier 0,
// no-disk, not-yet-assessed - sorts after every value that has one, fleet.js's
// rule for a state it does not recognise, restated for a verdict this column
// cannot know.
const VERDICT_RANK = { FAIL: 0, WATCH: 1, UNKNOWN: 2, OK: 3 }
const VERDICT_LAST = Object.keys(VERDICT_RANK).length
function verdictRank (disk) {
  const rank = VERDICT_RANK[verdictKey(disk)]
  return rank === undefined ? VERDICT_LAST : rank
}

const expanded = ref(null)
function rowKey (disk) { return disk.node_id + ':' + disk.device }
function toggle (disk) {
  const key = rowKey(disk)
  expanded.value = expanded.value === key ? null : key
}

function sortBy (key) {
  if (sortKey.value === key) sortAsc.value = !sortAsc.value
  else { sortKey.value = key; sortAsc.value = true }
}

const rows = computed(() => {
  const all = (data.value?.disks ?? [])
    .filter(d => !nodeFilter.value || d.node === nodeFilter.value)
    .filter(d => !smartFilter.value || verdictKey(d) === smartFilter.value)
  // sort.js, not an inline comparator: a disk with no temperature used to
  // sort as if it were 0 C and lead an ascending sort, reading as the coldest
  // drive in the fleet (P1 triage P2-7). Pinned twice - as a function in
  // views.mjs, and through the header click in interact.mjs.
  return sortRows(all, sortKey.value === 'verdict' ? verdictRank : sortKey.value, sortAsc.value)
})

const anyTier0 = computed(() =>
  (data.value?.disks ?? []).some(d => d.smart_tier !== 1))

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
           column. Unraid reports OK|UNKNOWN and nothing else at Tier 0 - and
           gated on there still being one, so a fully Tier 1 fleet stops seeing
           a limit that no longer applies to it. -->
      <p v-if="anyTier0" class="um-hint">
        Unraid's API reports SMART health as OK or UNKNOWN only. Full SMART
        attributes, and the disk assessment they support, need a Tier 1 agent
        on each node.
      </p>

      <!-- Controller amendment C: two different facts, so two different
           sentences. fetched_at is null only for a node that has never been
           polled - on a freshly enrolled fleet that is EVERY node for up to
           ten minutes, which is expected and must not read as a failure. Split
           by domain too: a SMART call failing on a node whose disk list is
           fine is not "no disk list yet". -->
      <p v-for="entry in stale" :key="entry.node_id + ':' + entry.domain"
         class="um-node-stale">
        <template v-if="entry.domain === 'smart'">
          <template v-if="entry.fetched_at">
            {{ entry.node }}: showing the SMART assessment collected
            {{ localTime(entry.fetched_at, tz, clock12) }} — the latest agent call
            did not complete ({{ entry.error }}).
          </template>
          <template v-else>
            {{ entry.node }}: no SMART assessment yet — this node runs the agent
            but has not been polled for SMART. The inventory poll is slow; give
            it ten minutes.
          </template>
        </template>
        <template v-else>
          <template v-if="entry.fetched_at">
            {{ entry.node }}: showing the disk list collected
            {{ localTime(entry.fetched_at, tz, clock12) }} — the latest poll did not complete
            ({{ entry.error }}).
          </template>
          <template v-else>
            {{ entry.node }}: no disk list yet — this node has not been polled
            since it was enrolled. The inventory poll is slow; give it ten
            minutes.
          </template>
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
        <button type="button" @click="smartFilter = 'WATCH'">WATCH</button>
        <button type="button" @click="smartFilter = 'FAIL'">FAIL</button>
        <button type="button" @click="smartFilter = 'UNKNOWN'">UNKNOWN</button>
        <button type="button" @click="smartFilter = LIMITED">Tier 0 only</button>
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
            <th @click="sortBy('temp')">Temp &deg;C</th>
            <th @click="sortBy('array_status')">Array</th>
            <th @click="sortBy('errors')">Errors</th>
            <th @click="sortBy('verdict')">Verdict</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length"><td colspan="10">No disks reported yet.</td></tr>
          <!-- Two <tr> per disk (the row plus its collapsible reasons row)
               can't both carry the same v-for, so the pair is wrapped in a
               <template v-for> - the standard Vue 3 form for a row plus a
               detail row. device is unique per node and present on every row;
               model repeats across identical drives and is null on the
               orphans (controller amendment A). -->
          <template v-for="disk in rows" :key="rowKey(disk)">
            <tr @click="toggle(disk)">
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
              <td :class="verdictClass(disk)">{{ verdictText(disk) }}</td>
            </tr>
            <!-- The reason strings can carry a controller-supplied value (a
                 self-test result off the drive's own firmware), so this is
                 always plain {{ }} interpolation - never v-html. -->
            <tr v-if="expanded === rowKey(disk) && disk.reasons.length"
                :key="rowKey(disk) + ':why'">
              <td colspan="10">{{ disk.reasons.join(' · ') }}</td>
            </tr>
          </template>
        </tbody>
      </table>

      <!-- P1 exit finding F-5: every spare also appears in the table above,
           with a dash for slot/array/errors - `spares` is a subset of `disks`,
           not a separate set of hardware. Said out loud rather than solved by
           hiding rows: the table above is "every disk this box can see", and
           filtering it would make the two tables disagree about what exists.
           Raven has eleven disks and nine of them are spares. -->
      <h3>Spares</h3>
      <p class="um-hint">
        Unassigned disks. These also appear in the table above, where their
        slot, array status and error count read “—” because the array does not
        track them.
      </p>
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
