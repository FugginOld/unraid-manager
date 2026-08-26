# Unraid-Manager — Unraid Fleet Manager

**Name:** Unraid-Manager
**Repo:** `FugginOld/Unraid-Manager`
**Plugin slug:** `unraid-manager`
**Status:** planning — no code written
**Purpose of this doc:** input to a Superpowers brainstorming pass for the backend, and a frontend brief for ui-ux-pro-max.

---

## 1. Problem statement

Unraid has no concept of a fleet. Every box is an island: its own webGUI, its own parity schedule, its own SMART state, its own notification channel. Once you run two or more servers, the operator becomes the integration layer — remembering which box has free space, which one is mid-parity-check, which one is running an older release, and which NFS mount is currently flapping.

Proxmox solved this with Datacenter Manager: one pane, N nodes, aggregated state, cross-node actions. Nothing equivalent exists for Unraid.

Unraid-Manager is a plugin that installs on one Unraid host (the **manager**) and gives a single dashboard over every Unraid box on the network, plus a small set of genuinely cross-host actions that no single-box tool can perform.

---

## 2. Scope

### In scope
- Read-only aggregated fleet state (arrays, pools, disks, shares, containers, VMs, versions).
- Cross-host coordination that requires knowing about more than one box: parity scheduling, capacity tiering, spares allocation, shutdown ordering.
- Managed inter-host storage links (mounts, namespace federation, replication jobs) with health supervision.
- Fleet-level alerting with deduplication.

### Out of scope (non-goals)
- Turning N arrays into one Unraid array. Unraid has no clustering primitive and this plan does not invent one.
- Live VM migration. Unraid VMs are libvirt but there is no shared-storage assumption; cold migration only.
- Replacing Unraid Connect. Unraid-Manager consumes the official API rather than competing with it.
- Managing non-Unraid hosts. That is [[topographer]]'s job — see §14 for the integration seam.
- Any write path to a peer's flash device beyond explicitly requested config backup.

---

## 3. Unraid platform substrate

The architecture must respect these. Several are load-bearing constraints, not preferences. Items marked **(verify)** should be confirmed against a live 7.3.2 box during brainstorming before they harden into design.

| Fact | Consequence for design |
|---|---|
| rootfs is tmpfs; `/usr/local/emhttp/**` is wiped on reboot | All persistence goes to flash (config) or a pool (data). Nothing durable lives in the plugin dir. |
| Flash is a USB stick with finite write endurance | **Telemetry never touches flash.** Config only, written on change, not on poll. |
| webGUI is PHP 8 under nginx + php-fpm, no framework | Page layer is plain PHP. Anything long-running lives outside php-fpm. |
| Pages are `.page` files with a header block (`Menu=`, `Title=`, `Icon=`, `Tag=`, `Cond=`) | Menu integration is declarative. Top-level tab needs a `Type="xmenu"` page **(verify exact 7.3 syntax)**. |
| Canonical local state is `/var/local/emhttp/*.ini` (`disks.ini`, `var.ini`, `shares.ini`, `users.ini`, `network.ini`) | Local collector reads these with `parse_ini_file`, never scrapes HTML. |
| Unraid 7 ships the official API — GraphQL endpoint, keys via `unraid-api apikey --create`, role-scoped | This is the agentless peer transport. Huge: peers need no plugin install for read-only mode. |
| nginx ships nchan for server push; the stock dashboard uses it | Live UI updates without polling. Publish to `/pub/<channel>`, subscribe at `/sub/<channel>` **(verify plugin-accessible channel namespace)**. |
| Python 3 is in the base image since 6.12 | Manager daemon can be Python with no bundled runtime. Confirm the exact minor version and stdlib completeness on 7.3.2. |
| Plugins are `.plg` XML fetching `.txz` built with `makepkg` | Standard packaging. Same shape as Unraid-HBAviewer — reuse that build pipeline. |
| emhttp fires event scripts (`started`, `stopping_svcs`, `disks_mounted`, `unmounting_disks`) into `plugins/<name>/event/` | Clean lifecycle hooks for daemon start/stop and safe-unmount ordering. |
| Cron via `/boot/config/plugins/<name>/<name>.cron` + `update_cron` | Scheduled jobs use the platform scheduler, not a bespoke timer loop. |
| Notifications via `/usr/local/emhttp/webGui/scripts/notify` | Fleet alerts can surface in the native notification bell as well as in Unraid-Manager's own bus. |
| POSTs require `$var['csrf_token']` | Every action endpoint validates CSRF. Non-negotiable. |

