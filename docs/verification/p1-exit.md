# P1 exit — the pane on Raven and Golem

Operator-assisted trial, 2026-08-28, Raven (manager) + Golem (peer).
Head at start `ab77584`, release `2026.08.28`. Two defects were fixed during
the trial; head at end `5296e86`.

## Verdict

**Not yet.** The pane does replace opening two browser tabs for everything it
shows — one screen carries both boxes' health, disks and drift, it follows the
theme, and it now updates live. What it cannot yet do is tell you when to stop
believing it: with `managerd` stopped, the pane goes on displaying its last
numbers with no warning of any kind, indefinitely. One open P1 defect
(**F-1**) blocks the phase; everything else found is fixed or triaged.

## Per step

| # | Step | Result |
|---|------|--------|
| 1 | Build and install | pass, after three defects in the runbook itself (**D-1..D-3**) |
| 2 | Pane loads | pass — three tabs, card grid, manifest present |
| 3 | Overview | pass |
| 4 | Drawer | pass — per-domain state with `fetched_at`, fleet visible behind |
| 5 | Disks | pass |
| 5a | Null-model rows | **0 of 49 disks** — the orphan inference is not exercised here |
| 6 | Drift | pass (static); the live plugin-change check was not run |
| 7 | Hysteresis | **not run** |
| 8a | Live updates | **fail, then fixed** — see F-2 and F-3; now working |
| 8b | Stale banner | **fail — F-1, open** |
| 8c/8d | Banner across tabs, banner clears | **blocked by F-1** — no banner ever appeared |
| 9 | Themes | pass — white, black, azure, gray |
| 10 | Bundle | pass — 31,206 bytes gzipped against a 256,000 budget |

### 3. Overview, verbatim

`2 node(s): 1 ok, 1 degraded, 0 unknown.`

- **Golem** — `Degraded`, `STARTED · Unraid 7.3.2 / API 4.37.3+d5058009 · up 8d`,
  capacity bar `93% · 233 TB of 250 TB`, `5 alert · 3 warn · 0 info`.
  Indicators: `OK array state`, `Warning capacity — 93.4% used, high-water mark
  is 90%`, `OK thermal — hottest disk 47 C`, `OK disk errors — no new disk
  errors in the window`. `degraded for 17h`.
- **Raven** — `OK`, `empty array` (**not** `0%`), `0 alert · 1 warn · 4 info`,
  `OK capacity — array is empty`, `? Unknown thermal — no disk temperature
  reported` **while the card's own head chip stayed `OK`**.

Every rendering invariant the Task 13/14 amendments were written for held on
live data.

### 5. Disks, verbatim

49 disks, 11 spares across both nodes. The Model column is populated
throughout (`H0H72121CLAR12T0`, `HUH721010AL4200`, `SPCC Solid State Disk`) —
a blank column here would have been the 0-of-72 join failure returning, and it
was the one observation designated an unarguable P1 defect in advance.

