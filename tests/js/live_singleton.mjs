// Real proof of live.js's singleton and mount-time-fetch behaviour, not a
// source-text grep: mocks EventSource and setInterval, then drives useLive()
// the way App.vue and a view actually would - by mounting callers, never by
// calling tick() by hand. Zero dependencies, node:assert only.
//   node tests/js/live_singleton.mjs   ->   "live_singleton: all pass" (exit 0)
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const liveJsPath = path.join(here, '..', '..', 'frontend', 'src', 'live.js')

let fails = 0
function check (name, ok) {
  console.log((ok ? 'PASS  ' : 'FAIL  ') + name)
  if (!ok) fails++
}

let eventSourceCount = 0
class FakeEventSource {
  constructor (url) { eventSourceCount++; this.url = url }
}
global.EventSource = FakeEventSource

let intervalCount = 0
const realSetInterval = global.setInterval
global.setInterval = (fn, ms) => { intervalCount++; return realSetInterval(fn, ms) }

// Let every pending microtask (and the promise chains kick() builds on top of
// them) settle, without waiting on any real timer.
function flush () {
  return new Promise((resolve) => setImmediate(resolve))
}

const { useLive } = await import(pathToFileURL(liveJsPath).href)

/* ── item 1: the first caller must fetch before any timer fires ─────────── */
let callsA = 0
useLive(async () => { callsA++ })
check('the first useLive() call opens exactly one EventSource', eventSourceCount === 1)
check('the first useLive() call starts exactly one pair of timers', intervalCount === 2)
await flush()
check('mounting the first caller fetches immediately, not after the 30s fallback',
      callsA === 1)

/* ── item 2: a second caller (e.g. a tab switch) must not wait either ───── */
let callsB = 0
useLive(async () => { callsB++ })
check('a second useLive() call does not open a second EventSource', eventSourceCount === 1)
check('a second useLive() call does not start a second pair of timers', intervalCount === 2)
await flush()
check('mounting a second caller fetches immediately for that caller too',
      callsB === 1)
check('mounting a second caller does not re-fetch for the first',
      callsA === 1)

console.log(fails === 0 ? 'live_singleton: all pass' : `live_singleton: ${fails} FAILED`)
process.exit(fails === 0 ? 0 : 1) // live.js's real setInterval calls would otherwise keep the loop alive
