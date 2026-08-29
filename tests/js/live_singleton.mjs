// Real proof of live.js's singleton, mount-time-fetch and refcounted-teardown
// behaviour - not a source-text grep. Mocks EventSource and setInterval, then
// drives useLive() the way App.vue and a view actually would: by mounting
// (and, for the teardown proof, actually UNmounting) callers through a real
// Vue component tree, never by calling tick() by hand or by faking
// onUnmounted. Zero dependencies beyond `vue` itself (already a runtime
// dependency) and node:assert.
//   node tests/js/live_singleton.mjs   ->   "live_singleton: all pass" (exit 0)
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendSrc = path.join(here, '..', '..', 'frontend', 'src')
const liveJsPath = path.join(frontendSrc, 'live.js')
const apiJsPath = path.join(frontendSrc, 'api.js')
// The exact file live.js's own `import ... from 'vue'` resolves to (import +
// node condition). Importing that same resolved path here, rather than
// reaching into `@vue/runtime-core` by a different route, guarantees this
// script's createRenderer/onUnmounted/getCurrentInstance share Vue's internal
// "current instance" state with live.js's - not a second, disconnected copy.
const vueEntryPath = path.join(here, '..', '..', 'frontend', 'node_modules', 'vue', 'index.mjs')

let fails = 0
function check (name, ok) {
  console.log((ok ? 'PASS  ' : 'FAIL  ') + name)
  if (!ok) fails++
}

const eventSourceInstances = []
class FakeEventSource {
  constructor (url) { this.url = url; this.onmessage = null; eventSourceInstances.push(this) }
}
global.EventSource = FakeEventSource

const intervalCalls = [] // { fn, ms }
const realSetInterval = global.setInterval
global.setInterval = (fn, ms) => { intervalCalls.push({ fn, ms }); return realSetInterval(fn, ms) }

// Let every pending microtask (and the promise chains kick() builds on top of
// them) settle, without waiting on any real timer.
function flush () {
  return new Promise((resolve) => setImmediate(resolve))
}

const { useLive } = await import(pathToFileURL(liveJsPath).href)
const { createRenderer } = await import(pathToFileURL(vueEntryPath).href)

// A headless renderer: no DOM, no jsdom dependency. Every op is a no-op or
// returns a placeholder - enough to mount/unmount a component whose render
// function returns null (a single comment-node placeholder), which is all
// that's needed to run setup(), its onUnmounted hooks, and nothing else.
const { createApp } = createRenderer({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  setText () {},
  setElementText () {},
  insert () {},
  remove () {},
  parentNode: () => null,
  nextSibling: () => null,
  patchProp () {}
})
function mountHeadless (setup) {
  const app = createApp({ setup, render: () => null })
  app.mount({})
  return () => app.unmount()
}

// A window and a document with nothing but addEventListener. Installed here,
// after the imports above, so defining `window` cannot change how Vue itself
// decided to behave when it was loaded.
const fakeListeners = { document: {}, window: {} }
const fakeDocument = {
  visibilityState: 'visible',
  addEventListener: (ev, fn) => { (fakeListeners.document[ev] ||= []).push(fn) },
}
global.document = fakeDocument
global.window = {
  addEventListener: (ev, fn) => { (fakeListeners.window[ev] ||= []).push(fn) },
}

/* ── the first caller must fetch before any timer fires ──────────────────── */
let callsA = 0
useLive(async () => { callsA++ })
check('the first useLive() call opens exactly one EventSource', eventSourceInstances.length === 1)
check('the first useLive() call starts exactly one pair of timers', intervalCalls.length === 2)
await flush()
check('mounting the first caller fetches immediately, not after the 30s fallback',
      callsA === 1)

/* ── a second caller (e.g. a tab switch) must not wait either ───────────── */
let callsB = 0
const callsA_beforeSecondMount = callsA
useLive(async () => { callsB++ })
check('a second useLive() call does not open a second EventSource', eventSourceInstances.length === 1)
check('a second useLive() call does not start a second pair of timers', intervalCalls.length === 2)
await flush()
check('mounting a second caller fetches immediately for that caller too',
      callsB === 1)
check('mounting a second caller does not re-fetch for the first',
      callsA === callsA_beforeSecondMount)

/* ── the fallback timer must actually refetch, not just exist ───────────── */
// frontend_test.php only checks that the literal 30000 appears in live.js;
// this is the behavioural half - grab the callback actually handed to
// setInterval(tick, FALLBACK_MS) and run it.
const fallbackEntry = intervalCalls.find((c) => c.ms === 30000)
assert.ok(fallbackEntry, 'setInterval was never called with FALLBACK_MS (30000)')
const beforeFallback = { a: callsA, b: callsB }
fallbackEntry.fn()
await flush()
check('the 30s fallback timer actually re-fetches every registered caller',
      callsA === beforeFallback.a + 1 && callsB === beforeFallback.b + 1)

/* ── an nchan message must actually refetch, not just "use EventSource" ─── */
// frontend_test.php only checks that the word EventSource appears; this is
// the behavioural half - drive the mocked stream's onmessage the way a real
// nchan push would and confirm it re-fetches.
const stream = eventSourceInstances[0]
assert.ok(stream && typeof stream.onmessage === 'function', 'EventSource.onmessage was never wired up')
const beforeMessage = { a: callsA, b: callsB }
stream.onmessage()
await flush()
check('an nchan message actually re-fetches every registered caller',
      callsA === beforeMessage.a + 1 && callsB === beforeMessage.b + 1)

