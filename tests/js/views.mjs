// Real proof of what Disks.vue and Drift.vue actually RENDER (Tasks 14-15).
// Same argument as node_card.mjs: every fact these two screens exist to carry
// is a fact about output, and every one of them has a null in it -
//   a slot with no disk behind it (model: null) vs a healthy disk,
//   0 errors vs errors unknown,
//   a node never polled vs a node whose poll failed on a retained payload,
//   a plugin absent (false) vs a plugin unreported (null).
// A grep over the source cannot tell any of those pairs apart; a comment
// sitting next to the right branch satisfies one just as well as the branch.
// ../api.js and ../live.js are stubbed so a fixture can be rendered at all -
// the real ones open a fetch, an EventSource and two intervals on call.
//   node tests/js/views.mjs   ->   "views: all pass" (exit 0)
import path from 'node:path'
import { createCompiler, frontend, reporter } from './ssr.mjs'

const viewsDir = path.join(frontend, 'src', 'views')
const { check, done } = reporter('views')

const ssr = createCompiler({
  stubs: {
    '../api.js': `
      import { ref } from 'vue'
      export function useEndpoint () {
        const f = globalThis.__um_fixture ?? {}
        return {
          data: ref(f.data ?? null),
          error: ref(f.error ?? null),
          loading: ref(f.loading ?? false),
          dbUnreadable: ref(f.dbUnreadable ?? false),
          refresh: async () => {},
        }
      }
    `,
    '../live.js': `
      import { ref } from 'vue'
      export function useLive () {
        return { stale: ref(false), tick: () => {}, lastGood: ref(Date.now()) }
      }
    `,
  },
})

/* The fixture is read at setup() time by the api.js stub above, so it has to
   be in place before the render, not passed as a prop: these are views, not
   components - they take their data from the endpoint, which is the whole
   thing under test. */
async function renderView (component, fixture) {
  globalThis.__um_fixture = fixture
  try {
    return await ssr.render(component)
  } finally {
    globalThis.__um_fixture = undefined
  }
}

const DISK = {
  node: 'Raven', node_id: 'n1', model: 'ST10000NM0226', device: '/dev/sda',
  vendor: 'Seagate', size: 10000831348736, temp: 34, smart_status: 'OK',
  interface: 'SATA', slot: 'disk1', errors: 0, array_status: 'DISK_OK',
  fetched_at: '2026-08-28T00:00:00Z',
}
/* Exactly the shape disks.php emits for an array slot with nothing behind it
   in the physical enumeration - a drive that fell off the bus. */
const ORPHAN = {
  node: 'Raven', node_id: 'n1', model: null, device: 'sdj',
  vendor: null, size: null, temp: null, smart_status: null, interface: null,
  slot: 'disk7', errors: 12, array_status: 'DISK_DSBL',
  fetched_at: '2026-08-28T00:00:00Z',
}

