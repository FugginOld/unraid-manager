<script setup>
import { computed, ref } from 'vue'
import { useEndpoint } from './api.js'
import { useLive, STALE_MS } from './live.js'
import Overview from './views/Overview.vue'
import Disks from './views/Disks.vue'
import Drift from './views/Drift.vue'

// Three tabs is not a routing problem. A router would add a dependency, a
// history integration and a URL scheme to a shell that already owns the URL.
const TABS = [
  { id: 'overview', label: 'Overview', component: Overview },
  { id: 'disks', label: 'Disks', component: Disks },
  { id: 'drift', label: 'Drift', component: Drift },
]
const active = ref('overview')

// The shell's own heartbeat. One endpoint is enough to know whether the
// database is readable and whether managerd is answering at all, and both
// facts belong above the tabs, not inside whichever view happens to be
// active - P0's stale banner was page-wide, and an Overview-only one would go
// dark the moment an operator switched to Disks or Drift while managerd was
// down. Tasks 13-15's own useEndpoint() calls share this same db check for
// their own views; useLive() is a module singleton, so this call does not
// open a second stream on top of theirs.
const { data, dbUnreadable, refresh } = useEndpoint('health')
// `unreachable` is the transport clock: how long since this pane got ANY
// response. It is not the same question as "is the data current", and on its
// own it was the P1 exit's blocking defect (F-1): health.php reads only the
// database, so with managerd stopped it keeps answering 200 with yesterday's
// rows, lastGood stamps forward every 30s, and this could never fire. Three
// minutes with the daemon dead produced no banner at all on Raven.
const { stale: unreachable, lastGood } = useLive(refresh)

// The data clock, measured by the server against its own clock (health.php's
// um_fleet_age) so a skewed browser clock cannot banner a healthy fleet or
// hide a dead one. null means nothing has ever been collected - a fleet
// enrolled a minute ago, which must NOT banner.
const age = computed(() => data.value?.age ?? null)
// The server formats this one, in the BOX's zone: Unraid runs PHP with
// date.timezone unset, and toLocaleString() here would render the VIEWER's
// zone, which is only coincidentally the same. Falls back to the raw instant
// rather than to nothing.
const newest = computed(() => data.value?.newest_local ?? data.value?.newest ?? 'never')
const dataStale = computed(() => age.value !== null && age.value * 1000 > STALE_MS)
const stale = computed(() => unreachable.value || dataStale.value)

function minutesOld (seconds) {
  const mins = Math.floor(seconds / 60)
  return mins < 60 ? `${mins} minutes` : `${Math.floor(mins / 60)} hours`
}
</script>

<template>
  <div class="um-pane">
    <p v-if="dbUnreadable" class="um-db-banner" role="alert">
      The Unraid-Manager database could not be read. Check the database path
      under
      <a href="/Settings/UnraidManagerSettings">Settings → Utilities → Unraid-Manager</a>.
    </p>
    <!-- Two different failures, so two different sentences. Saying "the
         manager has not answered" when the manager is dead but nginx is fine
         was precisely the false statement F-1 was about: the pane WAS being
         answered, with numbers nobody was updating any more. -->
    <p v-if="stale" class="um-stale-banner" role="status">
      <template v-if="dataStale">
        Nothing new has been collected for {{ minutesOld(age) }} — the newest
        reading in the fleet is from {{ newest }}. Check that managerd is
        running on the Settings page.
      </template>
      <template v-else>
        This page has not been able to reach the server since
        {{ new Date(lastGood).toLocaleTimeString() }}, so these numbers may
        have moved on without it.
      </template>
    </p>
    <nav class="um-tabs">
      <button v-for="tab in TABS" :key="tab.id" type="button"
              :class="{ 'um-tab': true, 'um-tab-active': active === tab.id }"
              :aria-current="active === tab.id ? 'page' : undefined"
              @click="active = tab.id">{{ tab.label }}</button>
    </nav>
    <component :is="TABS.find(t => t.id === active).component" />
  </div>
</template>

<style>
.um-tabs { display: flex; gap: .25rem; margin-bottom: 1rem;
           border-bottom: 1px solid var(--um-border); }
.um-tab { background: none; border: none; border-bottom: 2px solid transparent;
          color: var(--um-fg); padding: .5rem 1rem; cursor: pointer; font-size: 1rem; }
.um-tab-active { border-bottom-color: var(--um-accent); font-weight: 600; }
.um-stale-banner { padding: .5rem; margin-bottom: 1rem;
                   border: 1px solid var(--um-watch); }
.um-db-banner { padding: .5rem; margin-bottom: 1rem; font-weight: 600;
                border: 1px solid var(--um-crit); background: var(--um-surface); }
</style>
