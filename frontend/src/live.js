import { ref, onUnmounted, getCurrentInstance } from 'vue'

const CHANNEL = '/sub/unraid-manager'
const FALLBACK_MS = 30000
// Exported so App.vue judges the AGE OF THE DATA against the same threshold
// this module judges the age of the last response against (P1 exit, F-1).
// Two copies of "three minutes" would drift, and the two conditions have to
// mean the same thing to the operator.
export const STALE_MS = 180000

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
// A refcounted Map, not a Set: useEndpoint() memoises `refresh` per endpoint
// name, so App.vue's own heartbeat call and a view's call for the same
// endpoint (Tasks 13-15) register the SAME function object. A Set keyed by
// that identity has only one entry for both of them - when the view unmounts
// and its onUnmounted does callbacks.delete(refresh), it deletes App.vue's
// registration too, permanently: 1 fetch per tick, then 0, forever. Counting
// registrations and deleting only at zero keeps the shared entry alive for
// as long as anyone - including App.vue, which never unmounts - still holds it.
const callbacks = new Map()
let started = false

function register (refresh) {
  callbacks.set(refresh, (callbacks.get(refresh) || 0) + 1)
}

function unregister (refresh) {
  const count = (callbacks.get(refresh) || 0) - 1
  if (count <= 0) callbacks.delete(refresh)
  else callbacks.set(refresh, count)
}

function kick (cb) {
  return Promise.resolve().then(cb).then(
    () => { lastGood.value = Date.now(); stale.value = false },
    () => { /* the staleness check below is the report */ }
  )
}

function tick () {
  for (const cb of callbacks.keys()) kick(cb)
}

// A monitoring pane that is wrong when you look at it is worse than one that
// is slow. Browsers throttle setInterval in background tabs and freeze it
// outright in occluded ones, and with managerd dead there are no nchan nudges
// either - so that 30s timer is the ONLY thing driving updates, and a window
// sitting behind another one goes completely static. Observed on Raven
// 2026-08-29: the daemon was down for minutes and the pane only caught up on
// a manual F5.
//
// Both events, because they answer different questions. `visibilitychange`
// fires for a hidden TAB; a window merely behind another one is still
// `visible` by the spec, so only `focus` fires for it - and that is the case
// that actually bit.
// Long enough to collapse the pair a browser fires for one glance - they land
// milliseconds apart - and short enough that genuinely looking away and back
// still refetches. Not a rate limit; the 30s timer is that.
const WAKE_DEBOUNCE_MS = 250
let lastWake = 0

function wake () {
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
  // Chrome fires both together when a tab is unhidden AND focused; two
  // fetches for one glance is a doubled request rate on every tab switch.
  const now = Date.now()
  if (now - lastWake < WAKE_DEBOUNCE_MS) return
  lastWake = now
  tick()
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
  // Guarded: these harnesses render live.js with no DOM at all.
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('visibilitychange', wake)
  }
  if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('focus', wake)
  }
}

export function useLive (refresh) {
  if (refresh) {
    // Register, then kick THIS caller immediately - before start() - so a
    // brand new callback never waits on the shared 30s timer, whether it is
    // the very first caller (page load) or the Nth (a tab switch mounting a
    // view mid-session).
    register(refresh)
    // Only meaningful inside a component's setup(). App.vue's own call
    // supplies a refresh too, but App.vue is the root and never unmounts, so
    // its registration is never decremented away.
    if (getCurrentInstance()) onUnmounted(() => unregister(refresh))
    kick(refresh)
  }
  start()
  return { stale, tick, lastGood }
}