---

## 4. Architecture

### 4.1 Two-tier node model

```
                    ┌───────────────────────────────┐
                    │  MANAGER  (one Unraid host)   │
                    │                               │
   webGUI  ◄────────┤  .page  →  api/*.php          │
   (browser)        │              │                │
                    │              ▼                │
                    │        managerd (python)       │
                    │      scheduler │ collectors   │
                    │              │                │
                    │        SQLite on pool         │
                    └──────┬────────────────┬───────┘
                           │                │
              ┌────────────┘                └────────────┐
              ▼                                          ▼
   ┌──────────────────────┐                  ┌──────────────────────┐
   │ TIER 0  peer node    │                  │ TIER 1  peer node    │
   │ no plugin installed  │                  │ unraid-manager in node mode  │
   │ Unraid GraphQL + key │                  │ GraphQL + SSH agent  │
   │ READ ONLY            │                  │ READ + ACT           │
   └──────────────────────┘                  └──────────────────────┘
```

**Tier 0 (agentless).** Peer runs stock Unraid with the official API enabled and an Unraid-Manager-scoped API key. Manager polls GraphQL. Gets: array state, disk inventory, share list, container list, VM list, version, uptime, notifications. Costs the peer nothing and requires no trust beyond a read-scoped key.

**Tier 1 (agent).** Same plugin installed on the peer in node mode. Adds:
- Extended collectors GraphQL doesn't expose (HBA/PHY telemetry, mover state, pool balance, mount health, smb/nfs export config).
- Action execution: mount management, replication, container migration, staged shutdown.

Design rule: **every feature must degrade gracefully to Tier 0 or declare itself Tier-1-only in the UI.** No silent half-function.

### 4.2 Transport and auth

| Channel | Use | Auth |
|---|---|---|
| HTTPS → peer `/graphql` | All reads, both tiers | Unraid API key, read-scoped, one key per manager |
| SSH → peer, forced command | All Tier 1 actions | Dedicated ed25519 key, `command="/usr/local/emhttp/plugins/unraid-manager/scripts/agent-exec"` in `authorized_keys`, no PTY, no port forwarding |
| nchan on manager | Browser live updates | Inherits webGUI session |

Rationale for SSH over a bespoke HTTP agent: no new listening port, no TLS material to manage, the forced-command pattern gives a hard allowlist of executable verbs, and Unraid admins already have root SSH configured. The agent-exec script accepts a JSON verb envelope on stdin and is the **only** entry point — it validates verb, arguments, and refuses anything not in its table.

Optional overlay: bind the manager's outbound calls to the tailnet interface when Tailscale is present, so fleet traffic never rides the LAN broadcast domain.

### 4.3 Process layout on the manager

- `managerd` — long-lived Python daemon. Poll scheduler, collector dispatch, job runner, alert engine, nchan publisher. Started by `event/started`, stopped by `event/stopping_svcs`. Single process, thread pool for peer I/O, no external service dependencies.
- `api/*.php` — thin request layer. Validates CSRF and session, reads SQLite or talks to `managerd` over a unix socket, returns JSON. **Contains no business logic.**
- `.page` files — shell + mount point for the frontend bundle.
- `scripts/agent-exec` — the Tier 1 forced-command handler (present on every node, dormant on the manager unless it is also a managed node).

### 4.4 Storage

| Data | Location | Notes |
|---|---|---|
| Node registry, keys, feature config | `/boot/config/plugins/unraid-manager/*.cfg` (ini) | Flash. Written only on user change. API keys stored with `0600`, never world-readable. |
| Time series, event log, job history | `<pool>/appdata/unraid-manager/manager.db` (SQLite, WAL) | Pool path is **user-configured and mandatory**. Refuse to start if it resolves to flash or to `/tmp` on a box with an available pool. |
| Runtime cache, last-poll snapshots | `/tmp/unraid-manager/` | Volatile by design. |
| Flash config vault (M11) | `<pool>/appdata/unraid-manager/vault/` | Peer flash backups pulled *to* the pool, never pushed to flash. |

Retention: raw samples 7d, 5-minute rollups 90d, daily rollups 2y. Vacuum on a weekly cron. Hard cap the DB size with a configurable ceiling and drop oldest rollups on breach.

