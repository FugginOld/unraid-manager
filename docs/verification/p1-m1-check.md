# P1 milestone 1 live check — Raven and Golem, 2026-08-27

**Manager:** Raven, Unraid 7.3.2, API 4.37.3+d5058009, php-fpm 8.2
**Nodes:** Golem (23 array slots, 49 physical disks, 93.4% full), Raven (empty array)
**Package under test:** built on the box from `dev`, installed through the `.plg`
**Database:** existing P0 database at schema version **1**, backed up before the run

Milestone 1's surface — health engine, `node_health`, three read-only endpoints,
threshold editing — is verified against real hardware. Four defects were found,
three of them invisible to a suite that was green before and after.

---

## What was observed

| Step | Result |
| --- | --- |
| Migration 1 → 3 on a live database | `node_health` created; `nodes` 2, `samples` 58494→58601, `events` 26→28, `node_state` 18 all intact. Only derived tables were dropped, as designed. |
| `db` flag on all three endpoints | `true`. The `sqlite3`-not-PDO read layer works under php-fpm. |
| Health rows | Five indicators per node, both nodes, within two fast cycles. |
| Raven's empty array | `capacity` reads *"array is empty"* — not 0%, not a division by zero. |
| **Raven's missing temperature** | `thermal` = `unknown` while `overall` stays `ok`. The P0 inversion — one blind reading declaring a whole node unreachable — stays fixed. |
| Golem's capacity | `warn` at 93.4% against a 90% high-water mark; `overall` = `degraded`, basis `capacity`. |
| **Hysteresis, escalating** | With `temp_warn` lowered to 38 against a 46 C disk: first sample `thermal=ok, pending=watch, count=1`; second sample `thermal=watch`. Gated, exactly as specified. |
| **Hysteresis, clearing** | Threshold restored to 50: `pending=ok` counting 1, 2, … toward 5. Asymmetric as designed — escalate on 2, clear on 5. |
| `overall` basis with two culprits | `capacity, thermal`. The chip lives in `state`; `basis` names what is wrong. |
| Settings save | All seven keys written to `manager.cfg`; no operator-set threshold erased. Closes the Task 4 deferral. |
| Secrets | No API key and no disk serial in any of the three payloads. |

## Defects found and fixed

### 1. The Disks endpoint matched nothing on real hardware

`api/disks.php` joined the physical enumeration's `name` to `array.disks.device`.
On real hardware the physical `name` is a **model string** (`ST10000NM0226`) and
its `device` is a full path (`/dev/sda`), while `array.disks` reports a bare
kernel name (`sdj`). The join matched **0 of 72** rows: every physical disk got
`slot: null`, and all 23 array slots fell through as phantom "orphan" rows.

The fixture had written `'name' => 'sdc'`, a shape the daemon never produces, so
the check named *"the array slot is merged in by device"* had been asserting
against fiction since it was written. Thirteen of thirteen single-field mutations
were caught by that suite. Mutation testing proves a check *can* fail; it cannot
prove the check is asking about something real.

Fixed by reducing both sides to the bare kernel name before joining, exposing the
physical `name` as `model` — calling a model string `name` is what invited the
wrong join — and rebuilding the fixture from payloads captured off Golem.
Verified after: **23 of 49 slots joined, 0 orphans.**

### 2. Every setting was inert until a daemon restart

`manager.cfg` was read once at process start. `Manager.reload()` re-read only the
node registry, so both poll intervals and all four health thresholds were frozen
for the life of the process. Saving `temp_warn=38` left a 46 C node reading `ok`
because the evaluator still held the 50 it booted with — while the settings page
reported success and the file on flash was correct.

There is no worse shape for a settings bug: nothing anywhere says it did not take.

`reload()` now re-reads `manager.cfg` alongside `nodes.cfg` and pushes the
intervals into the scheduler. `db_path` is deliberately excluded — repointing the
database under a running daemon would mean reopening a connection every worker
already holds. An unreadable `manager.cfg` logs and leaves the settings alone
rather than taking the registry reload down with it.

### 3. The Drift matrix was two rows short of its own spec

The design spec calls for `unraid, api, kernel, php, docker`; three shipped. The
plugin's collector only requested `versions { core { … } }`.

Rather than guess the field names — guessing one is what made the API answer an
entire query with `INTERNAL_SERVER_ERROR` in P0 — the schema was introspected on
the box: `InfoVersions.packages: PackageVersions`, carrying
`openssl node npm pm2 git nginx php docker`. Only the two the spec asks for are
requested. `pm2` returned an **empty string** on real hardware, so an empty
version is normalised to `None` and reads as *not reported* rather than as a
value the whole fleet agrees on.