try {
  const Disks = await ssr.load(path.join(viewsDir, 'Disks.vue'))

  /* ── amendment B: an orphan row is the most important row on the screen ── */
  const htmlOrphan = await renderView(Disks, { data: { disks: [DISK, ORPHAN], spares: [], stale: [] } })
  check('an orphan slot (model: null) is rendered, not filtered away',
        htmlOrphan.includes('disk7'))
  /* Scoped to the row, never to the document: /no disk/ over the whole render
     is also matched by the "No disk" filter button, which made this check pass
     against a view with amendment B deleted outright. Same mistake, and the
     same fix, as the UNKNOWN check below. */
  const orphanRow = (htmlOrphan.split('<tr').find(r => r.includes('no disk present')) ?? '').split('</tr>')[0]
  const diskRow = (htmlOrphan.split('<tr').find(r => r.includes('/dev/sda')) ?? '').split('</tr>')[0]
  check('an orphan slot is marked by a WORD, not only by a colour or a blank cell',
        /no disk present/.test(orphanRow))
  check('exactly one of the two rows is marked as an orphan',
        (htmlOrphan.match(/no disk present/g) || []).length === 1)
  check('an orphan slot still shows what the array DID report (slot, errors)',
        orphanRow.includes('disk7') && orphanRow.includes('12'))
  /* array_status is what tells the operator which Saturday this is: DISK_DSBL
     means the array is emulating the missing disk, DISK_NP means the slot was
     never filled. */
  check('an orphan slot reports what the array thinks of the slot',
        orphanRow.includes('DISK_DSBL'))
  /* The Model column had no assertion at all - the one amendment A exists for.
     A blank cell, the wrong field, or the old `name` under a renamed loop
     variable all passed both suites. */
  check('a real disk renders its model, the field the endpoint emits',
        diskRow.includes('ST10000NM0226'))
  /* Survived the first mutation round: dropping the orphan branch out of
     smartOf() left the row claiming SMART UNKNOWN - a disk that answered "I
     don't know" - for a slot where nothing answered at all. Neither fixture
     row is UNKNOWN, so the word must not appear anywhere in this render. */
  check('an orphan slot is not reported as a disk whose SMART came back UNKNOWN',
        orphanRow !== '' && !orphanRow.includes('UNKNOWN'))
  /* Twice: "no disk present" in the Model column and "no disk" in the SMART
     column, which is also the word the No-disk filter button offers. Renaming
     the shared constant alone left the button labelled "No disk" and the cell
     reading something else, with everything else green. */
  check('the orphan row uses one word for the state, the one the filter offers',
        (orphanRow.match(/no disk/g) || []).length === 2)

  /* ── null-vs-zero, the family this repo has shipped wrong twice ────────── */
  const htmlZeroErrors = await renderView(Disks, { data: { disks: [DISK], spares: [], stale: [] } })
  const htmlNullErrors = await renderView(Disks, {
    data: { disks: [{ ...DISK, errors: null }], spares: [], stale: [] },
  })
  check('errors: 0 and errors: null render differently', htmlZeroErrors !== htmlNullErrors)
  check('errors: 0 renders a real zero, not an em dash', />\s*0\s*</.test(htmlZeroErrors))
  check('errors: null never renders a zero the array never reported',
        !/>\s*0\s*</.test(htmlNullErrors))
  /* And it gets the same "we cannot see this" treatment NodeCard gives a null
     unread count - asserting only the absence of a zero left the um-unknown
     class deletable with the suite green. */
  const nullErrRow = (htmlNullErrors.split('<tr').find(r => r.includes('/dev/sda')) ?? '').split('</tr>')[0]
  check('errors: null gets the unknown treatment, not a plain empty-looking cell',
        /um-unknown[^>]*>\s*—/.test(nullErrRow))

  /* ── amendment C: both stale shapes, and neither reads as an error ─────── */
  const htmlNeverPolled = await renderView(Disks, {
    data: {
      disks: [], spares: [],
      stale: [{ node: 'Golem', node_id: 'n2', status: 'unknown',
                error: 'no disks poll recorded yet', fetched_at: null }],
    },
  })
  const htmlFailedPoll = await renderView(Disks, {
    data: {
      disks: [DISK], spares: [],
      stale: [{ node: 'Golem', node_id: 'n2', status: 'error',
                error: 'HTTP 504 from 10.0.0.9', fetched_at: '2026-08-27T09:00:00Z' }],
    },
  })
  check('a never-polled node and a failed poll do not render the same sentence',
        htmlNeverPolled !== htmlFailedPoll)
  check('a never-polled node reads as pending, not as a failure',
        /not been polled|no disk list yet/i.test(htmlNeverPolled))
  check('a never-polled node never renders a bare "null" where a time would go',
        !/null/.test(htmlNeverPolled))
  /* Vue renders {{ null }} as an empty string, so the check above only bites a
     string concatenation. This one bites the interpolation: the never-polled
     sentence must not reach for a timestamp it does not have. */
  check('a never-polled node does not claim to be showing data collected at some time',
        !/collected/.test(htmlNeverPolled))
  check('a failed poll names the real error so the operator can act on it',
        htmlFailedPoll.includes('HTTP 504 from 10.0.0.9'))
  check('a failed poll says how old the data on screen is',
        htmlFailedPoll.includes('2026-08-27T09:00:00Z'))
  check('both stale entries name the node they are about',
        htmlNeverPolled.includes('Golem') && htmlFailedPoll.includes('Golem'))

  /* ── never a blank pane, never a second wrong claim (Task 13 items 6, 7) ── */
  const htmlDown = await renderView(Disks, { data: null, error: 'disks.php: HTTP 502', loading: false })
  check('a view with no data yet renders something, and surfaces the reason',
        htmlDown.includes('HTTP 502'))
  const htmlNoDb = await renderView(Disks, { data: { disks: [], spares: [], stale: [] }, dbUnreadable: true })
  check('an unreadable database does not also claim the fleet reported no disks',
        !/No disks/i.test(htmlNoDb))

  /* ── spares ───────────────────────────────────────────────────────────── */
  const htmlSpare = await renderView(Disks, {
    data: { disks: [], stale: [], spares: [{ node: 'Raven', node_id: 'n1', model: 'WDC WD80EFAX',
            device: '/dev/sdz', vendor: 'WDC', size: 8001563222016, smart_status: 'OK',
            fetched_at: '2026-08-28T00:00:00Z' }] },
  })
  check('a spare is listed by the model the endpoint emits, not a name field it does not',
        htmlSpare.includes('WDC WD80EFAX'))

  /* ── drift ────────────────────────────────────────────────────────────── */
  const Drift = await ssr.load(path.join(viewsDir, 'Drift.vue'))
  const NODES = [{ id: 'n1', name: 'Raven' }, { id: 'n2', name: 'Golem' }, { id: 'n3', name: 'Wraith' }]
  const pluginRow = {
    key: 'plugin:dynamix.cache.dirs', kind: 'plugin', divergent: true,
    cells: { n1: true, n2: false, n3: null },
  }
  const sameRow = { key: 'kernel', kind: 'version', divergent: false,
                    cells: { n1: '6.1.74', n2: '6.1.74', n3: '6.1.74' } }
  const versionRow = { key: 'unraid', kind: 'version', divergent: true,
                       cells: { n1: '6.12.9', n2: '6.12.10', n3: null } }

  const htmlDrift = await renderView(Drift, {
    data: { nodes: NODES, rows: [versionRow, sameRow, pluginRow], plugin_versions_available: false },
  })
  check('the matrix is headed by node NAMES, not the opaque ids they are keyed by',
        htmlDrift.includes('Wraith') && !htmlDrift.includes('>n3<'))
  check('a divergent row is highlighted as such', htmlDrift.includes('um-warn'))
  check('a plugin present on a node reads "present"', htmlDrift.includes('present'))
  check('a plugin absent from a node reads "absent", not a blank cell',
        htmlDrift.includes('absent'))
  check('a node that never reported its plugins is NOT called absent',
        (htmlDrift.match(/absent/g) || []).length === 1)
  check('a version a node never reported is not rendered as a value',
        htmlDrift.includes('6.12.10') && !htmlDrift.includes('>null<'))
  check('identical rows are collapsed away by default', !htmlDrift.includes('6.1.74'))
  check('the collapse says how many rows it hid', /1 identical row/.test(htmlDrift))
  check('divergent rows survive the collapse',
        htmlDrift.includes('6.12.9') && htmlDrift.includes('dynamix.cache.dirs'))
  check('the plugin row is not labelled with its internal "plugin:" prefix',
        !htmlDrift.includes('plugin:'))
  check('the tier 0 plugin-version limit is stated when the endpoint reports it',
        /Tier 1/.test(htmlDrift))

  const htmlDriftSame = await renderView(Drift, {
    data: { nodes: NODES, rows: [sameRow], plugin_versions_available: false },
  })
  check('a fleet that agrees on everything says so rather than showing an empty table',
        /Nothing differs/i.test(htmlDriftSame))

  const htmlDriftDown = await renderView(Drift, { data: null, error: 'drift.php: HTTP 502' })
  check('the drift view never renders a blank pane either',
        htmlDriftDown.includes('HTTP 502'))
  const htmlDriftNoDb = await renderView(Drift, {
    data: { nodes: [], rows: [], plugin_versions_available: false }, dbUnreadable: true,
  })
  check('an unreadable database does not also claim the fleet agrees on everything',
        !/Nothing differs/i.test(htmlDriftNoDb))
} finally {
  ssr.cleanup()
}

done()
