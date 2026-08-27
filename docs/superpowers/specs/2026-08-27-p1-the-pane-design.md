# Unraid-Manager P1 — The pane: design spec

**Phase:** P1 (plan §11). **Exit criterion:** replaces opening two browser tabs.
**Builds on:** P0, verified live on Raven and Golem 2026-08-26
([p0-exit.md](../../verification/p0-exit.md)).

## Decisions this spec inherits

Settled during brainstorming, 2026-08-27:

1. **Tier 0 only, with honest limits.** No node-mode agent in P1. Where the API
   cannot supply something the UI says so rather than showing a blank.
2. **Vue 3 + Vite**, mounted the way Unraid mounts its own components. Chosen on
   host convention after finding `dynamix.my.servers/unraid-components` is a
   Vite-built Vue library read through a runtime manifest.
3. **Rollup inputs:** per-domain reachability (from P0) plus four computed
   indicators — array state, capacity headroom, thermal, disk error rate.
4. **Hysteresis is asymmetric:** escalate on 2 consecutive agreeing samples,
   clear on 5.
5. **Health is computed in the daemon and persisted.** Not in PHP per request,
   not in the browser.

## Constraints from live verification (binding)

`docs/verification/tier0-coverage.md` overrides this document wherever they
disagree. The ones that shape P1:

- **No SMART attributes at Tier 0.** `smartStatus` is `OK|UNKNOWN` only. M4's
  verdict chain and confidence tiers need Tier 1 `smartctl`; P1 ships a disk
  table, not a verdict.
- **No plugin versions at Tier 0.** `installedUnraidPlugins` returns names only.
  Drift covers core/API/kernel/php/docker versions and plugin *presence*.
- **No pool profile or redundancy at Tier 0.** The `redundancy: 0` warning from
  plan §10.4 is Tier 1 and is not in P1.
- **`Query.disks` 504s at nginx's 60s timeout on either box**, load-dependent.
  The disk table must render from a possibly-stale last-good payload and say how
  stale it is.
- **A node is `unknown` only when nothing about it is readable** — corrected
  live; one blind domain is `degraded`.

## 1. Health engine — `daemon/health.py`

New module, pure functions, no I/O.

```
evaluate(payloads: dict, thresholds: dict) -> dict[str, Indicator]
    Indicator = namedtuple('Indicator', 'state value basis')
    state ∈ {ok, watch, warn, unknown}
```

Indicators, all from fast-lane payloads already collected:

| Indicator | Source | Rule |
| --- | --- | --- |
| `array_state` | `array.state` | `STARTED` → ok; `STOPPED` → watch; anything else → warn. An **empty** started array is ok (constraint 3). |
| `capacity` | `array.capacity` | used/total against `capacity_high_water` (default 90) → `warn`; ten points below it → `watch`. A zero-total array yields `ok`, never a division by zero. |
| `thermal` | `array.temp_max` | ≥ `temp_crit` (60) → warn; ≥ `temp_warn` (50) → watch. `None` → `unknown`, never ok. |
| `disk_errors` | `array.errors_total` | A **rate**, not an absolute: `warn` while the counter has increased at any point inside a trailing window (`error_window_min`, default 15). Not "increased since the last sample" — a one-off jump would then be visible for a single poll, never survive the 2-sample escalation, and be lost. A counter that has not moved inside the window is `ok` regardless of its magnitude: three errors logged in 2019 are not a problem. |

`disk_errors` requires history, so `array.errors_total` joins the samples the
collector already emits. Absent a previous sample the indicator is `unknown`.

```
apply_hysteresis(current, proposed, pending_state, pending_count, up=2, down=5)
    -> (state, pending_state, pending_count)
```

Pure and table-driven in tests. Escalation (toward a worse state) needs `up`
consecutive agreeing evaluations; return to `ok` needs `down`. A proposal that
disagrees with the pending one resets the counter. Severity order for
"worse": `ok < watch < warn`, with `unknown` outside the ladder — it is
reachability, and P0's existing 3-failure rule owns it.

**Overall node state** is the worst of two independent rollups:

