# P0 exit verification — Raven and Golem, 2026-08-26

**Manager:** Raven, `192.168.2.19`, Unraid 7.3.2, API 4.37.3+d5058009, php-fpm 8.2
**Nodes enrolled:** Golem (`192.168.2.248:15137`), Raven itself (`192.168.2.19:29220`)
**Package under test:** built from `b0e6155`, installed through the `.plg`
**Operator:** supplied both API keys at run time. No key appears in this repo, in
any log, in any response, or in the session transcript.

P0's exit criterion — *two real nodes enrolled and visible with live state* — is
met. Both boxes render on the Fleet tab with array state, capacity, versions,
notification counts and last-seen, refreshed live.

---

## What was observed

| Step | Result |
| --- | --- |
| 1. Install via `.plg` | Plugin tree, `manager.cfg`, `nodes.cfg`, `keys/` all present. Daemon auto-started by `event/started`. |
| 2. Flash guard | `db_path=/boot/...` refused with the flash-wear message, exit 1, **no daemon started**, `status` exit 3. Also fired on first install with `db_path` unset. |
| 3. Real db_path | Saved from the Settings page. `manager.db` + `-wal` + `-shm` on `/mnt/cache`, `managerd.sock` at `srw-------`. |
| 4. Probe + enroll Golem | Verdict `ok`, all six fast domains green. Key written `0600` under a uuid name; no key in `nodes.cfg`; **zero** occurrences of the key in the log. |
| 5. Probe + enroll Raven | Verdict `ok`, array `STARTED` and flagged **empty** — constraint 3 confirmed against the real thing. |
| 6. Fleet tab | Both nodes live. Golem 93% · 233 TB of 250 TB; Raven's capacity cell reads **"empty array"**, not 0% and not blank. |
| 7. Slow lane | Golem `disks` returned a real **504** (see below). One domain marked; the node stayed readable on the other eight. |
| 8. Deliberate failure | Golem pointed at a closed port → grey **`? Unknown`**, never green, never red. `last_seen` froze while Raven's advanced. **Exactly one** `poll_fail` row, not one per cycle. Restored → back to `ok`, one `poll_ok` row. |
| 9. Live updates | nchan publisher found at `/var/run/nginx.socket` after a fix; the page no longer falls back to polling. |
| 10. Restart survival | `rc restart` completes in ~1.0s after a fix. Uninstall/reinstall cycle preserved `manager.cfg`, `nodes.cfg` and both keys, and both nodes returned automatically. |

## Platform faults confirmed, not ours to fix

**`Query.disks` 504s at nginx's 60s gateway timeout — on either box.** Documented
on 2026-08-25 as Raven-specific; tonight it was **Golem** that failed, having
enumerated 37 disks in 15.4s the day before. It is load- and state-dependent, not
a property of one machine. Our 90s slow-lane timeout is not the ceiling and
raising it would change nothing — nginx cuts first. The designed outcome held:
the domain is marked, its last-good `fetched_at` is preserved rather than nulled,
the message is legible (*"the query took longer than the server allows"*), and
the node stays readable. First exercise of that classification path against a
real 504 rather than a captured page.

**`ParityCheck.errors` overflows the API's own `Int`.** Golem's history row 46
reads `2441379360`; requesting that field makes the API answer the entire query
with `INTERNAL_SERVER_ERROR`. `parityHistory` takes no arguments, so the newest
row cannot be asked for alone. The collector no longer selects the field. This
is what gave constraint 1 its live proof: one domain errored, five stayed green,
the node stayed enrollable, and the report said why.

## Defects found and fixed

Fourteen, every one with a regression test. Eleven were **platform-integration**
faults invisible to an off-box suite — code that assumed emhttp's dispatcher
context, or a PHP extension the CLI has and php-fpm does not.

| Commit | Defect |
| --- | --- |
| `d991abe` | A guarded no-op returned 1, so the `.plg` aborted every fresh install before unpacking a file. |
| `0828786` | Nothing started the session; every endpoint 401'd regardless of login. |
| `8be1bf2` | The server's CSRF token was read from emhttp's `$var`, which a standalone endpoint never has. |
| `420dc1a` | Unraid validates CSRF in `local_prepend.php` and **unsets the field**; re-checking a consumed token refused every write. |
| `0e37935` | That gate was credited on any method, though the platform only validates POST. |
| `faea89f` | `sqlite3` refuses cross-thread use by default — the control socket and every worker poll would have raised. |
| `9f063ce` | Umask 000 left the pidfile world-writable, and the rc script feeds it to `kill` as root. |
| `055fe02` | Asked for a field the API cannot serialise. |
| `f0b3e7b` | "Taken from the node if left blank" used the address; Golem enrolled as an IP. |
| `cd76aa1` | **php-fpm has no `pdo_sqlite`.** Every PHP read failed and returned empty while the daemon polled happily. The CLI *does* have it, which is why CI was green. |
| `1694238` | A top-level tab needs `Code=`; without it the Fleet tab silently never rendered. |
| `b988c8a` | The nchan regex required `;` immediately after the socket path; the real line ends at a space. |
| `b0e6155` | Shutdown waited on in-flight polls, so `rc stop` failed at 10s and array stop would have stalled. |
| `2800c93` | The rollup let one unreadable domain declare a whole node unreachable. |

The last one was a contradiction inside the plan: Task 18 specified "any domain
`unknown` → node `unknown`", while Task 22 step 7 calls exactly that a defect.
Live, Golem answered nine domains with a current `last_seen` and rendered as
unreachable. `unknown` now means nothing about the node is readable; anything in
between is `degraded`. Still fail-closed — never `ok` while a domain is blind.

## Open, and deliberately not fixed in P0

- **The tab is labelled "UnraidManager"**, from the filename. `Title="Fleet"`
  only sets the page heading; the top-bar label comes from `Name=`. Accepted by
  the operator; one line to change if wanted.
- **`Code="f0e8"` is a guess** at Unraid's subsetted icon font. It renders; if it
  ever shows an empty box, swap for a codepoint observed on the box.
- **The 504 root cause on either box.** A box-side investigation, not a plugin
  change.
- **`SLOW_TIMEOUT = 90`** stays, though nginx's 60s is the real ceiling. Harmless,
  and correct if the endpoint is ever reached without nginx in front.

## What this milestone actually demonstrated

Every defect above was invisible to 210 Python tests and 9 PHP suites, all green
before the trial and all green after. Nine of the fourteen could not have been
caught anywhere but on a live box: they are facts about emhttp's dispatcher,
php-fpm's extension set, Unraid's CSRF prepend, its nginx layout and its menu
loader. The suite's job was to make each one a one-line fix once found, and it
did — every fix here landed with a test that would catch its return.
