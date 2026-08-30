# Tier 0 coverage — verified against live Unraid 7.3.2

**Box:** Raven, `https://192.168.2.19:29220/graphql`
**Verified:** 2026-08-25, read-scoped API key, `x-api-key` header auth
**Versions:** Unraid 7.3.2, API 4.37.3+d5058009, kernel 6.18.38-Unraid
**Schema dump:** [graphql-schema-raven.json](graphql-schema-raven.json) (251 types; introspection requires `unraid-api developer --sandbox true`, off in production)

## Module map: what Tier 0 (agentless GraphQL) actually supports

| Module | Plan tier | Verified Tier 0 support | Notes |
|---|---|---|---|
| M1 inventory/health | 0 | **Full**, one gap | `array` (state enum, per-disk temp/errors/fsUsed), `info` (versions incl. php/docker/nginx, uptime-as-boot-timestamp), `metrics` (cpu/mem/temp/net), `notifications.overview`, `services`. **Gap:** pool profile/redundancy not exposed — the redundancy-0 warning needs Tier 1. |
| M2 tiering | 1 | Monitoring only | `shares` (free/used/size, floor). Moves stay Tier 1. |
| M3 parity coordinator | 1 | **Upgraded: live control at Tier 0** | `parityCheck.start(correct)/pause/resume/cancel` mutations + `parityHistory` + `array.parityCheckStatus`, with a write-scoped key. Schedule *rewriting* (cron) stays Tier 1. |
| M4 disk lifecycle | 0/1 | Partial | `smartStatus` is only `OK\|UNKNOWN` — no SMART attributes at Tier 0; full verdict chain needs Tier 1 smartctl. `ArrayDisk.numErrors`/`temp` work. `assignableDisks` = spares pool. **Hazard: see finding 2.** |
| M5 mounts/watchdog | 1 | None (as planned) | No mount/export API. |
| M6 replication | 1 | None (as planned) | |
| M7 federation | 1 | None (as planned) | |
| M8 migration | 1 | Control plane only | `docker.stop/start`, `vm.start/stop/...` mutations exist; preflight data (ports, mounts, devices, tailscale) rich on `DockerContainer`. Data move stays Tier 1. |
| M9 alerts/syslog | 0/1 | **Upgraded** | `logFiles` + `logFile(path,lines,startLine)` + `logFile` subscription = syslog tail at Tier 0. `notifications` typed with importance. Remote syslog forwarding optional Tier 1. |
| M10 drift | 0 | Full, one trap | `installedUnraidPlugins` = real .plg list (names only, **no versions** at Tier 0). `plugins` = *API* plugins, not .plg — do not confuse. Versions matrix via `CoreVersions`/`PackageVersions`; config via `vars` (curated). |
| M11 flash vault | 1 | Uncertain | `initiateFlashBackup` mutation exists but appears Connect-cloud-oriented. Keep Tier 1 pull plan; investigate later. |
| M12 power | 1 | UPS view at Tier 0 | `upsDevices`/`upsUpdates` (errors when no UPS — see finding 1). `array.setState` mutation exists. No WoL, no shutdown verb → staged shutdown stays Tier 1. |

Also available at Tier 0, unplanned: GraphQL **subscriptions** over WS (`arraySubscription`, `notificationAdded`, `systemMetrics*`, `dockerContainerStats`, `logFile` tail) — a future alternative to polling; P0 still polls.

## Operational findings (bind the collector design)

1. **One failing resolver nulls the whole response.** A batched query including `upsDevices` on a box with no UPS returned `data: null` + error. Collector must issue **per-domain queries** with independent error isolation; a domain error marks that domain `unknown`, never the node.
2. **`Query.disks` 504s at nginx's 60s timeout** on Raven, reproducibly. Physical-disk enumeration is a slow lane: separate query, generous timeout, cached last-good value, never in the hot poll path. `array.disks` (assigned disks) is fast and sufficient for M1.
3. **Empty array is a healthy state.** Raven: `STARTED`, 0 data disks, 0 parities, capacity `0/0/0`, 30 free slots, 2-device btrfs cache. Rollup must not read this as failure (plan §17.7 stress case, live).
4. **Introspection is off in production.** Runtime capability probing must use targeted probe queries, never introspection.
5. `InfoOs.uptime` is a **boot timestamp** (ISO8601), not a duration.
6. Multi-device pool members appear as sibling `caches` entries (`cache`, `cache2`), the non-primary with null `fsType`/`fsSize` — dedupe when computing pool capacity.

