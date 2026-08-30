// The clicks. views.mjs proves what Disks.vue and Drift.vue RENDER; this
// proves what they DO when the operator uses them (P1 triage P2-8).
//
// Everything here was unreachable from an SSR render, which paints one frame
// from the initial state and stops:
//   - sortBy()'s toggle. sort.js is unit-tested, but nothing tied a <th> to
//     the column it is labelled with, or checked that the arrow flips on the
//     second click and RESETS when a different column is picked.
//   - both filters. The No-disk button's label and smartOf()'s verdict have to
//     be the same string; when they were two literals they drifted apart and
//     the whole suite stayed green (Disks.vue's own NO_DISK comment).
//   - the one limit views.mjs writes down about its um-warn count: with the
//     collapse hiding every non-divergent row, "highlight the divergent cells"
//     and "highlight all cells" render identically. Showing all rows is what
//     separates them, and that needs a click.
//
// Costs one devDependency (happy-dom) - the first in this directory. Vue's
// client runtime resolves `document` when it loads, so there is no DOM-free
// way to mount a component and dispatch an event at it; the alternative was a
// hand-written custom renderer, ~100 lines of untested harness whose bugs
// would read as passing tests. The dep is dev-only and never enters the
// bundle: build.sh runs `vite build`, which does not see this file.
//   node tests/js/interact.mjs   ->   "interact: all pass" (exit 0)
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// Before anything imports vue: @vue/runtime-dom reads `document` at module
// scope, so a DOM that arrives later is a DOM the runtime never sees. That is
// also why ssr.mjs is imported dynamically further down instead of at the top.
// fileURLToPath, never url.pathname: on Windows the latter yields "/C:/..."
// and every path built from it resolves one drive letter too deep.
const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(here, '..', '..', 'frontend')
const { Window } = await import(
  pathToFileURL(path.join(frontendDir, 'node_modules', 'happy-dom', 'lib', 'index.js')).href)
const win = new Window({ url: 'http://localhost/' })
// Document and ShadowRoot joined the list for Overview.vue's search box: it is
// the first v-model in a tested view, and vModelText's beforeUpdate hook reads
// `rootNode instanceof Document` at every patch. Missing, that is a bare
// ReferenceError from inside Vue's own directive - which reads as the component
// being broken rather than the harness being short a global.
for (const key of ['window', 'document', 'navigator', 'location', 'history',
                   'Node', 'Element', 'HTMLElement', 'SVGElement', 'Event',
                   'CustomEvent', 'MouseEvent', 'getComputedStyle',
                   'requestAnimationFrame', 'cancelAnimationFrame',
                   'Document', 'ShadowRoot']) {
  // defineProperty, not assignment: Node defines some of these (navigator) on
  // globalThis as getter-only, and a plain `=` throws rather than shadowing.
  Object.defineProperty(globalThis, key,
    { value: win[key], configurable: true, writable: true })
}

const { createCompiler, frontend, reporter, vueEntryPath, VIEW_STUBS } =
  await import('./ssr.mjs')
const { createApp, nextTick } = await import(pathToFileURL(vueEntryPath).href)

const viewsDir = path.join(frontend, 'src', 'views')
const { check, done } = reporter('interact')

// client: true compiles the same SFC to a client render function instead of an
// ssrRender. Same file, same stubs, same fixture protocol as views.mjs.
const dom = createCompiler({ stubs: VIEW_STUBS, client: true })

/* Mounts a view over a fixture and hands back the live root plus the helpers
   a check needs. The fixture is read by the api.js stub at setup() time, so it
   has to be in place before mount() - same contract as views.mjs. */
async function mount (component, fixture) {
  globalThis.__um_fixture = fixture
  const host = win.document.createElement('div')
  win.document.body.appendChild(host)
  let app
  try {
    app = createApp(component)
    app.mount(host)
    await nextTick()
  } finally {
    globalThis.__um_fixture = undefined
  }
  const click = async (el) => {
    if (!el) throw new Error('click() called on nothing - the selector missed')
    el.click()
    await nextTick()
    return el
  }
  return {
    host,
    click,
    // The first table is the disk/drift matrix; Disks.vue's second one is the
    // Spares list, which has no filters and no sortable headers.
    table: () => host.querySelector('table'),
    // Data rows only. A tbody with one full-width "nothing here" cell is an
    // empty result, not a row - counting it would make an over-eager filter
    // look like it kept something.
    rows: () => [...host.querySelectorAll('table tbody tr')]
      .filter(tr => tr.querySelectorAll('td').length > 1),
    cells: (tr) => [...tr.querySelectorAll('td')].map(td => td.textContent.trim()),
    // Buttons and headers are addressed by the text the operator reads, not by
    // position: a check that clicks "the fourth button" keeps passing after
    // the button it meant is renamed or moved.
    byText: (sel, text) => [...host.querySelectorAll(sel)]
      .find(el => el.textContent.trim() === text),
    text: () => host.textContent,
    unmount: () => { app.unmount(); host.remove() },
  }
}

