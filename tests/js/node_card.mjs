// Real proof of what NodeCard.vue actually RENDERS - not a source-text grep.
// Fix round 1, item 5: null-vs-zero, empty-array-vs-0%, and unknown-vs-failed
// are facts about output, and a comment sitting next to the right branch is
// enough to fool a grep (two of frontend_test.php's checks were, until this
// round). The compile+SSR rig lives in ssr.mjs, shared with views.mjs.
//   node tests/js/node_card.mjs   ->   "node_card: all pass" (exit 0)
import assert from 'node:assert/strict'
import path from 'node:path'
import { createCompiler, frontend, reporter } from './ssr.mjs'
import * as live from '../../frontend/src/live.js'

const componentsDir = path.join(frontend, 'src', 'components')
const { check, done } = reporter('node_card')
const ssr = createCompiler()

const NodeCard = await ssr.load(path.join(componentsDir, 'NodeCard.vue'))

const renderCard = node => ssr.render(NodeCard, { node }, { 'um-stale-after': 180 })

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

  /* A node never heard from says so in a word. Rendering an empty string
     instead leaves "last seen" trailing off into nothing, which reads as a
     layout bug rather than as a fact about the node - and the timestamp
     formatter (time.js) is one shared helper away from every card. */
  const htmlNeverSeen = await renderCard(baseNode({ last_seen: null }))
  check('a node never seen renders the word "never", not an empty space',
        /last seen\s*never/.test(htmlNeverSeen))
  /* And a real timestamp is rendered as a wall clock, not as the stored UTC
     instant. No zone is provided here (the card is rendered standalone), so
     time.js falls back to UTC - labelled, which is the point. */
  const htmlSeen = await renderCard(baseNode({ last_seen: '2026-08-28T19:34:38Z' }))
  check('a real last-seen is rendered as a readable local time, not raw ISO',
        htmlSeen.includes('2026-08-28, 19:34:38 UTC')
        && !htmlSeen.includes('2026-08-28T19:34:38Z'))

  /* "never" is a claim about the node; an unreadable timestamp is a claim
     about us. A value we cannot parse is shown as it came, not relabelled
     into a fact we do not have. */
  const htmlBadStamp = await renderCard(baseNode({ last_seen: 'not-a-date' }))
  check('an unreadable timestamp is shown as-is, never relabelled "never"',
        htmlBadStamp.includes('not-a-date') && !/last seen\s*never/.test(htmlBadStamp))

  /* ── unknown-vs-failed: an unknown indicator must not colour the head chip ─ */
  const htmlUnknownIndicator = await renderCard(baseNode({
    state: 'ok',
    indicators: { disk_temp: { state: 'unknown', value: null, basis: 'no sensor', since: null } },
  }))
  const head = htmlUnknownIndicator.match(/<div class="um-card-head">[\s\S]*?<\/div>/)
  assert.ok(head, 'um-card-head not found in rendered output')
  check('a node.state="ok" head chip stays OK even when one indicator is unknown',
        head[0].includes('um-ok') && !head[0].includes('um-unknown'))

  /* ── P1 triage P2-6: staleness ─────────────────────────────────
     The DOWNGRADE is health.php's job, not this card's. It lived here for a
     day and the summary line disagreed with the cards under it on Raven -
     "0 unknown" beside a card reading "? Unknown" - because the count was
     tallied server-side from the stored row while the chip was decided here.
     One rule, two languages, two answers.

     So the chip is now whatever the server said, full stop. What the card
     still owns is the WORDING: why this thing is grey, which the chip alone
     cannot say. `stored_state` travels beside `state` so it can tell "was ok,
     nobody has checked" from "was degraded and still is".

     The threshold is a wall-clock number here, not imported, so widening the
     server's constant cannot quietly widen the test with it. */
  const STALE_SECONDS = 180
  const headOf = html => {
    const m = html.match(/<div class="um-card-head">[\s\S]*?<\/div>/)
    assert.ok(m, 'um-card-head not found in rendered output')
    return m[0]
  }
  const aged = (overrides = {}) => baseNode({
    age: STALE_SECONDS + 1, updated_at: '2026-08-28T19:34:38Z', ...overrides })

  /* The server downgrades a stale ok to unknown, so this is what arrives. */
  const staleOk = await renderCard(aged({ state: 'unknown', stored_state: 'ok' }))
  check('the chip renders the state the server sent, not one recomputed here',
        headOf(staleOk).includes('um-unknown') && !headOf(staleOk).includes('um-ok'))
  check('a card that went grey for want of an update says so, and when',
        /no update/.test(staleOk) && staleOk.includes('2026-08-28, 19:34:38 UTC'))

  const staleBad = await renderCard(aged({ state: 'degraded', stored_state: 'degraded',
                                           since: null }))
  check('a stale degraded node KEEPS its verdict - the problem did not go away',
        headOf(staleBad).includes('um-degraded') && !headOf(staleBad).includes('um-unknown'))
  check('...and is marked stale rather than "no update", which would be a lie',
        /stale/.test(staleBad) && !/no update/.test(staleBad)
        && staleBad.includes('2026-08-28, 19:34:38 UTC'))

  const freshOk = await renderCard(baseNode({ state: 'ok', stored_state: 'ok',
                                              age: STALE_SECONDS - 1,
                                              updated_at: '2026-08-28T19:34:38Z' }))
  check('a fresh ok node is untouched', headOf(freshOk).includes('um-ok'))
  check('...and carries no staleness wording at all',
        !/stale/.test(freshOk) && !freshOk.includes('as of'))

  const freshBad = await renderCard(baseNode({ state: 'degraded', stored_state: 'degraded',
                                               age: 0, updated_at: '2026-08-28T19:34:38Z' }))
  check('a fresh degraded node is not marked stale', !/stale/.test(freshBad))

  /* A node the daemon has never managed to stamp already arrives as `unknown`
     from health.php. Inventing staleness on top of a null would be a second
     clock answering a question the first one already answered. */
  const noAge = await renderCard(baseNode({ state: 'ok', stored_state: 'ok',
                                            age: null, updated_at: null }))
  check('a node with no age is not treated as stale', headOf(noAge).includes('um-ok'))
  check('...and is not given an "as of" it does not have', !noAge.includes('as of'))

  /* Rendered with no provider at all - a standalone harness, or a future
     embed. The card cannot know the threshold, so it claims nothing rather
     than inventing three minutes of its own. Defaulting the other way would
     mark every card in such a render stale. */
  const orphan = await ssr.render(NodeCard, { node: aged({ state: 'degraded',
                                                           stored_state: 'degraded' }) })
  check('with no threshold provided the card claims no staleness at all',
        !/stale/.test(orphan) && !orphan.includes('as of'))

  /* The card must not re-derive the chip: given a state the server did NOT
     downgrade, it renders it as-is even though the age is past the mark. This
     is the check that fails if the downgrade ever creeps back in here. */
  const notDowngraded = await renderCard(aged({ state: 'ok', stored_state: 'ok' }))
  check('an ok state the server chose to keep is rendered ok, stale or not',
        headOf(notDowngraded).includes('um-ok'))

} finally {
  ssr.cleanup()
}

done()