## Platform facts verified on Raven (2026-08-25, root shell paste-back)

- **Python 3.11.15** in base image; `sqlite3, ssl, json, threading, socket` all import — daemon can be pure-stdlib Python.
- **nchan:** subscriber at `location /sub/...` with free-form `nchan_channel_id "$1"` (comma-split multi-channel), `nchan_authorize_request` present but commented out; publishers listen on a **local socket** (`servers.conf`) also with free-form `$1` channel ids. A plugin can publish to its own channel (e.g. `unraid-manager`) from localhost and browsers subscribe at `/sub/unraid-manager` inside the authed webGUI vhost. 256M shared memory configured.
- **`.page` top-level tab:** `Type="xmenu"` is standard (49 uses on this box); top-nav placement via `Menu="Tasks:NN"` ordering.
- **`/var/local/emhttp/` on 7.3.2:** `disks.ini, var.ini, shares.ini, users.ini, network.ini, devs.ini, diskload.ini, monitor.ini, sec.ini, sec_nfs.ini, unassigned.devices.ini, flashbackup.ini`, plus a **`smart/` directory** (cached SMART data — likely Tier 1 goldmine for M4) — Tier 1 local collector inputs confirmed.

## Second box verified: Golem (2026-08-25)

**Box:** Golem, `https://192.168.2.248:15137/graphql` — Unraid 7.3.2, API 4.37.3 (same as Raven).
22 data disks + 1 parity (`numErrors: 0`, DISK_OK), 250 TB used / 268 TB, 39 shares, 5 pools across xfs/btrfs/zfs — the plan §17.7 stress case, live.

