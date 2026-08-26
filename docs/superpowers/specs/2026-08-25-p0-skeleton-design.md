# Unraid-Manager P0 — Skeleton: design spec

**Date:** 2026-08-25
**Scope:** Build phase P0 only (plan §11). Parent plan: `unraid-manager-plan.md`. Verified platform facts: `docs/verification/tier0-coverage.md`.
**Exit criterion:** Raven and Golem enrolled on the manager and visible in the webGUI with live state.

## Decisions this spec inherits

- Plugin renamed **Unraid-Manager** (slug `unraid-manager`, daemon `managerd`, DB `manager.db`); `Link` stays reserved for the P2 model object.
- Fixed single manager. No node mode, no SSH, no Tier 1, no mutations in P0 — the manager is strictly read-only against peers.
- P0 UI is plain `.page` PHP. Svelte arrives in P1.
- Verification is done: both target boxes run Unraid 7.3.2 / API 4.37.3, auth via `x-api-key` header against `https://<host>:<port>/graphql`.

## Constraints from live verification (binding)

1. **Per-domain queries.** One failing resolver nulls an entire batched GraphQL response (observed: `upsDevices` on a UPS-less box). Every domain is its own HTTP request; a domain failure marks that domain `unknown` — never the node, never the fleet.
2. **`Query.disks` is a slow lane.** 15.4s on a healthy 37-disk box; reproducible 60s/504 on Raven. Generous timeout (90s), cached last-good value, never in the fast loop.
3. **Empty array is healthy.** `capacity.kilobytes` all-zero + zero used slots = empty array (Raven), not missing data. Rollup must not read it as failure.
4. **No runtime introspection.** Production boxes have it off. Capability probing uses targeted per-domain probe queries.
5. **Fail closed.** Unreadable → `unknown`, visually distinct from `ok` and from failed, end to end (daemon → DB → API → UI).
6. **Flash discipline.** Config written only on user change. Telemetry only on the pool. Startup refuses a DB path resolving under `/boot`.
7. **Secrets.** API keys: flash, `0600`, never in the DB, never in any HTTP response, log line, or DOM.

## 1. Repo & package layout

```
unraid-manager.plg                  ← XML installer (adapted from hbaviewer.plg)
build.sh                            ← .txz builder (adapted from HBAviewer)
.github/workflows/release.yml       ← release CI (adapted)
source/usr/local/emhttp/plugins/unraid-manager/
├── UnraidManager.page              ← Menu="Tasks:95", Type="xmenu" — top-level "Fleet" tab
├── UnraidManagerSettings.page      ← Menu="Utilities" — settings + enrollment
├── api/
│   ├── nodes.php                   ← list/detail/enroll/test/delete
│   ├── settings.php                ← get/set manager config
│   └── events.php                  ← journal feed
├── include/common.php              ← session+CSRF gate, SQLite open-readonly, socket client, JSON helpers
├── daemon/
│   ├── managerd.py                 ← entrypoint: scheduler, thread pool, signal handling
│   ├── gqlclient.py                ← urllib-based GraphQL POST, TLS (self-signed tolerated), timeouts
│   ├── collector.py                ← domain definitions: query text, lane, parser → normalized dict
│   ├── store.py                    ← SQLite schema/bootstrap/writes, retention prune
│   └── ctl.py                      ← unix-socket command listener
├── event/
│   ├── started                     ← rc.unraid-manager start
│   └── stopping_svcs               ← rc.unraid-manager stop
└── scripts/rc.unraid-manager       ← start|stop|status; refuses bad DB path
tests/                              ← Python unittest + fixtures; PHP gate tests
```

Install script seeds `/boot/config/plugins/unraid-manager/`, installs event hooks, registers the retention cron (`unraid-manager.cron` + `update_cron`), starts `managerd` if the array is up. Uninstall stops the daemon, removes cron and `/usr/local/emhttp/plugins/unraid-manager`, prompts about keeping flash config and the pool DB.

## 2. Configuration (flash)

`/boot/config/plugins/unraid-manager/manager.cfg` (ini):

