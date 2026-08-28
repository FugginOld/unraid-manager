import { ref } from 'vue'

const BASE = '/plugins/unraid-manager/api'

// The top-level key each endpoint's payload always carries. A 200 that lacks
// it - a malformed body, a stray {"error": …} - is not a fresh refresh; if we
// accepted it, `lastGood` in live.js would stamp forward and the stale banner
// would hide a screen showing nothing. This is the P0 guard
// (`if (!r || !r.nodes) return`) generalised across the three endpoints, and
// it is a different failure mode from `db: false` below: this one is "the
// response is not shaped like a payload at all", not "the database could not
// be opened".
const EXPECT_KEY = { health: 'nodes', disks: 'disks', drift: 'rows' }

// GET only. P1 is strictly read-only against every peer and the pane mutates
// nothing, so nothing here needs a CSRF token and no credential ever travels in
// a URL or a body from this file.
export async function get (path) {
  const response = await fetch(`${BASE}/${path}`, { credentials: 'same-origin' })
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`)
  return response.json()
}

export function useEndpoint (name) {
  const data = ref(null)
  const error = ref(null)
  const loading = ref(true)
  // True when the response parsed but its `db` field says PHP could not open
  // the database. Distinct from `error` (transport/HTTP failure - the
  // request itself didn't succeed) and from `loading`: the request
  // succeeded, the payload is just reporting that there is nothing readable
  // behind it. Left false, an unset db_path renders byte-identical to a
  // healthy, empty fleet.
  const dbUnreadable = ref(false)

  async function refresh () {
    try {
      const json = await get(`${name}.php`)
      const expectKey = EXPECT_KEY[name]
      if (!json || (expectKey && !(expectKey in json))) {
        throw new Error(`${name}.php: response missing "${expectKey}"`)
      }
      data.value = json
      dbUnreadable.value = json.db === false
      error.value = null
    } catch (err) {
      // Keep the last good data on screen; the stale banner is what tells the
      // operator it is old. Blanking the pane on one failed poll is worse than
      // showing numbers that are a minute stale.
      error.value = err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  return { data, error, loading, dbUnreadable, refresh }
}