Null-vs-zero holds on real hardware: assigned disks render `0` errors, while
the unassigned ones (`/dev/sdg`, `/dev/sdy` on Golem; all of Raven's) render a
dotted grey `—` for slot, array and errors. Real error counts render in the
warning colour (192 on Golem `disk15`, 64 on `disk20`).

### 6. Drift, verbatim

`31 identical row(s) hidden.` Both words render — `present` and `absent` — with
no blank cells and no `—`, so both nodes had reported their plugin lists.

## Findings

| # | What | Severity | State |
|---|------|----------|-------|
| F-1 | The stale banner cannot fire while php-fpm is up | **P1** | fixed `a485caf`, awaiting live re-check |
| F-2 | Every nchan publish was refused `403` | P1 | fixed, `5b10e55` |
| F-3 | Nudges fired only on a status flip, so never on a healthy fleet | P1 | fixed, `5296e86` |
| F-4 | Thermal is blind on a box whose disks are unassigned | P1 or P2 | **open, awaiting ruling** |
| F-5 | Every spare is listed twice | P2 | **open** |
| F-6 | Card says `OK disk errors` while the table shows 192 | P2 | **open, awaiting ruling** |
| D-1 | `plugin remove` deletes the `.plg`, so the documented order loses it | doc | fixed `HOWTO.md` + both runbooks |
| D-2 | The runbook's `rc` path (`/etc/rc.d/…`) does not exist | doc | fixed in the runbook |
| D-3 | Building on the box silently depends on node/npm being present | doc | documented in `HOWTO.md` |

### F-1 — the stale banner measures the wrong clock (P1, fixed `a485caf`)

Stopping `managerd` for three minutes produced no banner at all. The cards kept
showing their numbers, and nothing on screen said they had stopped moving.

`api/health.php` reads only the database; it never contacts the daemon. So with
`managerd` down the endpoint still returns 200 with the last rows written, the
pane's `refresh()` resolves, and `live.js` stamps `lastGood = Date.now()`.
`stale` is `Date.now() - lastGood > 180000`, so the banner can only fire when
nginx or php-fpm is down — the case where the operator already knows.

The banner says *"the manager has not answered"*. What it measures is *"the web
server answered"*.

Inherited, not a P1 regression: P0's `fleet.js` (`b90fa23`) had the identical
`lastGood = Date.now()` on fetch success, under a comment stating the intent it
never achieved — *"rather than showing stale numbers as though they were
current."* It passed P0's exit trial too, because nobody stopped the daemon and
waited.

Fixed: `health.php` now reports `newest` (the freshest `last_seen` in the
fleet) and `age` (its age in seconds), computed against the server's own clock
so a skewed browser clock cannot banner a healthy fleet or hide a dead daemon.
Never collected is `null`, never `0`, and an unparseable timestamp is `null`
too. `App.vue` banners on either clock with a different sentence for each.

Nothing rendered `App.vue` in any harness, which is how this survived two
phases; `tests/js/views.mjs` now SSR-renders it and asserts the exact Raven
case. **Awaiting live re-verification** — steps 8b-8d.

### F-2 — every nchan publish was refused (P1, fixed `5b10e55`)

```
POST /pub/unraid-manager  ->  403 missing nchan_message_buffer_length value
POST /pub/unraid-manager?buffer_length=1  ->  201 Created, active subscribers: 1
```

Unraid's publisher location is `nchan_message_buffer_length $arg_buffer_length`,
so the buffer length comes from the query string and a POST without one is
refused. `publish()` did `sock.recv(256)` and discarded the reply, so a refusal
was indistinguishable from an acceptance; the daemon logged `nchan publisher at
/var/run/nginx.socket` at every startup, which reports *discovery* and was read
as health. Live updates had never worked, in two phases, and the browser's 30s
fallback timer carried the feature — which is why nothing ever looked broken.

Fixed: the path carries `buffer_length=1`, and a non-2xx, empty or unparseable
reply now raises.

### F-3 — the nudge only fired on failure (P1, fixed `5296e86`)

With F-2 fixed, nginx's `total published messages` still did not move across a
forced poll: `758162` before and after. `_publish` fired `if changed`, and
`changed` holds status transitions (`ok` → `error` → `unknown`). On a healthy
fleet nothing ever transitions, so the daemon nudged essentially never.

The test that locked this in, `test_an_unchanged_cycle_publishes_nothing`,
asserted the broken behaviour and passed.

Fixed: a cycle that stored anything nudges; a node already failing and still
failing stays quiet. **Verified live on Raven** — the counter advances and the
pane updates within a second or two of each poll.

### F-4 — thermal is blind where disks are unassigned (open, awaiting ruling)

Raven's Disks tab shows 11 disks at 33–40 °C. Raven's card reads `? Unknown
thermal — no disk temperature reported`. `collector.py:127` computes `temp_max`
from array-assigned disks and parities only, and `health.py:78` returns
`UNKNOWN` for `None`. Raven's array is empty, so every disk it has is
unassigned and contributes nothing — the box has no thermal monitoring while
the pane displays eleven temperatures one tab over.

Recommended P1: a disk overheating in that box reads as "unknown", not "warn".
The fix belongs in the health engine, not the pane.

### F-5 — spares are listed twice (P2, open)

`spares` is a subset of `disks`, so every unassigned disk appears once in the
main table with `—` for slot/array/errors and again under Spares. Raven's 11
rows include the 9 repeated below. The fix is a decision — exclude unassigned
disks from the top table, or drop the Spares table — not a bug.

### D-1..D-3 — the runbook's own defects

All three were mine, and all three cost time at the keyboard:

- **D-1** `plugin remove` deletes `/boot/config/plugins/unraid-manager.plg`.
  Both `HOWTO.md` and the Task 10 and 16 runbooks write the `.plg` **before**
  the remove, so the install is handed a file that no longer exists and fails
  with `XML file doesn't exist or xml parse error`. It also leaves the box with
  no plugin installed until it is fixed. Write the `.plg` **after** the remove.
- **D-2** The runbook's `/etc/rc.d/rc.unraid-manager` does not exist; the script
  lives at `/usr/local/emhttp/plugins/unraid-manager/scripts/rc.unraid-manager`.
  `HOWTO.md` has this right.
- **D-3** `build.sh` hard-fails without npm. Raven happens to carry node and npm
  at `/usr/local/bin`, so building on the box works *there*; a stock Unraid box
  has neither. HOWTO should say so rather than leaving it to luck. (My first
  diagnosis of the failed install blamed exactly this and was wrong — the build
  had succeeded; D-1 was the cause.)

## Not covered

- **Step 7, hysteresis** — not run. `temp_warn` was never lowered, so the
  two-cycle escalation and five-cycle clear remain unverified on hardware.
- **Step 6's live plugin change** — the matrix was read, but no plugin was
  installed or removed to watch a row appear and be flagged divergent.
- **Steps 8c and 8d** — blocked by F-1; with no banner there is nothing to
  follow across tabs or to watch clear.
- **The orphan row** — 0 of 49 disks have a null model, so the "no disk present"
  rendering was never exercised against real hardware. It is proven only in
  `tests/js/views.mjs`.
- **Sorting and the filters** — clicked casually, not systematically tested; no
  automated coverage exists for them either.
- **Golem as the manager** — the whole trial ran with Raven as the manager.