---

## 5. Canonical data model

One normalized model, populated from either transport. Collectors emit these shapes; the API serves them; the frontend consumes them. Version the schema from day one (`schema_version: 1`).

```jsonc
// Node
{
  "id": "uuid",                 // stable, generated by manager at enrollment
  "name": "golem",              // Unraid hostname
  "tier": 0,                    // 0 | 1
  "reachable": true,
  "last_seen": "2026-08-25T14:02:11Z",
  "version": { "unraid": "7.3.2", "kernel": "6.12.x" },
  "license": { "type": "Pro", "expires": null },
  "uptime_s": 1382400,
  "array": { "state": "STARTED", "protection": "single", "autostart": false },
  "capabilities": ["graphql", "ssh", "zfs", "docker", "libvirt"]
}

// Array / pool (unified "storage set")
{
  "node_id": "uuid",
  "kind": "array" | "pool",
  "name": "cache_movies",
  "fs": "xfs" | "btrfs" | "zfs",
  "profile": "single" | "raid1" | "mirror" | "stripe" | null,
  "redundancy": 0,              // 0 = none. Drives a loud UI state.
  "bytes_total": 5170000000000,
  "bytes_used": 4600000000000,
  "device_count": 3,
  "members_unallocated": ["nvme3n1p1"]   // added but never balanced onto
}

// Disk
{
  "node_id": "uuid", "slot": "disk23", "dev": "sdq",
  "model": "WUH721414AL4204", "serial_hash": "sha256:...",  // never raw serial in the UI
  "size_bytes": 14000519643136, "temp_c": 38, "power_on_hours": 41230,
  "smart": { "grown_defects": 1, "corrected": 360, "uncorrected": 6,
             "non_medium": 0, "reallocated": 0 },
  "verdict": "warn",            // ok | watch | warn | critical | unknown
  "verdict_basis": ["uncorrected_read_errors"]
}

// Link (managed inter-host storage relationship)
{
  "id": "uuid", "kind": "nfs" | "smb" | "dfs" | "mergerfs" | "zfs-repl" | "rsync",
  "source": { "node_id": "uuid", "path": "/mnt/disks/media1" },
  "target": { "node_id": "uuid", "path": "/mnt/remotes/RAVEN_media1" },
  "health": "ok" | "degraded" | "flapping" | "down",
  "flap_count_1h": 0,
  "options": { "soft": true, "bg": true, "retrans": 3, "timeo": 100 },
  "watchdog": { "enabled": true, "action_on_flap": "unmount" }
}

// Job
{
  "id": "uuid", "type": "parity_check" | "replication" | "tier_move" | "migration",
  "node_ids": ["uuid"], "state": "queued" | "running" | "done" | "failed",
  "started": "...", "progress": 0.42, "bytes_moved": 0, "eta_s": 7200,
  "log_ref": "jobs/uuid.log"
}

// Alert
{
  "id": "uuid", "severity": "info" | "warning" | "critical",
  "node_id": "uuid", "module": "disk", "key": "disk23.uncorrected",
  "first_seen": "...", "last_seen": "...", "count": 22870,
  "suppressed_until": null, "message": "..."
}
```

**Privacy rule:** disk serials, API keys, and tailnet addresses are hashed or masked in every API response the browser can see. Full values stay server-side.

---

## 6. Feature modules

Each is independently shippable. `T` column = minimum tier.

| ID | Module | T | One-line value |
|---|---|---|---|
| M1 | Fleet inventory & health rollup | 0 | The pane. Node cards, worst-of health, capacity, versions. |
| M2 | Capacity & cross-host tiering | 1 | Spill cold data to a peer when a share crosses a threshold. |
| M3 | Parity check coordinator | 1 | Stagger checks so two boxes never storm I/O on the same night. |
| M4 | Disk lifecycle & spares planner | 0/1 | Fleet SMART rollup, spare pool, "which box gets the next drive". |
| M5 | Peer mount manager + flap watchdog | 1 | One-click linking with sane options and a supervisor that unmounts instead of wedging nfsd. |
| M6 | Replication jobs | 1 | ZFS send/recv and rsync schedules with history and retention. |
| M7 | Namespace federation | 1 | Generate DFS / NFSv4 referral / mergerfs configs so paths span hosts. |
| M8 | Container & VM migration | 1 | Cold-move a container or VM to a peer. |
| M9 | Alert bus + syslog aggregation | 0/1 | Fleet-wide dedup and rate-limiting of repeating noise. |
| M10 | Drift matrix | 0 | Version, plugin, and config divergence across hosts. |
| M11 | Flash config vault | 1 | Collect every node's USB backup to one pool, diffed over time. |
| M12 | Power orchestration | 1 | Fleet UPS view, staged shutdown and startup ordering, WoL. |

