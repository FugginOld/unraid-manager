// Real proof of what NodeCard.vue actually RENDERS - not a source-text grep.
// Fix round 1, item 5: null-vs-zero, empty-array-vs-0%, and unknown-vs-failed
// are facts about output, and a comment sitting next to the right branch is
// enough to fool a grep (two of frontend_test.php's checks were, until this
// round). The compile+SSR rig lives in ssr.mjs, shared with views.mjs.
//   node tests/js/node_card.mjs   ->   "node_card: all pass" (exit 0)
import assert from 'node:assert/strict'
import path from 'node:path'
import { createCompiler, frontend, reporter } from './ssr.mjs'

const componentsDir = path.join(frontend, 'src', 'components')
const { check, done } = reporter('node_card')
const ssr = createCompiler()

const NodeCard = await ssr.load(path.join(componentsDir, 'NodeCard.vue'))

const renderCard = node => ssr.render(NodeCard, { node })

function baseNode (overrides = {}) {
  return {
    id: 'n1', name: 'Raven', state: 'ok', since: null, array_state: 'started',
    array_empty: false, capacity: { used: 100, total: 200 },
    unraid: '6.12.9', api: '4.1.2', booted_at: null,
    /* NOT null: item 8 put um-unknown on the last-seen span, so a null here
       puts that class in every render and the unread-null check below passes
       on an unrelated element. */
    last_seen: '2026-08-28T00:00:00Z',
    unread: null, indicators: {},
    ...overrides,
  }
}

try {
  /* ── null-vs-zero (amendment A) ─────────────────────────────────────── */
  const htmlNull = await renderCard(baseNode({ unread: null }))
  const htmlZero = await renderCard(baseNode({ unread: { alert: 0, warning: 0, info: 0 } }))
  check('unread: null and unread: {alert:0,warning:0,info:0} render differently',
        htmlNull !== htmlZero)
  check('unread: null renders the "we have not heard" treatment, not a zero count',
        htmlNull.includes('um-unknown') && !htmlNull.includes('0 alert'))
  check('unread: {0,0,0} renders the real zero breakdown, not the unknown treatment',
        htmlZero.includes('0 alert') && htmlZero.includes('0 warn') && htmlZero.includes('0 info'))

  /* Found by a mis-aimed mutation: 'capacity unknown' is the third place the
     card says "we cannot see this", and it was the only one unpinned. Every
     unknown treatment must be visually distinct from healthy, not just the two
     the amendments named. */
  const htmlNoCap = await renderCard(baseNode({ capacity: null }))
  check('an unreportable capacity gets the unknown treatment, not a hint',
        /um-unknown[^>]*>\s*capacity unknown/.test(htmlNoCap))

  /* ── empty-array-vs-0% (Raven, constraint 3) ──────────────────────────── */
  const htmlEmpty = await renderCard(baseNode({ array_empty: true, capacity: { used: 0, total: 0 } }))
  check('array_empty renders "empty array"', htmlEmpty.includes('empty array'))
  check('array_empty never renders "0%" (the exact regression verified wrong on Raven)',
        !htmlEmpty.includes('0%'))

  /* ── unknown-vs-failed: an unknown indicator must not colour the head chip ─ */
  const htmlUnknownIndicator = await renderCard(baseNode({
    state: 'ok',
    indicators: { disk_temp: { state: 'unknown', value: null, basis: 'no sensor', since: null } },
  }))
  const head = htmlUnknownIndicator.match(/<div class="um-card-head">[\s\S]*?<\/div>/)
  assert.ok(head, 'um-card-head not found in rendered output')
  check('a node.state="ok" head chip stays OK even when one indicator is unknown',
        head[0].includes('um-ok') && !head[0].includes('um-unknown'))
} finally {
  ssr.cleanup()
}

done()
