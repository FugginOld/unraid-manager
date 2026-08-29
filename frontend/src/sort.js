// Column sorting for the fleet tables.
//
// Extracted from Disks.vue so it can be TESTED: SSR cannot click a header, so
// while this lived inside <script setup> the only coverage possible was a grep
// for the word `sortBy` (P1 triage P2-7 and P2-8). The behaviour below is not
// obvious enough to leave unpinned.
//
// Two rules, both about missing values:
//
//   1. A missing reading sorts LAST in both directions. The previous version
//      coerced null to '' and compared it against numbers, where '' < 34 is
//      true - so a disk with no temperature sorted as if it were 0 C and led
//      an ascending sort, reading as the coldest drive in the fleet. A disk
//      whose temperature we cannot see is not cold; it is unknown, and unknown
//      belongs at the bottom of the list whichever way the arrow points.
//
//   2. Numbers compare as numbers, everything else as strings. Mixed columns
//      exist here (`errors` is an integer or null; `slot` is 'disk1' or null),
//      and JavaScript's relational operators on mixed types are a coin toss.

function missing (value) {
  return value === null || value === undefined || value === ''
}

export function compareValues (x, y, asc = true) {
  const xGone = missing(x)
  const yGone = missing(y)
  if (xGone || yGone) {
    // Not multiplied by the direction: "last" means last either way.
    if (xGone && yGone) return 0
    return xGone ? 1 : -1
  }
  if (x === y) return 0
  const numeric = typeof x === 'number' && typeof y === 'number'
  const less = numeric ? x < y : String(x) < String(y)
  return (less ? -1 : 1) * (asc ? 1 : -1)
}

export function sortRows (rows, key, asc = true) {
  return [...rows].sort((a, b) => compareValues(a[key], b[key], asc))
}