- `capacity.kilobytes` is **truthful on a populated array** — Raven's zeros are the legitimate empty-array reading, not an API bug. All-zero kilobytes + zero used slots = empty array.
- `Query.disks` **works** here: 15.4s for 37 disks. The hard 504 is Raven-specific (investigate that box's stuck device eventually), but 15s+ confirms the slow-lane design — never in the 30s hot path. **Amended 2026-08-30: no longer Raven-specific.** Golem 504s too, and intermittently on both — a `node_state` read at 18:52 had `disks` `ok` on both nodes with fresh payloads, and the ~18:51 slow poll on each answered `HTTP 504 Gateway Time-out from nginx`. So the slow lane is not "one sick box"; it is a lane that fails on a healthy fleet often enough to be the normal case, which is what the retained-payload contract in api/disks.php is for. Observed via `scripts/check_disks_stale.py`.
- Multi-device pool pattern confirmed at scale: `cache_movies/2/3`, `cache_tv/2/3/4`, `medianucbackup/2` — non-primary members carry null `fsType/fsSize/fsFree`. ZFS pool (`medianucbackup`) reports fs sizes like the others; still no profile/redundancy field on any of them.

## Wire-shape facts (verified live 2026-08-26, both boxes — these correct the P0 plan)

Settled by querying Raven and Golem directly during Task 4 review, after the plan's
seed fixtures were found to encode shapes the API cannot return.

1. **`array.parityCheckStatus` is an OBJECT (`ParityCheck!`), not a string.** The leaf-field
   form the plan used is rejected outright: `Field "parityCheckStatus" of type "ParityCheck!"
   must have a selection of subfields.` Correct selection: `parityCheckStatus { status
   progress errors correcting paused running }`.
2. **On an empty array the boolean/count subfields come back `null`, not `false`/`0`.**
   Raven, verbatim: `{"status":"NEVER_RUN","progress":0,"errors":null,"correcting":null,
   "paused":null,"running":null}`. This is a blank-is-not-zero case: a parser coercing
   these to `false` invents a fact the box did not state.
3. **`BigInt` fields serialize as JSON NUMBERS, not strings.** Golem, verbatim:
   `{"name":"disk1","size":13672382412,"numErrors":0,"fsSize":13998382592,"fsFree":457119343}`
   and `{"name":"appdata","free":830838768,"used":168876601,"size":0}`. Every `BigInt`
   (`ArrayDisk.size/numErrors/fsSize/fsFree`, `Share.free/used/size`) is a number.
4. **But `Capacity` fields ARE strings** — `capacity.kilobytes` returns
   `{"free":"17337941911","used":"250627444451","total":"267965386366"}`. `Capacity.free/used/total`
   are declared `String` in the schema, unlike `BigInt`. Both encodings are correct, for
   different types; a collector must not assume one rule.
5. **Non-primary pool members carry `size`, but null `fsType`/`fsSize`/`fsFree`.** Golem's
   4-member pool, verbatim: `cache_tv` (btrfs, fsSize 4000815350), then `cache_tv2/3/4` each
   `{"fsType":null,"fsSize":null,"fsFree":null,"size":976761560}`. Pools run to at least
   4 members — dedupe logic that strips a trailing `2` or pairs adjacent entries silently
   drops members and under-reports capacity.
6. **`ParityCheck.status` observed values: `COMPLETED`, `CANCELLED`, `FAILED`** across
   Golem's 300+ entry history. `OK` is not a member of the enum and never appears.
7. **API keys are 64 lowercase hex characters** on both boxes — relevant to any
   credential-shaped scanning threshold.
8. **The nulls are NOT an empty-array artifact.** Golem — a populated array with 300+
   history entries — returns `{"status":"COMPLETED","progress":0,"errors":null,
   "correcting":null,"paused":null,"running":null}`. Every non-running parity state
   nulls those four subfields, on both boxes. Treat null as the normal idle reading.
9. **`Share.include` / `Share.exclude` are LISTS, not strings** — live returns `[]`.
   The type names recorded earlier in this document were produced by an introspection
   helper that unwrapped LIST wrappers when printing, so any `String` in those earlier
   notes may really be `[String]`. Check the raw schema dump's `ofType` chain, not the
   flattened name, before trusting a scalar type here.
10. **Share count is NOT a stable invariant.** Golem reported 39 shares on 2026-08-25 and
   36 on 2026-08-26. Real share sets include disk shares (`disk1`..`disk23`) alongside
   user shares. No test or fixture may assert an exact share count.
   Real sample, verbatim: `{"name":"domains","free":168788140,"used":830927229,"size":0,
   "include":[],"exclude":[],"cache":null,"nameOrig":"domains","comment":"saved VM instances",
   "allocator":"highwater","splitLevel":"","floor":"1000000","cow":"auto","color":"yellow-on",
   "luksStatus":"0"}`

11. **`parityHistory` rows null DIFFERENTLY from `array.parityCheckStatus`, despite being
   the same GraphQL type (`ParityCheck`).** Verified across Golem's 355 rows and Raven's 3:

   | field | `array.parityCheckStatus` (idle) | `parityHistory` row |
   |---|---|---|
   | `progress` | `0` | **`null`** |
   | `errors` | `null` | **`0` — non-null, 357/358 rows** |
   | `correcting` / `paused` / `running` | `null` | `null` (0/358 non-null) |

   A single shared parser for the two contexts will be wrong in one of them. Golem row
   verbatim: `{"date":"2026-07-23T22:23:56.000Z","duration":102398,"speed":"136726495",
   "status":"COMPLETED","errors":0,"progress":null,"correcting":null,"paused":null,"running":null}`

12. **`ParityCheck.speed` is a free-form string with at least four incompatible formats**
   in one box's history — a landmine for any numeric parse. Observed on Golem, verbatim:
   `"136726495"` (bare bytes/sec), `"88.3 MB/s"` (human-readable with unit),
   `"0"`, and `"nanB/s"` (a NaN that reached the formatter). `float(speed)` raises on two
   of those four. Treat speed as an opaque display string, or parse defensively with an
   explicit unknown result — never assume it is numeric.

13. **History carries implausible dates and zero durations.** Golem's oldest rows include
   `2001-12-10` and a `FAILED` row dated `2021-01-01` with `"duration":0`. Clock skew and
   garbage timestamps are normal in this data; sorting or date arithmetic must tolerate them.

## Still open

- `smart/` directory contents/format (inspect when building Tier 1 M4).
14. **`ParityCheck.errors` can exceed the API's own `Int` type.** Golem's
    `parityHistory` row 46 reads `2441379360`; asking for that field makes the
    API answer the ENTIRE query with `Int cannot represent non 32-bit signed
    integer value` and `INTERNAL_SERVER_ERROR`, so one 2024 row costs the parity
    domain permanently. `parityHistory` takes no arguments, so the newest row
    cannot be requested on its own. The collector does not select `errors`; its
    parser still reads it where a box returns it, and reports `None` — unknown,
    never zero — where it does not. Verified live 2026-08-26 against Golem.

15. **Unraid's php-fpm has NO `pdo_sqlite`.** `PDO::getAvailableDrivers()` is
    an empty array in the fpm-fcgi SAPI, so every `new PDO('sqlite:...')` fails
    with "could not find driver" — caught, and every read silently returns
    nothing while the daemon looks perfectly healthy. The php CLI *does* have
    pdo_sqlite, which is why an off-box test suite cannot see this. The
    extension that IS present in both is `sqlite3`, so the PHP read layer uses
    the `SQLite3` class. Verified live 2026-08-26 on Raven, php 8.2, fpm-fcgi.

16. **A top-level tab needs `Code=`, not `Icon=`.** `Menu="Tasks:NN"` plus
    `Type="xmenu"` is not enough: the top bar renders an icon-font glyph from
    `Code="<4 hex>"`, and a page without one is skipped silently. Confirmed
    against every top-level page on Raven (Dashboard e943, Main e908, Shares
    e92a, Docker e90b, VMs e918, Plugins e944, Apps f0db, Tools e909,
    networkstats f1fe). `Name=` is optional; pages without it take the label
    from `Title=` or the filename. `Icon=`/`Tag=` are the Utilities-submenu
    shape and are ignored by the top bar. Verified live 2026-08-26.

- Raven's `disks` 504 root cause (box-side issue, not a blocker).

- **Does `dynamix.cfg` store `hot`/`max` in the display unit, or always in
  Celsius?** **ANSWERED 2026-08-29 on Raven: always Celsius.** `unit` governs
  rendering only, and the file is unit-invariant.

  Two observations, because one is not enough — a box configured in C and then
  flipped to F only proves the *read* path, and a real Fahrenheit operator
  types their thresholds while in F:

  | Step | Observed |
  | --- | --- |
  | Baseline | `unit="C"`, `hot="45"`, `max="55"` |
  | Switch unit to F, change nothing | `hot="45"`, `max="55"` — **unchanged**, while Disk Settings rendered them as 113/131 |
  | Still in F, type 122 / 140 and apply | `hot="50"`, `max="60"` — **converted on write** |

  So dynamix converts in both directions at the display layer and stores
  Celsius unconditionally. Inheriting needs no conversion. Converting was the
  real trap: it would have read a stored `45` as Fahrenheit and inherited 7 °C,
  warning on every disk forever.

  `config.py` and `common.php` used to **decline to inherit any temperature**
  when `unit` was `F`, falling back to our own 50/60 — safe under either
  answer, which is why it was written that way while the answer was unknown.
  That guard is now removed in both: an F box inherits exactly like a C box.
  Capacity is a percentage and was never affected.

  The removal itself was then verified on the box at `59e24df`, which needed
  care: with `unit="C"` the old and new readers return the SAME thing, because
  the guard only ever fired on F. So a C-mode reading proves the deploy took
  nothing. The discriminating capture, both facts in one command so they cannot
  be out of step:

  ```
  unit="F"  hot="45"  max="55"      ->  temp_warn=45 temp_crit=55
  ```

  The old readers return 50/60 for that input. Absence of the guard on the box
  was confirmed separately (`out.pop('temp_warn'` and `$fahrenheit` both gone
  from the installed files) — grepping for the word "Fahrenheit" would not have
  worked, since the replacement comments still contain it.

  Note the range check at `THRESHOLD_BOUNDS` only half-covers the wrong answer:
  a Fahrenheit `113` would have been rejected as out of band `(20, 99)`, but a
  box set to `95 °F` would have stored `95`, passed the check, and been
  inherited as a 95 °C threshold that can never fire.
