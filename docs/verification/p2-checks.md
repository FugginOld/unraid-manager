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

## 2026-08-30 — P2-5, sort/filter on the fleet card grid (`2a09b1e`)

Ordering was `health.php`'s `ORDER BY name` and nothing else. Closed by making
the worst state lead unconditionally, with the summary counts as the state
filter and one search box.

Deployed as a full rebuild rather than a file patch: the bundle under `ui/` is
gitignored and built at package time, so a UI change cannot be curled onto the
box the way `managerd.py` and `health.php` were. HOWTO's remove-then-write-plg
order applies.

Confirmed on the box by observation (Joe, 19:57) — the grid ordered Golem ahead
of Raven with no control touched, the count buttons isolated and cleared, the
zero count was not pressable, state and search together produced the "No node
matches this filter" hint rather than "No nodes enrolled", and the escape hatch
restored the grid and emptied the search. Two nodes cannot show off the ordering;
the checks that would actually break at that size are the filter combination and
the empty-result hint, and both are visible at two.

Off the box: 19 unit checks in `tests/js/fleet.mjs`, 15 through real clicks in
`tests/js/interact.mjs`, and 5/5 mutants killed — rank order, an unknown state
sorting first, filter OR-instead-of-AND, an in-place sort, and a case-sensitive
search.

`interact.mjs` needed `Document` and `ShadowRoot` added to its happy-dom globals:
the search box is the first `v-model` in a tested view, and `vModelText`'s
`beforeUpdate` reads them at every patch. Missing, it surfaces as a bare
`ReferenceError` from inside Vue and reads as a broken component.

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

## 2026-08-30 — P2a preconditions, answered on the box

The three assumptions the Tier 1 agent spec rests on
(`docs/superpowers/specs/2026-08-30-tier1-agent-design.md`), checked on Raven and
Golem before a line of code was written. All three held; the check also caught a
defect in the implementation plan that no off-box test would have found.

**1. `authorized_keys` persistence — HELD, and simpler than the design feared.**

```
-rw------- 1 root root 746 Jan 16  2026 /root/.ssh/authorized_keys
readlink -f -> /boot/config/ssh/root/authorized_keys
```

The conventional path is a symlink onto flash, so an append at `/root/.ssh/`
writes through and survives a reboot with no plugin, no boot hook, and nothing in
tmpfs. The peer footprint decision stands as designed.

**It is not empty.** 746 bytes of the operator's own keys were already there. The
installer appends; an installer that wrote the file would lock its owner out of
their own box.

**2. `smartctl --json` — HELD.** smartctl 7.5 on both boxes, `--json=c` returns a
valid document. The agent passes it through untouched and the manager parses it,
as designed — no text scraping needed.

**3. Pools — ANSWERED.** `btrfs filesystem usage <mount>` works per mount point;
Golem's `zpool status` reports its pool; Raven answers `no pools available`, which
is a parseable statement rather than a failure. A pool parser can be written
honestly, so `pool.balance`'s manager-side domain no longer has to stay deferred
on this question.

### The defect this caught

`smartctl --json` includes `"serial_number"` and `"logical_unit_id"`. The plan had
`parse_smart` storing the whole document in `node_state.payload`, which
`api/disks.php` serves to the browser — against this repo's standing rule that no
raw serial reaches an API response (plan §199, §443), the same rule for which
`collector.py:328` already drops `serialNum` from the Tier 0 disk row.

Two things follow, both settled before Task 1: `parse_smart` strips both fields
before returning, with a test asserting neither can reach a payload; and the
captured fixture is scrubbed at capture, since a real serial committed to a public
history cannot be taken back.

### An operational finding, unrelated to this work

Golem's ZFS pool `medianucbackup` is a **two-device stripe, not a mirror** —
`sdy1` and `sdz1` sit as sibling vdevs with no `mirror` or `raidz` parent. It is
ONLINE and scrubbed clean, so nothing is wrong today, but a pool named for backups
has no redundancy: either disk failing loses all of it. Recorded here because it
is exactly the standing finding M4 is meant to surface, and because this check saw
it first.

## 2026-08-31 — P2a, the Tier 1 agent, verified end to end (`a2d469a`)

Golem enrolled as a Tier 1 peer from Raven. **No plugin on Golem** — one script on
flash and one `authorized_keys` line, which is the property the whole phase was
arranged to protect.

```
{"ok":true,"version":"2026.08.30",
 "verbs":["agent.hello","mounts.list","pool.balance","smart.attributes"]}
```

| Exit criterion | Result |
|---|---|
| Host key recorded, fingerprint matched the peer's own | ✅ `SHA256:J1qlDF6/…LubAk` identical on both sides |
| Forced command fires | ✅ settles the `-N` ruling on real sshd |
| Unknown verb refused, nothing run | ✅ `UNKNOWN_VERB` for `rm.everything` |
| Bad device refused by enumeration | ✅ `BAD_ARGS: not a device on this node: /dev/nope` |
| Real SMART data over the agent | ✅ full `smartctl --json` for `/dev/sda` |
| Survives a peer reboot | ⬜ not yet exercised — see below |

### Two design decisions the box overturned

**1. Nothing on `/boot` can ever be executed.** Live from Golem:

```
/dev/sdaa1 /boot vfat rw,noatime,nodiratime,fmask=0177,dmask=0077,...
-rw------- 1 root root 7356 /boot/config/plugins/unraid-manager/agent-exec
/boot/.../agent-exec   ->  Permission denied, exit=126
python3 /boot/.../agent-exec  ->  {"ok": true, ...} exit=0
```

`fmask=0177` forces every file on flash to `0600`. The execute bit is not unset,
it is **unsettable** — the installer's `chmod 700` was a silent no-op that appeared
to succeed. Fixed by naming the interpreter in the forced command
(`/usr/bin/python3 <script>`), so the script is an argument rather than the
executable. Reading a file on that mount was never blocked; only `execve` is. The
flash-resident design survives with no boot hook and still no plugin on the peer.
The `chmod` was **deleted** rather than left as decoration: a chmod that appears to
work and changes nothing is worse than none.

**2. `known_hosts` was read but never written.** `agentclient` passes
`StrictHostKeyChecking=yes` and `UserKnownHostsFile=…`, and nothing created that
file. First live call: `ssh exited 255: No ED25519 host key is known for
192.168.2.248 and you have requested strict checking.` The spec said "written at
enrollment"; the task brief dropped it. Enrollment now scans the host key, strips
any stale entry for that address before appending, and returns the fingerprint so
the operator can compare it against the one the installer printed on the peer.

Neither was reachable by any test. Every test injects `run_fn` and never touches
ssh — the tradeoff that keeps the suite runnable on Windows with no ssh binary.

### `/root/.ssh` is the symlink, not `authorized_keys`

```
lrwxrwxrwx /root/.ssh -> /boot/config/ssh/root/
stat -c '%i' both paths -> 3302607, 3302607
```

The **directory** points at flash, so everything written into it persists by
construction. Worth stating precisely: an earlier note in this file called the
*file* a symlink, which made `sed -i` look dangerous when it never was here.
It also means Golem had **no root key at all** before this — its
`authorized_keys` was a single blank line, and the 746 bytes recorded on
2026-08-30 was Raven's file, not Golem's.

### Still open

The reboot test. `/root/.ssh` resolving onto flash makes persistence structural
rather than hopeful, but this phase has twice been wrong about something that
looked obvious — `-N` and `chmod 700` both — so it is recorded as **unverified**,
not as proven by inference.
