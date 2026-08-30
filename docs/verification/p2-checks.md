# P2 hardware checks

Live verification of P2-era changes, in the order the box saw them. The P1 files
are closed milestones; this is where a P2 fix records what the hardware said.

**Box:** Raven, manager, with Golem enrolled as a peer.
**Standing rule:** verified as each change landed, never in a batch
(CONTRIBUTING.md). Every entry below names the observation that separates the
fixed build from the one before it — a check that passes on both proves nothing.

## 2026-08-30 — `capacity_watch` was inert until a daemon restart (`8b74600`)

`RELOADABLE` listed four of the five thresholds `health.evaluate()` reads.
Saving `capacity_watch` on the settings page produced a success message, a
correct flash file, and an evaluator still holding the boot value.

**The discriminator is the state, not the basis.** `evaluate_capacity`'s WATCH
message names the high-water mark and never the watch level, so the string reads
identically whichever value the evaluator holds. `scripts/check_capacity_watch_reload.py`
pins `capacity_high_water` above the node's real usage so it can never be the one
deciding, then moves `capacity_watch` across that usage with nothing but a reload
in between. Golem, verbatim:

```
start        state=warn   value=93.2   93.2% used, high-water mark is 90%
high_water=99, capacity_watch=95  ->  state=ok     value=93.2
capacity_watch=93 ONLY, reload ONLY -> state=watch  value=93.2
```

Same percentage throughout, one key changed, no restart. The previous build
stays `ok` at the last step because it never picks the key up.

Fixed by deriving `RELOADABLE` from `HEALTH_THRESHOLDS` rather than restating it.
A second hand-maintained copy of one list is how the bug happened, and the test
asserts the set relation rather than naming `capacity_watch` — a test naming the
key goes green on the fix and says nothing about the next key added to one list
and not the other.

## 2026-08-30 — the stale threshold was fixed while the poll interval is not (`6333ab5`)

`UM_STALE_AFTER = 180` was a constant while `poll_fast` is an operator setting
bounded at `UM_POLL_MAX`, 86400. A fleet polled hourly is always past the
threshold: every healthy node greys permanently, and the banner reports a manager
that has stopped answering while it answers exactly as configured. Nothing in the
daemon is wrong in that state, which is what would have made it hard to read from
the pane.

Now `max(180, 3 * poll_fast)`. Three intervals matches the daemon's own two
staleness rules (`UNKNOWN_AFTER` and `INVENTORY_STALE_AFTER` are both 3); the
floor stops the other end being wrong, since at a 5s poll three intervals would
call a node stale after 15 seconds and one slow answer would grey a healthy
fleet. Verified against the deployed `health.php`, verbatim:

```
poll_fast=30   -> stale_after=180    endpoint=180    ok@400s=unknown
poll_fast=600  -> stale_after=1800   endpoint=1800   ok@400s=ok
poll_fast=5    -> stale_after=180    endpoint=180    ok@400s=unknown
```

`endpoint` is what `um_fleet_health()` actually ships, checked against the
function so the two cannot drift. `ok@400s` is the bug itself: a 400-second-old
verdict is one poll old at a 600s interval and four minutes stale at 30s, and the
same deployed code now says so both ways. The previous build ships a flat `180`
on every row.

No frontend change was needed — `App.vue` and `NodeCard.vue` already consume the
shipped `stale_after` rather than a copy, which is what that design was for.

## Notes for whoever runs the next one

- **An API outage cannot drive the hysteresis ladder.** Every indicator proposes
  `unknown`, and `apply_hysteresis` returns immediately on `unknown` carrying the
  count unchanged. Drive it with a threshold change instead.
- **A forced threshold must be a valid PAIR.** `read_manager_cfg` refuses
  `temp_crit <= temp_warn` and `capacity_watch >= capacity_high_water`, restoring
  its own constants — which sit in the middle of the range and will quietly
  produce the verdict you were trying to move away from.
- **`bash tests/php/run.sh` in the php docker image silently skips
  `tests/js/*.mjs`** ("node not on PATH"), so the documented local workflow never
  runs them. `node tests/js/*.mjs` on the host does, and CI runs them too.
