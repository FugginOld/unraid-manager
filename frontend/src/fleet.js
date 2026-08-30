// Ordering and filtering for the fleet card grid (P2-5).
//
// Extracted rather than written into Overview.vue for the reason sort.js
// records: SSR cannot click, so a rule living inside <script setup> can only be
// grepped for, and a grep cannot tell two branches apart.
//
// The grid used to render health.php's `ORDER BY name` and nothing else, which
// is fine for two nodes and useless for twenty: the question at that scale is
// never "where is the node called Atlas", it is "which of these is sick".
// So the ordering is not a preference the operator has to set - the worst state
// leads, always, and the name only breaks ties.

import { compareValues } from './sort.js'

// Lower sorts first. degraded is something wrong and actionable; unknown is a
// claim we cannot make; ok is the rest. The same asymmetry health.php applies
// when it downgrades a stale ok to unknown but lets a stale degraded keep its
// finding - a finding outranks the absence of one.
const RANK = { degraded: 0, unknown: 1, ok: 2 }

// Anything not in RANK sorts after everything that is. This is sort.js's rule
// about missing readings, restated for states: there, a null temperature
// coerced to 0 and led an ascending sort, reading as the coldest drive in the
// fleet. A state we do not recognise - a future one, a typo - must not be able
// to present itself as the most urgent thing on the screen.
const LAST = Object.keys(RANK).length

export function stateRank (state) {
  const rank = RANK[state]
  return rank === undefined ? LAST : rank
}

export function sortNodes (nodes) {
  // Copied, not sorted in place: the caller's array is a computed over the
  // endpoint's payload, and mutating it would reorder the shared response.
  return [...(nodes ?? [])].sort((a, b) =>
    stateRank(a?.state) - stateRank(b?.state) ||
    compareValues(a?.name, b?.name, true))
}

export function filterNodes (nodes, { state = null, query = '' } = {}) {
  const needle = String(query ?? '').trim().toLowerCase()
  return (nodes ?? []).filter(node => {
    if (state && node?.state !== state) return false
    // A node with no name is not a match for a search; it is also not a crash.
    if (needle && !String(node?.name ?? '').toLowerCase().includes(needle)) return false
    return true
  })
}

// Filter first, then sort: sorting what will be thrown away is work for nothing,
// and the view only ever wants the two together.
export function arrangeNodes (nodes, options = {}) {
  return sortNodes(filterNodes(nodes, options))
}
