// The fleet grid's ordering and filtering rules (P2-5).
//
// Pure functions, unit-tested here; interact.mjs drives the same rules through
// real clicks on Overview.vue. Both, for the reason sort.js's own extraction
// records: a rule that lives inside <script setup> can only be grepped for, and
// a rule tested only through the DOM does not pin its edges.
//   node tests/js/fleet.mjs   ->   "fleet: all pass" (exit 0)
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { frontend, reporter } from './ssr.mjs'

const { check, done } = reporter('fleet')
const { stateRank, sortNodes, filterNodes, arrangeNodes } = await import(
  pathToFileURL(path.join(frontend, 'src', 'fleet.js')).href)

const node = (name, state) => ({ id: name, name, state })

/* ── ordering ─────────────────────────────────────────────────────────────── */

const mixed = [node('Atlas', 'ok'), node('Zeus', 'degraded'),
               node('Boreas', 'unknown'), node('Aegis', 'ok')]

check('the worst state leads, whatever the name',
      sortNodes(mixed).map(n => n.name)[0] === 'Zeus')
check('degraded, then unknown, then ok',
      sortNodes(mixed).map(n => n.state).join() === 'degraded,unknown,ok,ok')
check('name breaks ties inside one rank',
      sortNodes(mixed).map(n => n.name).slice(2).join() === 'Aegis,Atlas')

/* The sort.js lesson, restated for states: a value we do not understand must
   not lead the list. There it was a null temperature reading as the coldest
   drive in the fleet; here it would be a future state, or a typo, presenting
   itself as the most urgent thing on the screen. */
check('an unrecognised state sorts LAST, not first',
      sortNodes([node('X', 'wat'), node('Y', 'ok')]).map(n => n.name).join() === 'Y,X')
check('...and still last when everything else is degraded',
      sortNodes([node('X', 'wat'), node('Y', 'degraded')])
        .map(n => n.name).join() === 'Y,X')
check('degraded outranks unknown', stateRank('degraded') < stateRank('unknown'))
check('unknown outranks ok', stateRank('unknown') < stateRank('ok'))

check('sorting does not mutate the array it is given',
      (() => { const before = mixed.map(n => n.name); sortNodes(mixed)
               return mixed.map(n => n.name).join() === before.join() })())

/* ── filtering ────────────────────────────────────────────────────────────── */

check('no options is every node',
      filterNodes(mixed, {}).length === 4)
check('a state isolates exactly that state',
      filterNodes(mixed, { state: 'ok' }).map(n => n.name).join() === 'Atlas,Aegis')
check('a state with no members is empty, not everything',
      filterNodes(mixed, { state: 'nope' }).length === 0)

check('search matches a substring of the name',
      filterNodes(mixed, { query: 'eu' }).map(n => n.name).join() === 'Zeus')
check('search is case-insensitive',
      filterNodes(mixed, { query: 'ATL' }).map(n => n.name).join() === 'Atlas')
check('a blank search is not a filter',
      filterNodes(mixed, { query: '   ' }).length === 4)
check('search ignores surrounding whitespace',
      filterNodes(mixed, { query: ' atlas ' }).map(n => n.name).join() === 'Atlas')
/* A node with no name would otherwise throw and take the whole grid with it. */
check('a node with no name is searchable without exploding',
      filterNodes([{ id: 'x', name: null, state: 'ok' }], { query: 'a' }).length === 0)

check('state and search combine with AND, not OR',
      filterNodes(mixed, { state: 'ok', query: 'a' }).map(n => n.name).join()
        === 'Atlas,Aegis')

/* ── the one call the view makes ──────────────────────────────────────────── */

check('arrange filters and then sorts',
      arrangeNodes([node('Zeb', 'ok'), node('Ann', 'degraded'), node('Amy', 'ok')],
                   { query: 'a' }).map(n => n.name).join() === 'Ann,Amy')
check('arrange with no options is just the sort',
      arrangeNodes(mixed, {}).map(n => n.name).join() === 'Zeus,Boreas,Aegis,Atlas')
check('arrange tolerates a missing node list',
      arrangeNodes(undefined, {}).length === 0)

done()