const DISK = {
  node: 'Raven', node_id: 'n1', model: 'ST10000NM0226', device: '/dev/sda',
  vendor: 'Seagate', size: 10000831348736, temp: 34, smart_status: 'OK',
  interface: 'SATA', slot: 'disk1', errors: 0, array_status: 'DISK_OK',
  fetched_at: '2026-08-28T00:00:00Z',
}
const disk = (over) => ({ ...DISK, ...over })

/* Four disks over two nodes. The temperatures and the slots deliberately
   disagree on ordering, so a header wired to the wrong column sorts into a
   different sequence rather than the same one by luck. cold has no temp at
   all - the P2-7 row, here proven through the header the operator actually
   clicks rather than through sort.js directly. */
const HOT = disk({ device: '/dev/sda', slot: 'disk1', temp: 44 })
const WARM = disk({ device: '/dev/sdb', slot: 'disk4', temp: 31 })
const COLD = disk({ device: '/dev/sdc', slot: 'disk2', temp: null, smart_status: 'UNKNOWN' })
const GOLEM = disk({ node: 'Golem', node_id: 'n2', device: '/dev/sdd', slot: 'disk3', temp: 38 })
/* An array slot with nothing behind it: model null, which smartOf() reports as
   "no disk" and the fourth filter button offers under that same name. */
const ORPHAN = disk({
  node: 'Raven', node_id: 'n1', device: 'sdj', model: null, vendor: null,
  size: null, temp: null, smart_status: null, slot: 'disk7', errors: 12,
  array_status: 'DISK_DSBL',
})
const FLEET = { data: { disks: [HOT, WARM, COLD, GOLEM, ORPHAN], spares: [], stale: [] } }

// Which disk each row is, read off the Device column - the only cell that is
// unique per row and non-null on every fixture including the orphan.
const DEVICE_COL = 3