1. the **domain** rollup P0 already computes — every domain unreadable →
   `unknown`, any domain unreadable or errored alongside a readable one →
   `degraded`, else `ok`;
2. the **indicator** rollup defined here — any `warn` or `watch` → `degraded`,
   else `ok`.

`unknown` can only come from (1). An indicator that evaluates to `unknown` — a
missing temperature, no previous sample for the rate — is **excluded from the
worst-of and never makes the node unknown**; it is shown as unknown in the
detail. Otherwise a box that reports no disk temperature would render as
unreachable, which is the exact defect corrected live on 2026-08-26.

`watch` and `warn` both roll up to `degraded` because the chip is three-valued;
they stay distinct in the per-indicator detail, which is where an operator looks
to find out how bad it is.

## 2. Persistence — `node_health`

```sql
CREATE TABLE IF NOT EXISTS node_health(
  node_id TEXT NOT NULL, indicator TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('ok','watch','warn','unknown')),
  value REAL, basis TEXT,
  pending_state TEXT, pending_count INTEGER NOT NULL DEFAULT 0,
  since TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY(node_id, indicator));
```

Written by the daemon at the end of each fast cycle, under the same lock as
every other write. Rows for a removed node go with the node (registry sync).

`pending_state`/`pending_count` are **persisted, not held in memory**: otherwise
a daemon restart resets every hysteresis counter and a flapping node snaps green
the moment the daemon is restarted. `since` records when the current state was
entered, so the UI can say "degraded for 4 hours" rather than just "degraded".

Schema version goes to 2. `store.connect()` already runs `CREATE TABLE IF NOT
EXISTS`, so an existing database gains the table on next start with no migration
step; nothing in P1 alters an existing column.

## 3. Configuration

Three new keys in `manager.cfg`, editable on the settings page:

```
capacity_high_water=90
temp_warn=50
temp_crit=60
error_window_min=15
```

Validated the way P0 validates the poll intervals: numeric, bounded, and
`temp_warn < temp_crit` or the save is refused with the reason.

## 4. API — one endpoint per screen

All three: session-gated, read-only `um_db()`, `PHP_SAPI` dispatch guard, no key
in any response, and a null database answers `{}` rather than fataling.

**`api/health.php`** → the Overview screen.
```jsonc
{ "fleet": {"nodes": 2, "ok": 1, "degraded": 1, "unknown": 0},
  "nodes": [{ "id": "...", "name": "Golem", "state": "degraded",
              "since": "2026-08-27T02:00:00Z",
              "indicators": {"capacity": {"state":"warn","value":93.1,
                                          "basis":"93% of 250 TB used"}, ... },
              "array_state": "STARTED", "capacity": {...}, "unraid": "7.3.2",
              "booted_at": "...", "last_seen": "..." }] }
```

**`api/disks.php`** → the Disks screen. Merges the slow-lane `disks` payload
(device, vendor, size, temp, smartStatus, interface) with `array.disks` (slot,
numErrors, fs usage), plus `assignableDisks` as fleet spares. Every row carries
its node and the `fetched_at` of the payload it came from, because that payload
may be a stale last-good after a 504. Never a serial.

**`api/drift.php`** → the Drift screen. Nodes as columns; rows are
`unraid`, `api`, `kernel`, `php`, `docker`, then one row per plugin name seen
anywhere in the fleet with present/absent per node. Each row carries
`divergent: bool` so the UI can collapse the identical ones by default.
Responds with `"plugin_versions_available": false` so the UI states the Tier 0
limit rather than rendering an empty column.

## 5. Frontend — `frontend/`, built to `source/.../ui/`

Vue 3 + Vite. `npm run build` emits hashed chunks and `manifest.json`.

`um_asset_tags(string $entry): string` in `common.php` reads that manifest and
returns the `<script type="module">` and `<link rel=stylesheet>` tags, resolving
hashes at runtime — copied from `WebComponentsExtractor`'s approach, which is
how Unraid solves this for its own bundle. No hash is ever written into a
`.page`, and cache-busting is inherent.

