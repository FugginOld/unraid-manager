<script setup>
import { ref, watch, onMounted, onUnmounted, inject } from 'vue'
import { get } from '../api.js'
import StatusChip from './StatusChip.vue'
import { localTime } from '../time.js'

const tz = inject('um-tz', null)

const props = defineProps({ nodeId: { type: String, default: null } })
const emit = defineEmits(['close'])
const node = ref(null)
const error = ref(null)

// A drawer over the fleet view, not a page: the operator never loses fleet
// context (plan section 10.4).
watch(() => props.nodeId, async (id) => {
  node.value = null
  error.value = null
  if (!id) return
  try {
    // The id is enough. No key crosses this boundary - the daemon holds it.
    node.value = await get(`nodes.php?id=${encodeURIComponent(id)}`)
  } catch (err) {
    error.value = err.message
  }
}, { immediate: true })

function onKey (event) { if (event.key === 'Escape') emit('close') }
onMounted(() => document.addEventListener('keydown', onKey))
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <aside v-if="nodeId" class="um-drawer" role="dialog" aria-label="Node detail">
    <button type="button" class="um-drawer-close" @click="$emit('close')">Close</button>
    <h3 v-if="node">{{ node.name }} — {{ node.address }}:{{ node.port }}</h3>
    <p v-if="error" class="um-warn">{{ error }}</p>
    <p v-else-if="!node">Loading…</p>
    <table v-else class="tablesorter">
      <thead><tr><th>Domain</th><th>State</th><th>Fetched</th><th>Detail</th></tr></thead>
      <tbody>
        <tr v-for="(domain, name) in node.domains" :key="name">
          <td>{{ name }}</td>
          <!-- fleet.js showed "Error", not "Warning", for a hard-failed
               domain; 'warn' collapsed that distinction (fix round 1,
               item 8). StatusChip's 'crit' state (Critical / red X) is the
               distinct-from-warn treatment available for it. -->
          <td><StatusChip :state="domain.status === 'error' ? 'crit' : domain.status" /></td>
          <td>{{ localTime(domain.fetched_at, tz) }}</td>
          <td class="um-hint">{{ domain.error || '' }}</td>
        </tr>
      </tbody>
    </table>
  </aside>
</template>

<style>
.um-drawer { position: fixed; top: 0; right: 0; bottom: 0; z-index: 40;
             width: min(38rem, 92vw); overflow: auto; padding: 1rem;
             background: var(--um-bg); border-left: 1px solid var(--um-border);
             box-shadow: -8px 0 24px rgba(0, 0, 0, .25); }
.um-drawer-close { float: right; }
</style>