```ini
db_path=            # REQUIRED, pool path e.g. /mnt/user/appdata/unraid-manager — daemon refuses /boot/**
poll_fast=30        # seconds
poll_slow=600       # seconds
```

`/boot/config/plugins/unraid-manager/nodes.cfg` (ini, one section per node):

```ini
[<uuid>]            # generated at enrollment
name=Golem          # from probe (info.os.hostname), operator-editable
address=192.168.2.248
port=15137
tier=0
enabled=1
```

`/boot/config/plugins/unraid-manager/keys/<uuid>.key` — the node's API key, `0600`, one file per node. Deleting a node deletes its key file.

Flash cfg is the **authoritative registry** (survives pool/DB loss); the daemon syncs it into SQLite at startup and on `reload`.

## 3. managerd

Single Python 3.11 process, stdlib only (verified: `sqlite3, ssl, json, threading, socket` present). Structure:

- **Scheduler thread** ticks once per second; dispatches due (node × lane) jobs to a `ThreadPoolExecutor` (max 8 workers).
- **Lanes.** Fast (default 30s): domains `info`, `array`, `shares`, `notifications`, `metrics`, `parity`. Slow (default 600s): `disks` (includes `assignableDisks`), `plugins` (`installedUnraidPlugins`), `logfiles` (names/sizes only). Each domain = one GraphQL request with its own timeout (fast: 10s, slow: 90s).
- **Domain result** → `node_state` upsert: `ok` + normalized payload, or `error` + message (payload keeps last-good), and `samples` rows for numeric series (array bytes used/total, per-pool fs used/total, disk temps, cpu/mem percent).
- **Backoff.** Per node: consecutive all-domain failures double the effective fast interval (30 → 60 → … → 600s cap). A node with 3 consecutive missed fast cycles is `unknown`; `last_seen` stops advancing. Any success resets backoff.
- **nchan publish.** After each fast cycle with changes: POST a compact delta `{node_id, domains: {name: status}, ts}` to the local nchan publisher endpoint (channel `unraid-manager`), discovered from `/etc/nginx/conf.d/servers.conf` at startup. Publish failure is logged once and disables publishing until reload — the UI's fallback polling covers it.
- **Lifecycle.** `rc.unraid-manager start`: validate `db_path` (set, not under `/boot`, parent exists) else exit non-zero with a clear message; write pidfile `/var/run/unraid-manager/managerd.pid`. `stop`: SIGTERM → clean shutdown (drain pool, close DB). `event/started`/`event/stopping_svcs` call the rc script.
- **Logging.** `/var/log/unraid-manager/managerd.log`, size-capped rotation (1 MB × 2). Never logs a key.

### Control socket

`/var/run/unraid-manager/managerd.sock`, mode `0600` root. Protocol: one JSON object per line in, one per line out.

| Command | Args | Returns |
|---|---|---|
| `status` | — | daemon uptime, per-node last poll, queue depth |
| `poll_now` | `node_id?` | ack; schedules immediate fast+slow poll |
| `test_node` | `address, port, key` | probe report (below) — key passed through, never persisted |
| `reload` | — | re-read cfg, resync registry |

### Probe (used by `test_node` and enrollment)

Runs each fast-lane domain query once against the candidate. Returns per-domain `ok | error(message)` plus headline facts (hostname, unraid/api versions, array state, share count). Distinguishes: unreachable (connect/TLS failure), bad key (401/auth error), key lacking scope (domain-level auth errors), partial (some domains fail).

## 4. SQLite (pool, WAL)

```sql
PRAGMA user_version = 1;
CREATE TABLE nodes(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, port INTEGER NOT NULL,
  tier INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
  added_at TEXT NOT NULL, last_seen TEXT);
CREATE TABLE node_state(
  node_id TEXT NOT NULL, domain TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ok','error','unknown')),
  error TEXT, fetched_at TEXT, payload TEXT,          -- payload = last-good normalized JSON
  PRIMARY KEY(node_id, domain));
CREATE TABLE samples(node_id TEXT NOT NULL, metric TEXT NOT NULL, ts TEXT NOT NULL, value REAL NOT NULL);
CREATE INDEX samples_by_series ON samples(node_id, metric, ts);
CREATE TABLE events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, node_id TEXT,
  kind TEXT NOT NULL, message TEXT NOT NULL);          -- enroll/remove/poll_fail/daemon lifecycle
```

