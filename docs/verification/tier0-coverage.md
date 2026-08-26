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
- `Query.disks` **works** here: 15.4s for 37 disks. The hard 504 is Raven-specific (investigate that box's stuck device eventually), but 15s+ confirms the slow-lane design — never in the 30s hot path.
- Multi-device pool pattern confirmed at scale: `cache_movies/2/3`, `cache_tv/2/3/4`, `medianucbackup/2` — non-primary members carry null `fsType/fsSize/fsFree`. ZFS pool (`medianucbackup`) reports fs sizes like the others; still no profile/redundancy field on any of them.

## Still open

- `smart/` directory contents/format (inspect when building Tier 1 M4).
- Raven's `disks` 504 root cause (box-side issue, not a blocker).
