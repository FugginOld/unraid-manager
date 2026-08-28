<script setup>
import { ref, computed } from 'vue'
import { useEndpoint } from '../api.js'
import { useLive } from '../live.js'

const { data, error, dbUnreadable, refresh } = useEndpoint('drift')
useLive(refresh)

// Only the differences matter by default - a matrix of identical rows is noise
// (plan section 10.4).
const showAll = ref(false)

const nodes = computed(() => data.value?.nodes ?? [])
const rows = computed(() => {
  const all = data.value?.rows ?? []
  return showAll.value ? all : all.filter(r => r.divergent)
})
const hidden = computed(() => (data.value?.rows ?? []).length - rows.value.length)

function cell (row, nodeId) {
  const value = row.cells[nodeId]
  // null is "this node has not reported", which is not the same as "absent".
  // drift.php keeps the two apart on purpose; a falsy test here would put them
  // back together and accuse a node we have never heard from of missing a
  // plugin.
  if (value === null || value === undefined) return '—'
  if (row.kind === 'plugin') return value ? 'present' : 'absent'
  return value
}
</script>

<template>
  <div>
    <!-- Never a blank pane while managerd is down (Task 13, item 6). -->
    <p v-if="!data" class="um-hint">
      {{ error ? 'Could not reach the manager: ' + error : 'Loading…' }}
    </p>

    <!-- An unreadable database yields an empty matrix; "nothing differs across
         the fleet" would then be a confident wrong claim sitting under the
         shell's "database could not be read" banner (Task 13, item 7). -->
    <template v-if="data && !dbUnreadable">
      <p v-if="data.plugin_versions_available === false" class="um-hint">
        Unraid's API lists installed plugins by name only — it reports no
        plugin versions, so this matrix shows presence rather than version
        drift. Comparing plugin versions needs a Tier 1 agent.
      </p>

      <p>
        <button type="button" @click="showAll = !showAll">
          {{ showAll ? 'Show differences only' : 'Show all rows' }}
        </button>
        <span v-if="!showAll && hidden > 0" class="um-hint">
          {{ hidden }} identical row(s) hidden.
        </span>
      </p>

      <table class="tablesorter">
        <thead>
          <tr>
            <th>Item</th>
            <th v-for="node in nodes" :key="node.id">{{ node.name }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length">
            <td :colspan="nodes.length + 1">Nothing differs across the fleet.</td>
          </tr>
          <tr v-for="row in rows" :key="row.key">
            <td>{{ row.key.replace('plugin:', '') }}</td>
            <td v-for="node in nodes" :key="node.id" :class="{ 'um-warn': row.divergent }">
              {{ cell(row, node.id) }}
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </div>
</template>
