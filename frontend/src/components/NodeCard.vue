<script setup>
import { inject } from 'vue'
import StatusChip from './StatusChip.vue'
import { localTime } from '../time.js'

// Provided by App.vue from the endpoint payload. Rendered standalone (a test
// harness, or a future embed) there is no provider, so undefined falls through
// to time.js's UTC default rather than throwing.
const tz = inject('um-tz', null)
const clock12 = inject('um-clock12', false)

defineProps({ node: { type: Object, required: true } })
defineEmits(['open'])

function bytes (n) {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let value = Number(n)
  let i = 0
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++ }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

function percent (capacity) {
  if (!capacity || !capacity.total) return null
  return Math.round((capacity.used / capacity.total) * 100)
}

function held (since) {
  if (!since) return ''
  const ms = Date.now() - Date.parse(since)
  if (Number.isNaN(ms) || ms < 0) return ''
  const hours = Math.floor(ms / 3600000)
  if (hours >= 24) return `for ${Math.floor(hours / 24)}d`
  if (hours >= 1) return `for ${hours}h`
  return `for ${Math.max(1, Math.floor(ms / 60000))}m`
}

// InfoOs.uptime is a BOOT TIMESTAMP, not a duration (verified platform fact 5),
// which is why the collector stores it as booted_at. Printing it raw is how a
// Uptime column ends up reading "2026".
function uptime (bootedAt) {
  if (!bootedAt) return ''
  const ms = Date.now() - Date.parse(bootedAt)
  if (Number.isNaN(ms) || ms < 0) return ''
  const days = Math.floor(ms / 86400000)
  if (days >= 1) return `${days}d`
  return `${Math.floor(ms / 3600000)}h`
}

// Controller amendment A: `unread` is null when a node has no notifications
// payload at all - "we have not heard", not "nothing unread" - and the two
// must not render the same. Kept as one function so the null branch is a
// single, obvious early return rather than an optional-chained default
// (`node.unread?.alert || 0`) that would print "0 alert" for both cases.
function unreadText (unread) {
  if (!unread) return null
  return `${unread.alert} alert · ${unread.warning} warn · ${unread.info} info`
}
</script>

<template>
  <div class="um-card" role="button" tabindex="0"
       @click="$emit('open', node.id)" @keydown.enter="$emit('open', node.id)"
       @keydown.space.prevent="$emit('open', node.id)">
    <div class="um-card-head">
      <strong>{{ node.name }}</strong>
      <StatusChip :state="node.state" />
    </div>

    <div class="um-card-line">
      {{ node.array_state || '—' }}
      <!-- Amendment B: the Fleet tab's version column showed Unraid *and* API
           version; show both here too, not only node.unraid. -->
      <span v-if="node.unraid || node.api"> · Unraid {{ node.unraid || '—' }} / API {{ node.api || '—' }}</span>
      <span v-if="uptime(node.booted_at)"> · up {{ uptime(node.booted_at) }}</span>
    </div>

    <!-- Constraint 3, all the way to the pixel: an array with nothing in it is a
         healthy empty array, not 0% of something and not missing data. -->
    <div class="um-card-line" v-if="node.array_empty">
      <span class="um-unknown">empty array</span>
    </div>
    <div class="um-card-line" v-else-if="percent(node.capacity) !== null">
      <span class="um-capbar"><span :style="{ width: percent(node.capacity) + '%' }" /></span>
      {{ percent(node.capacity) }}% · {{ bytes(node.capacity.used) }} of
      {{ bytes(node.capacity.total) }}
    </div>
    <div class="um-card-line" v-else><span class="um-unknown">capacity unknown</span></div>

    <!-- Amendment A: node.unread present -> the real alert/warning/info counts,
         even if all three are zero. node.unread === null -> a visibly distinct
         "unknown" treatment, never a silently-defaulted zero. -->
    <div class="um-card-line">
      <span v-if="node.unread" class="um-hint">{{ unreadText(node.unread) }}</span>
      <span v-else class="um-unknown">unread unknown</span>
    </div>

    <ul class="um-indicators">
      <li v-for="(indicator, name) in node.indicators" :key="name">
        <StatusChip :state="indicator.state" />
        <span class="um-indicator-name">{{ name.replace('_', ' ') }}</span>
        <span class="um-hint">{{ indicator.basis }}</span>
      </li>
    </ul>

    <div class="um-card-foot um-hint">
      <span v-if="node.state !== 'ok'">{{ node.state }} {{ held(node.since) }} · </span>
      <!-- fleet.js styled a never-seen node's cell as um-unknown - "we have
           not heard" gets the same distinct treatment here (fix round 1,
           item 8). -->
      <span :class="{ 'um-unknown': !node.last_seen }">last seen {{ localTime(node.last_seen, tz, clock12) }}</span>
    </div>
  </div>
</template>

<style>
.um-card { border: 1px solid var(--um-border); background: var(--um-surface);
           padding: .75rem; cursor: pointer; }
.um-card-head { display: flex; justify-content: space-between; align-items: baseline; }
.um-card-line { margin-top: .35rem; }
.um-indicators { list-style: none; margin: .5rem 0 0; padding: 0; font-size: .9em; }
.um-indicators li { display: flex; gap: .5rem; align-items: baseline; }
.um-indicator-name { min-width: 7em; }
.um-card-foot { margin-top: .5rem; }
.um-capbar { display: inline-block; width: 8em; height: .8em; vertical-align: middle;
             background: color-mix(in srgb, var(--um-fg) 20%, transparent); }
.um-capbar > span { display: block; height: 100%; background: currentColor; }
</style>
