import { ref, onUnmounted, getCurrentInstance } from 'vue'

const CHANNEL = '/sub/unraid-manager'
const FALLBACK_MS = 30000
const STALE_MS = 180000

// One EventSource and two timers for the life of the page - not one per
// caller. App.vue and each of Tasks 13-15's views call useLive(), and
// <component :is> unmounts the inactive view on every tab switch; without a
// module-level singleton that would open a new stream and start two new
// timers on every switch, all still firing after the view that started them
// is gone. P0 ran with exactly one of each for its whole lifetime - this is
// that same invariant, shared across callers instead of scoped to a page with
// only one caller.
const stale = ref(false)
// Seeded at load rather than zero: "never succeeded once" is the case the
// banner exists for. A 0 seed would suppress it forever in exactly that case.
// A ref, not a private snapshot: the stale banner needs to say when the last
// good response actually landed, the way P0's did.
const lastGood = ref(Date.now())
const callbacks = new Set()
let started = false

function kick (cb) {
  return Promise.resolve().then(cb).then(
    () => { lastGood.value = Date.now(); stale.value = false },
    () => { /* the staleness check below is the report */ }
  )
}

function tick () {
  for (const cb of callbacks) kick(cb)
}

function start () {
  if (started) return
  started = true
  // nchan carries a nudge, never the data: on any message every registered
  // caller re-fetches through the authenticated API. If the stream never
  // connects, the fallback timer is the whole mechanism and the pane still
  // works.
  try {
    const stream = new EventSource(CHANNEL)
    stream.onmessage = tick
    stream.onerror = () => { /* the fallback timer covers it */ }
  } catch {
    /* no EventSource: fallback only */
  }
  // NOT tick() here: start() only builds the shared stream/timers, once.
  // Fetching for a given caller happens when that caller registers (below),
  // whether it is the first ever caller or the fiftieth - otherwise the
  // first page load, and every later tab switch, waits up to FALLBACK_MS for
  // its first data.
  setInterval(tick, FALLBACK_MS)
  setInterval(() => { stale.value = Date.now() - lastGood.value > STALE_MS }, 15000)
}

export function useLive (refresh) {
  if (refresh) {
    // Register, then kick THIS caller immediately - before start() - so a
    // brand new callback never waits on the shared 30s timer, whether it is
    // the very first caller (page load) or the Nth (a tab switch mounting a
    // view mid-session).
    callbacks.add(refresh)
    // Only meaningful inside a component's setup(). App.vue's own call
    // supplies a refresh too, so this also unregisters cleanly there.
    if (getCurrentInstance()) onUnmounted(() => callbacks.delete(refresh))
    kick(refresh)
  }
  start()
  return { stale, tick, lastGood }
}
