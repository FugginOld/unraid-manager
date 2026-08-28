<script setup>
// Colour is never the only carrier: glyph + word + colour, every time. `unknown`
// is grey and distinct from both healthy and failed - a red chip meaning "I
// can't see it" trains operators to ignore red.
const LABELS = {
  ok: ['✓', 'OK'],
  degraded: ['⚠', 'Degraded'],
  unknown: ['?', 'Unknown'],
  watch: ['●', 'Watch'],
  warn: ['⚠', 'Warning'],
  // .um-crit exists in the stylesheet below; without this entry state="crit"
  // fell back to LABELS.unknown and rendered the critical colour next to the
  // word "Unknown" - colour and word disagreeing, which is the one thing
  // this component exists to prevent.
  crit: ['✕', 'Critical'],
}
defineProps({ state: { type: String, default: 'unknown' } })
</script>

<template>
  <span :class="'um-chip um-' + state">
    <span aria-hidden="true">{{ (LABELS[state] || LABELS.unknown)[0] }}</span>
    {{ (LABELS[state] || LABELS.unknown)[1] }}
  </span>
</template>

<style>
.um-chip { white-space: nowrap; font-weight: 600; }
.um-ok       { color: var(--um-ok); }
.um-watch    { color: var(--um-watch); }
.um-warn,
.um-degraded { color: var(--um-warn); }
.um-crit     { color: var(--um-crit); }
.um-unknown  { color: var(--um-unknown); border-bottom: 1px dotted var(--um-unknown); }
</style>