`UnraidManager.page` reduces to a mount point plus those tags. Three in-page
tabs (Overview, Disks, Drift) — no router library; the tab is component state.

- **Overview:** node cards. Hostname, status ring, array state, capacity bar
  with headroom, indicator chips, uptime from `booted_at`, version. Click opens
  a **Node Detail drawer** (per-domain status, `fetched_at`, errors) over the
  grid rather than navigating away. Escape closes.
- **Disks:** dense sortable table, filter chips by node and by `smartStatus`.
  Bottom panel lists fleet spares. A node whose `disks` payload is stale is
  labelled with its age, not hidden.
- **Drift:** matrix, nodes as columns, divergent cells highlighted, identical
  rows collapsed by default with a count.

**Theming.** Palette from Unraid's own CSS custom properties; `--um-*` exist
only as fallbacks. No hardcoded background anywhere. Every status carries icon +
word + colour — never colour alone. `unknown` is grey and visually distinct from
both healthy and failed.

**Live updates.** Reuse P0's nchan `EventSource` on `/sub/unraid-manager`: a
ping triggers a refetch through the authenticated API. The 30s fallback poll and
the 3-minute stale banner stay exactly as they are.

**Budget:** ≤ 250 KB gzipped, asserted in CI.

## 6. Build and CI

- `build.sh` runs the frontend build before packaging and **refuses with a clear
  message if node is absent**. Built output is not committed; `frontend/node_modules`
  and the emitted `ui/` are gitignored.
- CI gains a `frontend` job: `npm ci`, `npm run build`, assert the manifest
  exists and the gzipped bundle is under budget.
- The existing bytecode purge in `build.sh` stays.

**Accepted cost:** the single-file `curl` hot-patch that made P0's live trial
tractable no longer works for UI changes — a frontend fix is rebuild-and-reinstall.

## 7. Testing

- **Python:** `health.py` evaluators against fixture payloads including the
  empty-array and missing-temperature cases; `apply_hysteresis` table-driven
  across escalate/clear/reset-on-disagreement; the rate calculation with no
  previous sample, a flat counter, and a jump. Integration: a `Manager` cycle
  writes `node_health` and survives a restart with counters intact.
- **PHP:** one test file per endpoint — shape, null-db, no key in the encoded
  payload, no serial in the disks payload, and drift's `divergent` flags.
- **JS:** no test framework. The bundle is render-only and every decision it
  displays was made server-side where it is already tested. CI proves it builds.

## 8. Out of scope for P1

Storage, Links, Jobs and Alerts screens; the capacity treemap; the disk verdict
chain, confidence tiers and spares *recommendation*; plugin version drift; pool
redundancy warnings; any mutation against a peer; Tier 1 in any form; alert
suppression; the dry-run modal (nothing mutates yet).

## 9. Plan shape

Large enough that the implementation plan should run as two milestones with a
review checkpoint between: the daemon and API surface first (health engine,
schema, three endpoints, thresholds), then the frontend (toolchain, mount,
three screens, CI job). The first is independently verifiable — the endpoints
can be exercised on Raven before a single Vue component exists, which keeps the
live-trial loop short the way it was in P0.

## 10. Risks

| Risk | Reveal / mitigation |
| --- | --- |
| `node_health` is the first stored *opinion* rather than observed fact. A changed evaluator leaves stale rows until the next poll. | Rows carry `updated_at`; the daemon rewrites every indicator every fast cycle, so staleness is bounded by one interval. |
| Hysteresis makes the UI feel unresponsive — a real problem takes 60s to show. | Deliberate, and the raw values are always visible even when the indicator has not flipped. |
| Vue bundle drifts from the PHP that mounts it. | The manifest reader fails loudly if the entry is missing; CI builds the bundle every run. |
| Thresholds are global, not per-node. A box that runs hot legitimately will sit amber. | Accepted for P1; per-node overrides are a settings-page change, not a design change. |
| The 504 makes the Disks screen look broken. | Rows show the age of their payload, and the screen states that `disks` is a slow-lane domain that can time out on the peer. |
