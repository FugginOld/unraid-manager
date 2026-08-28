<script setup>
import { ref } from 'vue'
import { useEndpoint } from './api.js'
import { useLive } from './live.js'
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
const { dbUnreadable, refresh } = useEndpoint('health')
const { stale, lastGood } = useLive(refresh)
</script>

<template>
  <div class="um-pane">
    <p v-if="dbUnreadable" class="um-db-banner" role="alert">
      The Unraid-Manager database could not be read. Check the database path
      under
      <a href="/Settings/UnraidManagerSettings">Settings → Utilities → Unraid-Manager</a>.
    </p>
    <p v-if="stale" class="um-stale-banner" role="status">
      These numbers are more than three minutes old — the manager has not
      answered since {{ new Date(lastGood).toLocaleTimeString() }}. Check that
      managerd is running on the Settings page.
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