/* ── fix round 2's Critical: a shared (memoised) refresh must survive one ──
   of its two registrants unmounting ───────────────────────────────────── */
// useEndpoint() memoises `refresh` per name, so App.vue's heartbeat call and
// a view's call for the same endpoint hand useLive() the SAME function
// object. A Set keyed by that identity has one entry for both; the view's
// onUnmounted deleting it deletes App.vue's registration too - permanently.
// Reproduced here with App.vue simulated as a registration with no
// component instance (never unmounts, exactly like the real root), and the
// view simulated as a REAL mount/unmount so the onUnmounted branch actually
// runs, not just exists.
let sharedCalls = 0
const sharedRefresh = async () => { sharedCalls++ }
const { tick: sharedTick } = useLive(sharedRefresh) // "App.vue"
await flush()
const unmountView = mountHeadless(() => { useLive(sharedRefresh) }) // "Overview.vue", same memoised refresh
await flush()
unmountView() // the tab switch away from Overview
sharedCalls = 0
sharedTick()
await flush()
check('a shared refresh still fetches after ONE of its two registrants unmounts (round-2 Critical)',
      sharedCalls === 1)

/* ── onUnmounted must genuinely deregister a solo caller ─────────────────── */
// The other half of the same invariant: when the LAST registrant of a given
// refresh unmounts, it must stop being ticked - proving unregister() deletes
// at zero rather than only ever decrementing.
let soloCalls = 0
const soloRefresh = async () => { soloCalls++ }
const unmountSolo = mountHeadless(() => { useLive(soloRefresh) })
await flush()
check('a solo view fetches on mount', soloCalls === 1)
unmountSolo()
soloCalls = 0
sharedTick() // the one shared tick() every useLive() caller gets back
await flush()
check('a caller with no remaining registrant is not fetched after it unmounts',
      soloCalls === 0)

/* ── useEndpoint must memoise per name (identity, not a source-text grep) ── */
const { useEndpoint } = await import(pathToFileURL(apiJsPath).href)
const first = useEndpoint('health')
const second = useEndpoint('health')
check('useEndpoint(name) returns the identical object on a second call',
      first === second && first.refresh === second.refresh)

/* ── kick()'s success handler is how the stale banner ever CLEARS ─────────
   No-op'ing it left all 13 checks and all 58 PHP checks green, while `stale`
   would latch true after three minutes and never come back down - the same
   "banner latches permanently" family as the round-2 Critical. Nothing was
   asserting the fulfilled branch, only that a fetch happened. */
{
  let ok = () => {}
  const probe = () => { ok(); return Promise.resolve() }
  const { stale } = useLive(probe)
  stale.value = true                       // pretend three minutes elapsed
  await new Promise(r => setTimeout(r, 0)) // let the mount kick settle
  check('a successful refresh clears the stale flag', stale.value === false)

  stale.value = true
  const failing = useLive(() => Promise.reject(new Error('endpoint down')))
  await new Promise(r => setTimeout(r, 0))
  check('a failed refresh does NOT clear the stale flag', failing.stale.value === true)
}


/* ── waking on focus ─────────────────────────────────────────────────────
   A monitoring pane that is wrong when you look at it is worse than one that
   is slow. Browsers throttle setInterval in background tabs and freeze it in
   occluded ones, and with managerd dead there are no nchan nudges either - so
   that 30s timer is the ONLY thing driving updates and an unfocused window
   goes completely static. Observed on Raven 2026-08-29: the daemon was down
   for minutes, the window was behind a terminal, and the pane only caught up
   on a manual F5.

   BOTH events, because they answer different questions. `visibilitychange`
   fires for a hidden TAB; a window merely sitting behind another one is still
   `visible` by the spec, so only `focus` fires for it - and that was the case
   that actually bit. */
{
  /* Fires whatever is registered and nothing if nothing is. A missing
     listener must fail the checks below by NAME, not by throwing on
     `undefined[0]` - a stack trace says a test broke, where a named failure
     says the code did. */
  const fire = (bag, ev) => { for (const fn of bag[ev] || []) fn() }

  check('live.js listens for focus, not only for tab visibility',
        (fakeListeners.window.focus || []).length === 1
        && (fakeListeners.document.visibilitychange || []).length === 1)

  let wakeCalls = 0
  useLive(async () => { wakeCalls++ })
  await flush()
  const before = wakeCalls

  fakeDocument.visibilityState = 'hidden'
  fire(fakeListeners.document, 'visibilitychange')
  await flush()
  check('a visibilitychange to HIDDEN does not fetch - nobody is looking',
        wakeCalls === before)

  fakeDocument.visibilityState = 'visible'
  fire(fakeListeners.window, 'focus')
  await flush()
  check('focusing the window refetches at once rather than waiting on the 30s timer',
        wakeCalls === before + 1)

  /* Chrome fires focus and visibilitychange together when a tab is both
     unhidden and focused. Two fetches for one glance is a doubled request
     rate on every tab switch, for nothing. */
  // Past the debounce window, so this scenario starts from a clean slate
  // rather than being suppressed by the wake just above it.
  await new Promise(r => setTimeout(r, 300))
  const beforePair = wakeCalls
  fire(fakeListeners.window, 'focus')
  fire(fakeListeners.document, 'visibilitychange')
  await flush()
  check('two wake events arriving together cause one fetch, not two',
        wakeCalls === beforePair + 1)
}

console.log(fails === 0 ? 'live_singleton: all pass' : `live_singleton: ${fails} FAILED`)
process.exit(fails === 0 ? 0 : 1) // live.js's real setInterval calls would otherwise keep the loop alive
