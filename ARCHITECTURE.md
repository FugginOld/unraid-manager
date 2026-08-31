# Architecture

What Unraid-Manager is made of and why it is shaped this way. The product
rationale lives in [unraid-manager-plan.md](unraid-manager-plan.md); this file
describes what exists.

## The shape

One Unraid host is the **manager**. It polls every other Unraid box over the
official GraphQL API and renders the fleet in its own webGUI. Peers need no
plugin installed — a read-scoped API key is the whole integration.

```
 browser ──► .page files ──► api/*.php ──┬──► SQLite on a pool   (read-only)
                                         └──► unix socket ──► managerd
                                                                  │
                                          HTTPS + x-api-key       ▼
                                    ┌───────────────────── peer /graphql
                                    │
 managerd ──► SQLite on a pool  (the only writer)
          └─► nchan publish (a nudge, never data)
```

Three processes, three jobs:

- **`managerd`** (Python 3.11, stdlib only) owns every database write. It
  schedules polls, classifies failures, normalises payloads, and pings nchan
  when something changes.
- **`api/*.php`** is a thin request layer. It reads the database **read-only**,
  writes flash config, and talks to `managerd` over a unix socket. No business
  logic, and it never writes the database.
- **`.page` files** are the UI shell. Plain PHP and plain JS in P0.

## Why the daemon owns the writes

SQLite tolerates one writer. Giving php-fpm a second write path would mean
lock contention between a request handler and a poll loop for no benefit — the
page has nothing to write that the daemon does not already own. So the page
layer opens the database with `PRAGMA query_only = 1` and proves it in a test by
attempting an INSERT.

Everything a page *does* need to change goes one of two ways: flash config
(written atomically, only on user action) or a command on the control socket.

## Where state lives, and what must never move

| Data | Location | Rule |
| --- | --- | --- |
| Node registry, API keys, settings | `/boot/config/plugins/unraid-manager/` | Flash. Written **only on user change**. Keys are one file per node at `0600`, never in the registry, never in the database. |
| Telemetry, events, samples | `<pool>/appdata/…/manager.db` | Pool, WAL. Operator-configured and mandatory. |
| Socket, pidfile | `/var/run/unraid-manager/` | Volatile. Socket `0600`. |
| Log | `/var/log/unraid-manager/` | Volatile, rotated 1 MB × 2 — rootfs is tmpfs, so an unbounded log is RAM. |

**Telemetry never touches flash.** Flash is a USB stick with finite write
endurance and telemetry is continuous. `store.validate_db_path()` refuses a
`/boot` path, the rc script refuses it again before launching anything, and the
settings endpoint refuses it before saving. Three layers because the failure is
silent and takes months to show up.

## The collector

One domain is one HTTP request. Always. A batched GraphQL query containing a
resolver the box cannot satisfy returns `data: null` for **everything** — so a
domain failing has to cost that domain and nothing else.

Domains split across two lanes:

- **fast** (30s default, 10s timeout): `info array shares notifications metrics parity`
- **slow** (600s default, 90s timeout): `disks plugins logfiles`

`disks` is slow because it is: 15s on a healthy box, and reproducibly past
nginx's 60s gateway timeout on a loaded one. It must never sit in the hot path.

### Three failure classes, deliberately distinct

| Class | Meaning | Node reads as |
| --- | --- | --- |
| `TransportError` / `AuthError` | We could not get an answer | `unknown` |
| `DomainError` | The box answered and the resolver failed | `error` |
| — | We got an answer | `ok` |

The distinction is the whole point of `gqlclient.parse_response()` being a pure
function over bytes: every failure shape observed on real hardware — a 504 HTML
page, a key without scope answering 200, `data: null` with errors — is a unit
test against a captured fixture rather than something only a live box can show.

**Fail closed.** Unreadable is `unknown`, never `ok`, and `unknown` is visually
distinct from both healthy and failed all the way to the browser. A node that
has never been polled is `unknown`, not green.

At node level, `unknown` means *nothing about this node is readable*. One blind
domain among readable ones is `degraded` — see the correction in
[docs/verification/p0-exit.md](docs/verification/p0-exit.md).