Retention (daily cron → `managerd` prune via socket, or direct sqlite3 if daemon down): `samples` older than 7 days deleted; `events` capped at 10,000 rows; weekly `VACUUM`. Rollups are P1.

## 5. PHP API layer

All under `source/.../api/`, all responses `application/json`. Every endpoint requires an authenticated webGUI session; every POST validates `$var['csrf_token']`. GETs open the DB read-only. POSTs never touch the DB directly for writes — they write flash cfg and/or send a socket command; the daemon owns the DB.

| Call | Behavior |
|---|---|
| `GET nodes.php` | All nodes: registry fields + per-domain status + headlines (array state, capacity, versions, unread notification counts, last_seen). No payload bodies. |
| `GET nodes.php?id=<uuid>` | One node: everything above + full normalized domain payloads. |
| `POST nodes.php` `{action:"probe", address, port, key}` | Runs `test_node`, returns the probe report. Nothing persisted. |
| `POST nodes.php` `{action:"enroll", name, address, port, key}` | Writes `nodes.cfg` section + key file, `reload`s daemon, returns the new node. Rejects duplicate address:port. |
| `POST nodes.php` `{action:"delete", id}` | Removes cfg section + key file, `reload`. DB rows for the node are deleted by the daemon on sync. |
| `POST nodes.php` `{action:"poll", id}` | `poll_now`. |
| `GET settings.php` / `POST settings.php` | Read/write `manager.cfg` (validates db_path not under `/boot`), `reload` on change. |
| `GET events.php?since=<id>` | Journal rows after `since`, newest-capped at 200. |

Key material flows browser → PHP → cfg/socket only. No response ever includes a key; node listings include `has_key: true`.

## 6. P0 UI

**UnraidManager.page — "Fleet" tab.** A table: node name, state chip, array state, capacity bar (from `capacity.kilobytes`; empty-array shows "empty array", not 0%), Unraid/API versions, unread notifications (info/warn/alert counts), last seen. State chip is three-valued and each value pairs color + icon + label: `ok` (green), `degraded` (some domains error — amber), `unknown` (gray, distinct styling — never rendered as ok, never as red). Row click → simple detail section (per-domain status + fetched_at + headline payload fields). Live refresh: `EventSource` on `/sub/unraid-manager`; on message or 30s fallback timer, re-fetch `nodes.php`. A stale banner appears when neither stream nor fetch has succeeded for 3 minutes.

**UnraidManagerSettings.page.** Enrollment form (address, port, key → Probe button → capability report table → Enroll button enabled only on at-least-partial success). Node list with test/poll-now/remove. Manager settings: db_path (with not-flash validation message), poll intervals. Daemon status line (from `status` socket call) with start/stop.

Styling: stock webGUI conventions and classes; no bundler, no external assets; the `--um-*` token set from plan §10.2 is P1 material.

## 7. Testing

- **Python `unittest`** (no pip): `collector.py` parsers against fixtures captured from Raven and Golem during implementation (populated array, empty array, resolver-error response, 504 HTML body, malformed JSON, missing fields). Scheduler/backoff with a fake clock. Store writes + retention prune against tmp SQLite.
- **Invariant tests:** no key string in any log output or any API-bound payload; `unknown` propagation (unreachable node → all domains unknown → API reports unknown); empty-array box reports `ok`.
- **PHP gate tests** (HBAviewer pattern): POST without CSRF → 403; GET without session → 401; enroll writes `0600` key file.
- **CI:** GitHub Actions, Python + PHP jobs. No test requires a live box.

## 8. Out of scope for P0

M1 worst-of rollup (P0 ships the three-value chip only), hysteresis, Svelte/design tokens, nchan-pushed payloads (only "something changed" pings), retention rollups, any mutation against a peer, Tier 1/SSH/agent-exec, alerting, drift matrix, node cards. All P1+.