### 4. Two unpinned row lists

Dropping `php`/`docker` from `UM_DRIFT_VERSIONS` left both suites green — the row
list itself was unasserted. Pinned, with the fixture now reporting a differing
`docker` so the divergence path is exercised and not merely the presence one.

## Platform facts confirmed

- **GraphQL is HTTPS, not HTTP.** A plain-HTTP probe gets nginx's
  *"The plain HTTP request was sent to HTTPS port"*. The daemon already gets this
  right; recorded because the first diagnostic run did not.
- **Introspection is enabled** on the Unraid API with a read-scoped key.
- Physical `Disk.name` is a model, `Disk.device` is a full path,
  `ArrayDisk.device` is a bare kernel name. These three facts are the reason
  defect 1 existed and the reason its fixture is now a capture.

## Still open

- **`plugin remove` deletes `/boot/config/plugins/<name>.plg`.** Writing the plg
  before the remove means the install has no file to read. A HOWTO ordering note,
  not a code defect.
- ~~**Restart survival of `pending_count`** was not exercised~~ — **CLOSED
  2026-08-30 on Raven, driving Golem.** `scripts/check_pending_restart.py`,
  verbatim: `count=2` at `18:31:12` while mid-clear, the same `count=2` read out
  of the database with `rc stop` done and nothing running, then `count=4` on the
  first poll after `rc start` — it continued rather than restarting at 1, and did
  not skip the remaining samples to reach `ok`.

  Two things the box corrected about the check itself, both worth keeping:

  1. **An outage cannot drive this ladder.** Stopping a node's API makes every
     indicator propose `unknown`, and `apply_hysteresis` returns immediately on
     `unknown` carrying the count unchanged — so the obvious lever produces
     `pending_count=0` and proves nothing. A threshold change does drive it.
  2. **A high threshold has to be a PAIR.** Writing `temp_warn=99 temp_crit=99`
     left the evaluator using 50/60: `read_manager_cfg` refuses `temp_crit <=
     temp_warn` and restores its own constants, which sit *below* Golem's
     hottest disk. Two runs timed out at `count=0` against a guard doing its
     job. 90/95 works. The `basis` string is what says which — it names the
     threshold actually used, and reading it is the difference between "the
     ladder is broken" and "the config never took our number".
- ~~**`disks.stale` was never non-empty.**~~ — **CLOSED 2026-08-30 on Raven.**
  `scripts/check_disks_stale.py` enrols one throwaway node at `127.0.0.1:1` and
  reads the list back through `um_fleet_disks()` itself. Both branches render:
  never-polled gives `no disks poll recorded yet` with `fetched_at: null`, and
  the failed first poll gives `URLError: <urlopen error [Errno 111] Connection
  refused>` with the payload still NULL. The probe left the list on removal.

  **The 504 path proved itself in the same output, unprompted.** Both real nodes
  were already in `stale` when the probe arrived:

  ```
  Golem  status unknown  fetched_at 2026-08-30T18:41:33Z
         HTTP 504 Gateway Time-out from nginx - the query took longer than the server allows
  Raven  status unknown  fetched_at 2026-08-30T18:41:23Z   (same error)
  ```

  A read of `node_state` at 18:52 had shown both `disks` rows `ok` with payloads
  of 5405 and 2884 bytes; the ~18:51 slow poll 504'd, and `upsert_state` kept the
  18:41 payload and its `fetched_at` while status went `unknown`. That is the
  retention contract behaving exactly as api/disks.php's header describes, on
  live data, with no lever at all — and it is the answer to "neither path was
  exercised": both were, within five minutes of each other.

  Two facts fall out of it, neither of them about this milestone:

  - **A 504 reads `unknown`, not `error`** — worth pairing with the platform fact
    that a stopped API reads `error`. Neither of the two commonest disk-lane
    failures is the status a reader would guess.
  - **The `Query.disks` 504 is no longer Raven-specific.** tier0-coverage.md
    finding 2 records Golem answering in 15.4s; Golem now 504s too. Amended
    there.

## What this milestone demonstrated

Two of the four defects were in code that a green suite had covered — one of them
covered by a check that had *never been able to fail* because its fixture
described a payload the system does not produce. The suite's value is real: every
fix here landed with a test that would catch its return, and each was mutation-
proven against the committed baseline. But the shape of the data is not something
an off-box suite can know, and this is the second milestone in a row where the
most expensive defect was a fixture that lied.