**One clock owns `unknown`.** Nothing readable is necessary but not sufficient:
it must also persist past the scheduler's `UNKNOWN_AFTER`. Below that a node
reads `degraded` — we tried, and nothing answered — because a transient that
greys a card and un-greys it inside a minute teaches an operator to stop
believing the colour. The trigger is *nothing answered*, never the spelling of
the failure: **a node whose API is stopped does not refuse the connection.**
Something still replies over HTTP with a GraphQL `InternalError`, so every
domain lands on `error` rather than `unknown` (verified on Raven 2026-08-29,
`unraid-api stop`, all nine domains). A rule that keyed on the word `unknown`
therefore never fired on the commonest way a node dies. Correspondingly, a blind poll is an *absence* of
information: it neither advances a debounce count nor resets one, so one lost
cycle cannot launder a pending warning away.

**Stale good news greys; stale bad news stays.** When the daemon dies nothing
is written at all, so a row simply stops moving while still reading `ok`. Each
node therefore carries a server-computed `age`, and past `stale_after` a stale
`ok` resolves to `unknown` while a stale `degraded` keeps its verdict and is
marked stale — the finding is still true and still the thing worth seeing.

That resolution happens **once, in `health.php`**, so the fleet summary is a
tally of the cards it ships. It lived in `NodeCard.vue` for a day and the two
disagreed on hardware: `0 unknown` on the summary line beside a card reading
`? Unknown`. The threshold travels with the payload as `stale_after` for the
same reason — the banner and every card judge by one number rather than each
keeping a copy that can drift. The card is given `stored_state` alongside so it
can still word *why* something is grey without re-deciding *whether* it is.

## The seams that make it testable

No test may require a live Unraid box. That constraint drove most of the design:

- **`post_fn(address, port, key, query, timeout)`** — the collector and probe
  take the transport as a callable, so a fake answers from fixtures.
- **`parse_response(status, body)`** — pure. Classification is testable on
  Windows with no socket.
- **`Scheduler.due(now)`** — the scheduler owns no clock. `now` is an argument,
  which is what lets a test walk ten minutes of backoff in a millisecond.
- **`ctl.handle(line, handlers)`** — pure. Windows has no AF_UNIX, so every
  dispatch and error-shape rule lives in a function over a string; `serve()` is
  the thin socket shell around it.
- **`um_*_ok()` predicates** — the PHP gates split into pure predicates and thin
  `um_require_*` wrappers. The predicate is what the gate test exercises.
- **`PHP_SAPI !== 'cli'` dispatch blocks** — requiring an endpoint defines its
  functions and does nothing else, so the suite can call them with no session,
  no daemon and no web server.

## What this deliberately is not

**Read-only against every peer.** No mutation string appears anywhere in the
domain table OR the agent's verb table, and tests assert it on both sides.

That is now the only line left. P1 added what its own list ruled out — the Vue
bundle, design tokens, the rollup and its hysteresis. **P2a moved two more:**
there IS a Tier 1 agent now, and there IS SSH, verified against Golem on
2026-08-31.

What P2a deliberately did NOT move is the mutation boundary. A Tier 1 peer runs
one stateless script reached through an SSH forced command, and every verb in it
reads. So a defect today still only misreports a box.

**A peer still needs no plugin.** Tier 0 is a read-scoped API key and nothing
else; Tier 1 adds one file on flash and one `authorized_keys` line. That property
is what the phase was arranged to protect, and it survived.

**P2b is where the mutation boundary moves** — M5's mount manager and a watchdog
that unmounts by itself. After that, a defect can act on a box rather than
misreport one, which is why the confirm-token flow and the append-only action
journal arrive with it rather than before it.

## Hard-won platform facts

`docs/verification/tier0-coverage.md` is **binding** — it records what the
Unraid API and the webGUI actually do, verified against live boxes, and it
overrides any design document that disagrees. Read it before touching the
collector, the PHP gates or a `.page` header. Sixteen entries so far, most of
them things no amount of off-box testing would have revealed.