### M1 — Fleet inventory & health rollup
Poll every node on an interval (default 30s, configurable, backoff on failure). Compute a **worst-of rollup** per node over sub-indicators: array state, redundancy, capacity headroom, disk health, thermal, link health, service state. Reuse the rollup semantics already specified for Unraid-HBAviewer — five-ish sub-indicators, a mandatory `unknown` state, hysteresis so a single bad sample doesn't flip a card red, and rate-based rather than absolute thresholds where the underlying metric is a counter.

`unknown` is a first-class state, not a synonym for `ok`. A node that hasn't answered in three intervals is `unknown`, and it looks visually distinct from both healthy and failed.

### M2 — Capacity & cross-host tiering
Watch per-share fill against a policy. When a share crosses `high_water`, select candidate directories by access age, move them to a peer over the established link, and leave behind a DFS link or mergerfs branch so client paths don't change.

Guardrails: dry-run first and always; refuse if the target has less than `2 × payload` free; refuse while the source array is degraded or mid-parity-check; hardlink-aware (moving one member of a hardlink set across hosts silently doubles space and breaks the *arr import path — detect and refuse, don't "handle").

### M3 — Parity check coordinator
Central calendar. Reads each node's schedule, detects collisions, proposes a staggered plan, writes it back via each node's own cron. Also supports **pause on peer activity** — hold a check if a peer is mid-replication over a shared link. Must never start a check on a node with a disabled or missing parity device.

### M4 — Disk lifecycle & spares planner
Fleet SMART table, sortable by verdict. Unassigned drives across all nodes form a spares pool. Recommends allocation: the box with the worst-verdict disk and least redundancy gets the next spare. Surfaces "added but never balanced onto" pool members and single-profile btrfs pools as standing findings, not transient alerts.

Reuse from HBAviewer: verdict gate chain, confidence tiers (`confirmed` / `observed-floor` / `weak`), fail-closed on unreadable inputs, blank ≠ zero.

### M5 — Peer mount manager + flap watchdog
The foundation module. Everything in M2/M6/M7 sits on a link that works.

- Enrollment wizard: pick source node + path, target node + mountpoint, protocol. Generates the export line on the source and the Unassigned Devices mount on the target with correct options (`soft`, `bg`, `retrans`, `timeo`, `intr` equivalent) rather than the hard-mount default that hangs shfs.
- **Watchdog:** probe each link on a short interval. On N transitions within a window, unmount rather than let the mount flap. Emit one alert, not one per transition. Optionally re-mount after a cooldown with exponential backoff.
- Pre-shutdown hook: unmount peer links before `unmounting_disks` so array stop doesn't hang.

Motivating case: a single flapping NFS mount produced 22,870 of 34,253 syslog lines in one window and blocked an `nfsd` restart. The watchdog exists to make that a one-line alert and an automatic unmount.

### M6 — Replication jobs
Job types: `zfs-send` (incremental, snapshot-driven, `mbuffer` over SSH), `rsync` (with `--link-dest` for versioned backup, `--inplace` for large media), `rclone` (offsite). Per-job schedule, retention policy, bandwidth cap, and a resume story. History with bytes moved and duration; a job that has never succeeded is visually distinct from one that succeeded and is now overdue.

### M7 — Namespace federation
A config *generator*, not a runtime. Three strategies, presented with their trade-offs in the UI rather than picked silently:
- **SMB DFS** — `msdfs root` plus msdfs symlinks. Client-side redirection, no union layer, no hardlink hazard. SMB clients only.
- **NFSv4 referrals** — server-side redirect for Linux and container clients.
- **mergerfs union** — true single path for local processes. Loudest warnings: hardlinks never cross a branch, and a degraded remote branch degrades the union.

Output is a reviewable diff against `smb-extra.conf` / exports / mount unit before anything is written, and every generated block is fenced with markers so it can be cleanly removed.

### M8 — Container & VM migration
Cold migration: stop → rsync appdata → import template/XML on peer → start → verify → optionally remove source. Preflight checks path mappings, port conflicts, network mode (`host` doesn't survive a move blindly), device passthrough (a container bound to `/dev/dri` cannot move to a node without a GPU). Refuse rather than migrate-and-break.

### M9 — Alert bus + syslog aggregation
Remote syslog collection to the manager's pool, per-node ring buffers, and a dedup engine keyed on `(node, module, key)` with count and first/last seen. Rate-limit rules so a repeating message collapses to one row. Optional forward to the native Unraid notifier and to a webhook.

### M10 — Drift matrix
Grid: nodes × (Unraid version, plugin set + versions, key config values). Highlights divergence. Reads config values from a curated list — not a full config diff, which would be noise.

### M11 — Flash config vault
Pull each node's flash backup on a schedule into the pool. Keep N generations, diff between them, surface "what changed on Golem's flash last Tuesday". Read-only against peer flash.

### M12 — Power orchestration
Aggregate NUT state across nodes. Define shutdown order (dependents first — the box exporting storage goes down last) and startup order with WoL and a readiness gate. Dry-run rehearsal mode that logs the plan without executing.

---

## 7. API surface

Manager-local, under `/plugins/unraid-manager/api/`. All responses `application/json`, all mutations POST with CSRF.

```
GET  /nodes                        list + rollup state
GET  /nodes/{id}                   full node detail
POST /nodes                        enroll (name/address/tier/key)
POST /nodes/{id}/test              connectivity + capability probe
DEL  /nodes/{id}                   unenroll (revokes local key material)

GET  /storage?node={id}            arrays, pools, members
GET  /disks?verdict={v}            fleet disk table
GET  /shares?node={id}             shares + cache policy + fill

GET  /links                        managed links + health
POST /links                        create (dry_run=true default)
POST /links/{id}/action            mount | unmount | remount | pause_watchdog

GET  /jobs?state={s}               job list
POST /jobs                         submit (dry_run honored)
POST /jobs/{id}/cancel

GET  /alerts?since={ts}            deduped alert feed
POST /alerts/{id}/ack

GET  /drift                        version/plugin/config matrix
GET  /schedule/parity              coordinator calendar
POST /schedule/parity/apply        write staggered plan (dry_run default)

GET  /events                       nchan channel descriptor for live subscribe
```

**Dry-run is the default for every mutating endpoint.** Executing requires an explicit `"confirm": true` plus the dry-run token returned by the preview call. This is the single most important safety property in the system: every cross-host action is preceded by a preview the operator saw.

---

## 8. Security model

- API keys are read-scoped by default; a Tier 1 node's SSH key is separate from its GraphQL key so read access and act access revoke independently.
- `agent-exec` verb table is an explicit allowlist. Unknown verb → exit non-zero, log, no side effect. Arguments are validated against a schema before any shell contact; no string interpolation into shell commands anywhere in the action path.
- Fail closed on every unreadable input. An unreadable node state is `unknown`, never `ok`.
- Every action is journaled: who, what node, what verb, what arguments, what result. The journal is append-only on the pool.
- Destructive verbs (delete source after migration, remove link, apply mover policy) require the confirm-token flow and are individually toggleable in settings — an operator can install Unraid-Manager in a permanently read-only posture.
- Secrets never enter the DOM, never enter a URL query string, never enter a log line.

---

## 9. Packaging and install

- `.plg` fetches a versioned `.txz` from GitHub releases. Same pipeline shape as Unraid-HBAviewer — reuse the build script and the release workflow rather than writing a second one.
- Install script: create `/boot/config/plugins/unraid-manager/`, seed default cfg, install `event/` hooks, register cron, start `managerd` if the array is up.
- Uninstall: stop daemon, unmount all managed links, remove cron, remove `/usr/local/emhttp/plugins/unraid-manager`. Prompt (do not assume) whether to keep the pool DB and the flash config.
- Node mode is the same package with a cfg flag — one artifact, two roles.
- Community Applications template once the plugin is stable; `templates/unraid-manager.xml`.

---

## 10. Frontend brief — for ui-ux-pro-max

### 10.1 Hard constraints

The UI lives **inside the Unraid webGUI shell**, not in a standalone page. It renders into a mount point in a `.page` file, below the Unraid header and tab bar.

- **Bundle:** Svelte compiled to a single self-contained IIFE + one CSS file. No CDN fetches at runtime, no build-time network access assumed at install. Total bundle budget: ≤ 250 KB gzipped.
- **Theming is inherited, not chosen.** Unraid ships four themes (white, black, azure, gray). The UI must read its palette from Unraid's own CSS custom properties and only define its own tokens as fallbacks. A hardcoded background color anywhere is a bug.
- **No `<form>` elements with native submit.** Event handlers only.
- **No browser storage APIs** for anything that must survive — server-side settings only. Ephemeral view state (column widths, collapsed sections) in memory.
- Live data arrives over nchan; the UI must handle subscribe, reconnect, and a stale-data banner when the stream is silent past a threshold.
- Must be usable at 1280px and degrade to tablet width. Phone is nice-to-have — Unraid's own shell isn't responsive below that anyway.

### 10.2 Design tokens

Define as CSS custom properties with Unraid vars as the primary source:

```css
--um-bg:        var(--background-color, #fff);
--um-fg:        var(--text-color, #1c1b1b);
--um-accent:    var(--accent-color, #ff8c2f);
--um-surface:   color-mix(in srgb, var(--um-bg) 94%, var(--um-fg));
--um-ok:        #3aa757;
--um-watch:     #d9a400;
--um-warn:      #e07b39;
--um-crit:      #cf3b3b;
--um-unknown:   #7a7a7a;   /* visually distinct from ok — never gray-as-neutral-good */
```

Status color must never be the only carrier of meaning — pair with an icon and a text label in every indicator. Some of this fleet is being read at a glance from across a room; some of it will be read by someone colorblind.

### 10.3 Information architecture

```
Fleet (top-level tab)
├── Overview          node cards, fleet rollup, active jobs strip
├── Storage           arrays/pools/shares across nodes; capacity treemap
├── Disks             fleet disk table, verdicts, spares planner
├── Links             managed links, health, watchdog state, topology view
├── Jobs              replication + migration + tier moves, history
├── Alerts            deduped feed, ack, suppression rules
├── Drift             version/plugin/config matrix
└── Settings          nodes, keys, thresholds, safety toggles
```

### 10.4 Screens

**Overview.** Grid of node cards. Each card: hostname, rollup status ring, array state, capacity bar with headroom number, disk-warning count, uptime, version. Card is clickable → Node Detail drawer. Above the grid, a one-line fleet summary. Below, a strip of running jobs with progress.

Node card must make three states instantly separable: healthy, degraded, unreachable. Unreachable is not red — it's the `unknown` treatment, because a red card that means "I can't see it" trains operators to ignore red.

**Node Detail (drawer, not a page).** Tabbed: Storage / Disks / Containers / VMs / Links / Log. Slides over the fleet view so the operator never loses fleet context. Escape closes.

**Storage.** Grouped by node. Each storage set shows fs, profile, redundancy, fill. **A `redundancy: 0` set gets a persistent inline warning badge** — a two-disk stripe and a single-profile btrfs pool look identical to a healthy mirror in stock Unraid, and that's exactly the failure this tool exists to prevent. Capacity treemap toggle for the whole fleet.

**Disks.** Dense sortable table. Columns: node, slot, model, size, temp, power-on hours, error counters, verdict. Verdict cell shows basis on hover. Filter chips by verdict. Bottom panel: unassigned spares across the fleet, with the allocation recommendation.

**Links.** Two views: a table and a small node-to-node topology graph with links as edges colored by health. Flapping links get a distinct animated treatment — this is the state that most needs to grab attention. Per-link actions with the dry-run preview modal.

**Jobs.** Timeline plus table. A job that has never succeeded, a job that succeeded but is overdue, and a job running now are three visually distinct states. Progress with bytes and ETA. Click → log tail.

**Alerts.** Feed with count badges (`×22,870` on a single row, not 22,870 rows). Group by node or by module. Ack, snooze, and "create suppression rule" inline.

**Drift.** Matrix, nodes as columns. Divergent cells highlighted; identical rows collapsible so only the differences show by default.

**Settings.** Node enrollment wizard (address → probe → capability report → key paste → confirm). Threshold editors with live preview of what would currently trip. A prominent **read-only mode** master toggle.

### 10.5 The dry-run modal

Used by every mutating action, so it deserves its own spec. Structure:
1. Plain-language summary of what will happen.
2. The concrete diff — exact config lines, exact file paths, exact byte counts.
3. Preflight results as a checklist with pass/fail/skip and reasons.
4. Any refusal stated as a refusal with its cause, not a disabled button with no explanation.
5. Confirm requires typing the target node name for destructive verbs.

### 10.6 Empty and error states

Design these explicitly, not as afterthoughts: no nodes enrolled yet (should be a guided first-run, not an empty grid), a node enrolled but unreachable, GraphQL reachable but key lacking scope, Tier 0 node where a Tier 1 feature was requested, and stream disconnected.

---

## 11. Build phases

| Phase | Contents | Exit criterion |
|---|---|---|
| **P0** Skeleton | Plugin packaging, `.page` shell, `managerd` lifecycle, SQLite bootstrap, settings, node enrollment, Tier 0 GraphQL collector | Two real nodes enrolled and visible with live state |
| **P1** The pane | M1 rollup, M4 disk table, M10 drift, nchan live updates, full frontend Overview + Disks + Drift | Replaces opening two browser tabs |
| **P2** Links | M5 mount manager + watchdog, Links screen, dry-run modal, Tier 1 agent + forced command | A deliberately flapped mount produces one alert and an auto-unmount |
| **P3** Coordination | M3 parity coordinator, M9 alert bus + syslog, M12 power orchestration | Two nodes never parity-check on the same night without being told twice |
| **P4** Data movement | M6 replication, M11 vault, Jobs screen | Scheduled ZFS incremental between two nodes, with history |
| **P5** Federation | M7 namespace generator, M2 tiering, M8 migration | A path that spans hosts, and a container that moved cleanly |

P0–P2 is the minimum viable product. Everything past P2 is additive and independently valuable — stop anywhere.

**Decided 2026-08-25:** P0's UI (settings, enrollment, node status table) is plain `.page` PHP — no build toolchain in P0. The Svelte bundle (§10) arrives in P1 with the pane. The first design pass covers P0 only.

---

## 12. Testing

- **Offline fixtures.** Every collector tested against captured GraphQL responses and captured `.ini` files. No test may require a live Unraid host. Same pattern as topographer's `tests/test_pipeline.py`.
- **Fault injection.** A fixture harness that simulates: node unreachable, partial GraphQL response, malformed ini, clock skew between nodes, mount flap, and mid-job disconnect. The rollup and watchdog logic are only as good as their behavior under these.
- **PHP layer tests** for CSRF enforcement and the confirm-token flow — mirroring the gate-chain tests already used for HBAviewer's `flash.php`.
- **Invariant tests** on the data model: no raw serial in any API response, no secret in any log line, no write path resolving under `/boot` outside the allowed config dir.
- **Dry-run parity test:** for every mutating verb, assert that dry-run and execute produce the same plan, and that dry-run produces zero side effects.
- CI on GitHub Actions, matching the existing repo conventions.

---

## 13. Documentation conventions

Per the established repo standard: no repo-level `CLAUDE.md`. Repo-specific material lives in `CONTRIBUTING.md`, `ARCHITECTURE.md`, and `HOWTO.md`, with `docs/review-policy.md` carrying the evidenced protected-paths list and tool scope, pinned in CI. Generated reference docs must be verified against source in CI — a drifted reference that passes checks the code fails is a known and previously-encountered failure mode.

---

## 14. Relationship to topographer

Overlap is real and should be resolved deliberately rather than by accident:

- topographer already has the agent/server model, bootstrap one-liners, a collector→normalize→render pipeline, and a fleet dashboard.
- Unraid-Manager needs all four, but Unraid-specific and inside the webGUI.

Three options for brainstorming to decide:
1. **Independent.** Unraid-Manager reimplements what it needs. Cleanest boundaries, most duplication.
2. **Unraid-Manager as a topographer renderer.** topographer gains an `unraid` collector; Unraid-Manager becomes the Unraid-native presentation layer over topographer's model. Least duplication, couples release cycles.
3. **Shared core library.** Extract normalize + collector interfaces to a small package both consume. Middle path, most upfront work.

Recommendation to evaluate: option 1 for P0–P2, then extract to option 3 once the Unraid-Manager model has stabilized and the actual overlap is observable rather than predicted.

---

## 15. Open questions for brainstorming

1. **Unraid API coverage.** **Verified 2026-08-25 against a live 7.3.2 box** — see `docs/verification/tier0-coverage.md` and the raw schema dump beside it. Headlines: parity check live control and docker/vm stop/start are API mutations (Tier 0 with a write-scoped key); log tailing and typed notifications are Tier 0; SMART attributes and pool redundancy/profile are NOT exposed (Tier 1); `Query.disks` can 504; one failing resolver nulls a batched response, so collectors query per-domain.
2. **nchan channel namespace.** Can a plugin publish to its own channel without conflicting with the stock dashboard, and what's the auth boundary on `/sub`?
3. **Manager election.** ~~Fixed manager, or should node mode be promotable?~~ **Decided 2026-08-25: fixed manager for v1.** Links survive manager downtime (it supervises, it is not in the data path); losing the manager loses only the pane.
4. **Manager on a non-Unraid host.** Is there value in a container build so the manager can run somewhere that isn't itself an array? Changes the packaging story significantly.
5. **Multi-manager / read replica.** Two managers watching each other, or explicitly out of scope for v1?
6. **License and key handling.** Where do peer API keys live, how are they rotated, and what's the recovery path when a peer is rebuilt?
7. **Mover integration depth.** Does M2 extend Unraid's mover, or run alongside it? Extending is more seamless and considerably more dangerous.
8. **Failure semantics for partial fleet.** When 3 of 5 nodes answer, does the fleet rollup report a status at all, or refuse? Consistency with the HBAviewer fail-closed posture argues for an explicit degraded-visibility state.
9. **Python version floor.** Confirm what's in the 7.3.2 base image and whether any dependency needs vendoring.
10. **Terminology collision.** **Decided 2026-08-25: resolved by renaming the plugin** from Unraid-Link to Unraid-Manager (repo, slug, daemon `managerd`, DB `manager.db`). `Link` remains the model object, API resource, and UI screen with no collision.

---

## 16. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| A cross-host action corrupts or loses data | Critical | Dry-run default + confirm token; destructive verbs individually disableable; read-only master toggle; never delete source until target verified |
| Managed mount hangs shfs and wedges array stop | High | Soft mounts only; watchdog unmount; pre-`unmounting_disks` event hook |
| Telemetry writes wear out the flash device | High | Hard architectural rule: no telemetry on flash; startup check refuses a flash-resolving DB path |
| Unraid API changes between releases | Medium | Version-gate collectors; capability probe at enrollment; degrade to `unknown` rather than guess |
| SSH forced command escaped into arbitrary execution | High | Allowlisted verb table, schema-validated args, no shell interpolation, no PTY |
| Scope creep into a Proxmox clone | Medium | The non-goals in §2 are binding; anything requiring clustering is refused |
| Manager becomes a single point of failure for storage links | Medium | Links are configured to survive manager downtime — the manager supervises, it is not in the data path |
| Generated config blocks corrupt `smb-extra.conf` or exports | High | Fenced markers, diff preview, backup-before-write, clean removal path |

---

## 17. Prompts for the Superpowers brainstorming pass

Feed these individually rather than as a batch:

1. Challenge the two-tier node model. Is agentless Tier 0 worth the complexity of two code paths, or should the plugin simply require installation on every node?
2. Interrogate the SSH forced-command transport against a small mTLS HTTP agent. Which fails more safely, and which is easier to audit?
3. Attack the dry-run/confirm-token flow. Find the sequence where a preview and an execute diverge.
4. Design the M5 watchdog state machine in full — states, transitions, timers, hysteresis, and the interaction with array stop.
5. Specify the M1 rollup function precisely: sub-indicators, weights, `unknown` propagation, hysteresis windows, and what a fleet-level rollup means when nodes disagree.
6. Enumerate what the 7.3.2 GraphQL API actually exposes and mark every §6 module as fully / partly / not supported at Tier 0.
7. Stress the storage schema against a node with 23 array disks, five pools of three different filesystems, and a ZFS stripe — then against a single-disk box with no pools.
8. Decide the topographer relationship (§14) with a written rationale.
9. Define the SQLite schema, retention job, and size-cap enforcement.
10. Write the `agent-exec` verb table: every verb, its arguments, its schema, its preconditions, and its refusal conditions.