try {
  const Disks = await dom.load(path.join(viewsDir, 'Disks.vue'))

  /* ── sorting: the header, not the comparator ─────────────────────────── */
  {
    const v = await mount(Disks, FLEET)
    const order = () => v.rows().map(tr => v.cells(tr)[DEVICE_COL])
    const th = (label) => v.byText('th', label)

    check('the table starts sorted by node, the order health.php hands over',
          v.cells(v.rows()[0])[0] === 'Golem')

    await v.click(th('Temp °C'))
    const asc = order()
    check('clicking a column header sorts by THAT column',
          asc.slice(0, 3).join() === ['/dev/sdb', '/dev/sdd', '/dev/sda'].join())
    /* The whole point of P2-7. A temp-less disk coerced to 0 leads an ascending
       sort and reads as the coldest drive in the fleet; both of these rows have
       no reading, and both belong at the bottom whichever way the arrow points. */
    check('a disk with no temperature does not lead the ascending sort',
          asc.slice(3).sort().join() === ['/dev/sdc', 'sdj'].join())

    await v.click(th('Temp °C'))
    const desc = order()
    check('clicking the same header again reverses the sort',
          desc.slice(0, 3).join() === ['/dev/sda', '/dev/sdd', '/dev/sdb'].join())
    check('the missing readings stay at the bottom in descending order too',
          desc.slice(3).sort().join() === ['/dev/sdc', 'sdj'].join())

    /* sortBy()'s else branch. Drop its `sortAsc = true` and the direction leaks
       from the last column into the next one, so the operator picks Slot and
       silently gets it backwards. Temp is left descending by the click above,
       which is what makes this fail if the reset goes. */
    await v.click(th('Slot'))
    check('picking a different column starts it ascending, not carrying the last arrow',
          v.cells(v.rows()[0])[1] === 'disk1')

    v.unmount()
  }

  /* ── the filters ─────────────────────────────────────────────────────── */
  {
    const v = await mount(Disks, FLEET)
    const nodesOf = () => new Set(v.rows().map(tr => v.cells(tr)[0]))

    check('every disk in the fleet is listed before any filter is touched',
          v.rows().length === 5)

    await v.click(v.byText('button', 'Golem'))
    check('clicking a node button narrows the table to that node',
          v.rows().length === 1 && nodesOf().has('Golem'))

    await v.click(v.byText('button', 'All nodes'))
    check('All nodes puts the rest of the fleet back',
          v.rows().length === 5 && nodesOf().size === 2)

    await v.click(v.byText('button', 'OK'))
    const smartOf = () => v.rows().map(tr => v.cells(tr)[9])
    check('a SMART filter keeps exactly the rows with that verdict',
          v.rows().length === 3 && smartOf().every(s => s === 'OK'))

    /* The scar this repo already has: the button label and smartOf()'s verdict
       for a model-less slot are one constant (NO_DISK). When they were two
       literals, renaming one left this button selecting nothing - and no test
       could see it, because SSR never clicks the button. */
    await v.click(v.byText('button', 'No disk'))
    check('the No-disk filter selects the array slots with no disk behind them',
          v.rows().length === 1 && v.cells(v.rows()[0])[DEVICE_COL] === 'sdj')
    check('an orphan slot is not also counted as a disk that answered UNKNOWN',
          !smartOf().includes('UNKNOWN'))

    await v.click(v.byText('button', 'UNKNOWN'))
    check('UNKNOWN selects the disk that answered UNKNOWN, and not the orphan',
          v.rows().length === 1 && v.cells(v.rows()[0])[DEVICE_COL] === '/dev/sdc')

    /* Two filters, two independent refs. If the second click replaced the first
       the table would show every OK disk in the fleet, Golem's included. */
    await v.click(v.byText('button', 'Raven'))
    await v.click(v.byText('button', 'OK'))
    check('the node and SMART filters compose rather than replacing each other',
          v.rows().length === 2 && nodesOf().has('Raven'))

    /* An empty result has to SAY it is empty. A filter that leaves the tbody
       blank looks identical to a screen that failed to load. */
    await v.click(v.byText('button', 'Golem'))
    await v.click(v.byText('button', 'No disk'))
    check('a filter pair that matches nothing says so instead of going blank',
          v.rows().length === 0 && v.text().includes('No disks reported yet.'))

    v.unmount()
  }

  /* ── Drift's collapse, and the um-warn limit views.mjs writes down ───── */
  {
    const NODES = [{ id: 'n1', name: 'Raven' }, { id: 'n2', name: 'Golem' },
                   { id: 'n3', name: 'Wraith' }]
    const Drift = await dom.load(path.join(viewsDir, 'Drift.vue'))
    const v = await mount(Drift, {
      data: {
        nodes: NODES,
        rows: [
          { key: 'unraid', kind: 'version', divergent: true,
            cells: { n1: '6.12.9', n2: '6.12.10', n3: null } },
          { key: 'kernel', kind: 'version', divergent: false,
            cells: { n1: '6.1.74', n2: '6.1.74', n3: '6.1.74' } },
        ],
        plugin_versions_available: false,
      },
    })
    const warned = () => [...v.host.querySelectorAll('td.um-warn')].length

    check('the collapse hides the row every node agrees on',
          v.rows().length === 1 && !v.text().includes('6.1.74'))

    await v.click(v.byText('button', 'Show all rows'))
    check('Show all rows reveals the identical row it was hiding',
          v.rows().length === 2 && v.text().includes('6.1.74'))
    /* THE limit views.mjs states and cannot close: with the collapse on, one
       divergent row and three node columns means "highlight the divergent
       cells" and "highlight all cells" paint the same three cells. With the
       agreeing row on screen the two differ - `{'um-warn': true}` would paint
       six here and this is the only check in the suite that dies of it. */
    check('with every row shown, only the divergent cells are still highlighted',
          warned() === 3)
    check('the agreeing row carries no warning on any of its cells',
          [...v.host.querySelectorAll('tbody tr')]
            .find(tr => tr.textContent.includes('6.1.74'))
            .querySelectorAll('.um-warn').length === 0)

    await v.click(v.byText('button', 'Show differences only'))
    check('the toggle collapses again rather than being one-way',
          v.rows().length === 1 && !v.text().includes('6.1.74'))

    v.unmount()
  }

  /* ── Overview.vue: the fleet grid's controls (P2-5) ─────────────────────── */
  /* fleet.mjs pins the rules; these pin that the SUMMARY LINE is wired to them.
     The counts are the filter, so a button labelled "1 degraded" that isolates
     something else - or a count that stops matching what the grid shows - is
     the exact defect the shared `$counts[$state]` in health.php exists to
     prevent, reintroduced on the client. */
  {
    const node = (name, state) => ({ id: name, name, state, indicators: {},
                                     age: 1, since: null, updated_at: null })
    const FLEET_NODES = [node('Atlas', 'ok'), node('Zeus', 'degraded'),
                         node('Boreas', 'unknown'), node('Aegis', 'ok')]
    const HEALTH = { data: {
      fleet: { nodes: 4, ok: 2, degraded: 1, unknown: 1 }, nodes: FLEET_NODES,
      stale_after: 180, tz: 'UTC', clock12: false } }

    const Overview = await dom.load(path.join(viewsDir, 'Overview.vue'))
    const v = await mount(Overview, HEALTH)
    // Card identity by heading text, not by index: a check reading "the first
    // card" keeps passing when the order it was written to prove is gone.
    const cards = () => [...v.host.querySelectorAll('.um-grid > *')]
      .map(el => FLEET_NODES.map(n => n.name).find(name => el.textContent.includes(name)))
      .filter(Boolean)

    check('the worst node leads the grid without anyone asking it to',
          cards()[0] === 'Zeus')
    check('...and the rest follow by rank, name breaking ties',
          cards().join() === 'Zeus,Boreas,Aegis,Atlas')

    const count = (label) => v.byText('button', label)
    check('every count is a button the operator can press',
          !!count('2 ok') && !!count('1 degraded') && !!count('1 unknown'))

    await v.click(count('1 degraded'))
    check('clicking a count isolates exactly that state', cards().join() === 'Zeus')
    check('the pressed count says so to a screen reader',
          count('1 degraded').getAttribute('aria-pressed') === 'true')

    await v.click(count('1 degraded'))
    check('clicking the active count again clears it rather than sticking',
          cards().length === 4)
    check('...and it stops reporting itself as pressed',
          count('1 degraded').getAttribute('aria-pressed') === 'false')

    /* Two filters that each keep something, and whose intersection is empty:
       an OR would show three cards here and read as a working filter. */
    await v.click(count('2 ok'))
    const search = v.host.querySelector('input[type="search"]')
    search.value = 'zeus'
    search.dispatchEvent(new win.Event('input', { bubbles: true }))
    await nextTick()
    check('the state and the search combine with AND', cards().length === 0)
    check('an empty result says the filter is hiding them, not that the fleet is empty',
          v.text().includes('No node matches') && !v.text().includes('No nodes enrolled'))

    await v.click(v.byText('button', 'Show all 4'))
    check('the escape hatch restores every node', cards().length === 4)
    check('...and empties the search box too, not just the state',
          v.host.querySelector('input[type="search"]').value === '')

    search.value = 'AE'
    search.dispatchEvent(new win.Event('input', { bubbles: true }))
    await nextTick()
    check('the search is case-insensitive on the name', cards().join() === 'Aegis')

    v.unmount()
  }

  /* A count of zero must not be pressable - it would blank the grid with no
     way to tell that from a broken pane. The ACTIVE one stays pressable
     whatever it reads, or a refresh that empties it strands the operator. */
  {
    const HEALTH = { data: {
      fleet: { nodes: 1, ok: 1, degraded: 0, unknown: 0 },
      nodes: [{ id: 'a', name: 'Alone', state: 'ok', indicators: {}, age: 1 }],
      stale_after: 180, tz: 'UTC', clock12: false } }
    const Overview = await dom.load(path.join(viewsDir, 'Overview.vue'))
    const v = await mount(Overview, HEALTH)
    check('a zero count is not pressable',
          v.byText('button', '0 degraded').disabled === true)
    check('a non-zero count still is',
          v.byText('button', '1 ok').disabled === false)
    v.unmount()
  }
} finally {
  dom.cleanup()
  await win.happyDOM.close()
}

done()
