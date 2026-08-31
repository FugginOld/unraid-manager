# Unraid-Manager P2a — The Tier 1 agent: design spec

The transport that lets the manager reach a peer for things GraphQL does not
expose. Read-only. It moves the trust boundary without moving the mutation
boundary — nothing in this phase can change a peer.

P2 as planned bundled this with M5 (mounts, watchdog, Links screen, dry-run
modal). Split, on the grounds the plan already applies to modules: the layer
beneath them is independently valuable — M3, M5, M6, M8 and M12 all sit on it —
and bundling meant the first thing that ever wrote to a peer would arrive in the
same phase as the thing deciding what to write. **P2a is this spec. P2b is M5.**

## Decisions this spec inherits

| Decision | Made | Why |
|---|---|---|
| Split P2 into transport (P2a) then M5 (P2b) | 2026-08-30 | Two exit criteria, two hardware passes; a misbehaving flap test cannot be blamed on the pipe |
| Flash-resident script on the peer, **no plugin** | 2026-08-30 | A peer needing no plugin is the project's best property. `agent-exec` is one file plus one `authorized_keys` line |
| Operator pastes the installer; no assisted provisioning | 2026-08-30 | The manager never holds a credential for another box. It can only ever use what the operator installed |
| Read-only verbs in P2a | 2026-08-30 | The mutation boundary moves separately, in P2b, with the confirm-token flow |
| The agent declares the verbs it supports | 2026-08-30 | The agent is a hand-installed file and will drift. Version arithmetic strands a peer; a claimed list degrades one feature |
| The agent returns raw; the manager parses | 2026-08-30 | A parse bug in the agent costs twenty paste sessions. In the manager it costs one file patch |

Inherited from the plan and NOT reopened: SSH with a forced command as the
transport (§4.2), `agent-exec` as the only entry point, the verb allowlist,
per-node key separation so read and act revoke independently (§8).

## Preconditions — verify on Raven and Golem BEFORE writing code

Each can invalidate a real part of this design. This repo's record is that the
box rewrites the design, and three of the last five defects were things no
off-box test could have known.

1. **Where does a persistent `authorized_keys` live on 7.3.2, and does a
   `command="..."` entry survive a reboot?** The whole peer footprint rests on
   this. If Unraid regenerates the file at boot from a different source, the
   installer must write to that source instead.
2. **Does `smartctl` support `--json` on 7.3.2?** If not, the agent returns raw
   text and the manager parses it — the split holds either way, but the parser
   is a different size of problem.
3. **What can `pool.balance` honestly report for ZFS versus btrfs?** Golem has
   both plus a 4-member pool, which is the real test. If a profile cannot be
   read for one of them, the verb reports `unknown` for that pool and says so —
   it never reports a profile it inferred.

## Constraints from live verification (binding)

- **`/usr/local/emhttp` is tmpfs.** Anything the agent needs must live on flash.
- **Python 3.11 is in the Unraid base image**, stdlib only — same floor as the
  daemon. No pip on the peer, ever.
- **A stopped API answers `error`, not `unknown`;** a 504 answers `unknown`.
  Neither of the two commonest failures is the status a reader would guess, and
  the agent path must not invent a third convention.
- **The `Query.disks` 504 is routine on both boxes**, not one sick host. Any
  agent verb that duplicates a slow GraphQL read must justify itself.

## 1. Domain model — a Tier 1 read is a domain with a different transport

`Domain` gains two fields: `transport` (`graphql` | `agent`) and `min_tier`.
`domains_for_lane(lane, tier)` filters on both. `run_cycle` picks `post_fn` or
`exec_fn` from `domain.transport` and builds the same `collector.Result`.

Everything downstream is untouched and inherited: per-domain error isolation,
`upsert_state` retaining the last good payload across a failure, the staleness
labelling, the Disks tab's `stale` list, and the `unknown`/`error` distinction.

`tier` already exists on `nodes`, is synced by `sync_registry`, and is read by
nothing — `api/nodes.php` writes a hard-coded `0` at enrollment. This phase is
the first reader.

**A Tier 0 node simply has fewer domains.** That is the plan's "degrade
gracefully to Tier 0" rule (§4.1) satisfied structurally rather than by a check
in each feature.

## 2. Transport — `daemon/agentclient.py`

Mirrors `gqlclient.py`, including the seam that makes it testable:

- **`exec_agent(node, verb, args, timeout)`** builds the `ssh` argv and runs it
  through `subprocess` with a timeout. **argv list, never a string, never a
  shell.**
- **`exec_response(returncode, stdout, stderr)` is pure.** Classification is
  testable on Windows with no `ssh` binary, exactly as `parse_response` is
  testable with no socket.
- `exec_fn` is injected into the Manager the way `post_fn` is, so the entire
  agent path runs off fixtures in the suite.

SSH options: `-i <keyfile> -o BatchMode=yes -o ConnectTimeout=<n>
-o StrictHostKeyChecking=yes -o UserKnownHostsFile=<flash>/known_hosts`,
`-o PasswordAuthentication=no`, no PTY, no agent forwarding.

**Host keys are trust-on-first-use, made visible.** `known_hosts` lives on flash,
one entry per node, written at enrollment. The installer's last line prints the
peer's own host fingerprint; the manager shows the fingerprint it recorded. The
operator compares two strings on a screen they are already looking at.

## 3. The agent — `scripts/agent-exec`

Stateless. Writes nothing, creates nothing, keeps no state between invocations.
Stdlib Python 3.

**Protocol.** sshd invokes it as the forced command. One JSON envelope in on
stdin, one JSON object out on stdout:

```json
in:   {"verb": "smart.attributes", "args": {"device": "/dev/sdb"}}
out:  {"ok": true,  "verb": "smart.attributes", "data": {}}
out:  {"ok": false, "verb": "smart.attributes", "error": "...", "code": "UNKNOWN_VERB"}
```

**Stdin, not argv.** With a forced command, whatever the client typed arrives in
`$SSH_ORIGINAL_COMMAND` as an attacker-shaped string. Reading the body on stdin
means that variable is never parsed, never split, never expanded. It is ignored.

**The allowlist is the whole authorization model.** One dict: verb → (argument
schema, handler). An unknown verb exits non-zero with no side effect. Arguments
are validated before any subprocess contact. Every subprocess call is an argv
list.

**Arguments validate against reality, not a pattern.** `^/dev/sd[a-z]+$` still
admits a device that is not there, and hangs. The agent enumerates its own block
devices and refuses anything outside that set: the question is "is this one of
mine", which does not have a clever-input case.

**The agent returns raw.** `smart.attributes` shells `smartctl --json` and passes
the result through untouched. Parsing lives in the manager, where fixing it is
one file patch, and where the existing pure-`parse_*` convention and the
fixture corpus from `capture_fixtures.py` already are.

### Verb table — P2a

| Verb | Runs | Returns | Closes |
|---|---|---|---|
| `agent.hello` | nothing | version, hostname, **the verb list this agent supports** | version skew |
| `smart.attributes` | `smartctl --json` per device | `{device: <that JSON, raw>}` | M4's verdict chain — Tier 0 gives only `OK\|UNKNOWN` |
| `mounts.list` | reads `/proc/mounts` | raw text | the input P2b's watchdog needs |
| `pool.balance` | `btrfs filesystem usage` / `zpool status` | raw text, per pool | the pool profile/redundancy gap in `tier0-coverage.md` |

**One call per verb per node per cycle, never one per disk.** `smart.attributes`
takes an optional device list and defaults to every device the agent enumerates,
returning a map keyed by device. Golem has 22 disks: a per-device verb would open
22 SSH connections every slow cycle against a box whose disk lane already 504s
routinely. The reply is larger; the connection count is what matters.

**No verb in this table writes anything.** A defect in P2a still only misreports
a box. That property is the reason for the split and must hold at review.

## 4. Enrollment

One flow on the existing node settings, not a new screen.

1. Operator enables Tier 1 for a node.
2. Manager generates an ed25519 pair (`ssh-keygen`, argv list) and stores the
   private half at `keys/<node_id>.ssh`, `0600` — beside the GraphQL key, one
   per node, so revocation is per peer and read/act revoke independently.
3. Manager displays one installer command: fetch `agent-exec` to
   `/boot/config/plugins/unraid-manager/`, `chmod 700`, append the
   `command="...",no-pty,no-port-forwarding,no-agent-forwarding` line to the
   peer's persistent `authorized_keys`, and print the peer's host fingerprint.
4. Operator pastes it on the peer.
5. **Test connection** runs `agent.hello`. On a real reply the manager stores
   `tier=1`, the agent version and the claimed verb list, and logs an event.

Nothing is persisted until a real reply comes back. There is no state that says
"probably Tier 1".

## 5. Three failures that must not look alike

The absent-vs-empty family, which this repo has now shipped four times.

| Situation | Result | What the operator sees |
|---|---|---|
| Agent did not answer | domain `unknown`, node **stays Tier 1** | "agent unreachable" |
| Agent answered, verb not in its claimed list | not an error | "needs a newer agent" |
| Agent answered, the command failed | domain `error` with the reason | the reason |

**Never auto-downgrade a node to Tier 0.** A silent downgrade drops SMART
verdicts while every card still looks healthy, which is the worst shape a defect
can take here — the pane reports success and the data quietly stops arriving.

## 6. Journal

The plan's append-only action journal (§8) arrives in **P2b, with the first
mutating verb**, which is what it is for. Journaling every SMART poll would be
20 nodes × 6/hour of noise burying the thing the journal exists to show.

P2a uses the existing `events` table via `log_event`: enrollment, agent
unreachable, and a refused verb.

## 7. Testing

- `exec_fn` fakes over fixtures captured from Golem and Raven with the existing
  `capture_fixtures.py`. No test touches a network or an `ssh` binary.
- `exec_response` pure-classifier tests, including a timeout, a non-zero exit,
  garbage on stdout, and an empty reply.
- `agent-exec` is exercised directly in the suite (loaded by path — it has no
  `.py` extension because sshd runs it): allowlist enforcement, argument
  rejection, unknown verb, and that a rejected call produces no subprocess.
- **The existing structural assertion that no mutation string appears in the
  domain table is extended to the verb table.** In P2a it must hold on both.
- Mutation-test the classifier and the allowlist, per this repo's habit.

## 8. Out of scope for P2a

No mutating verb. No watchdog. No Links screen. No dry-run modal. No assisted
provisioning. No job queue. No extended collectors beyond the four verbs above.
Nothing asynchronous — a read verb answers or it times out.

## 9. Hardware exit criterion

All on Golem, from Raven:

1. `agent.hello` returns a version and a verb list.
2. `smart.attributes` returns real data for a real disk, and the Disks tab shows
   something Tier 0 could not.
3. A verb **not** in the table is refused, with no side effect.
4. A device argument naming a device that is not there is refused.
5. **It all still works after the peer reboots** — precondition 1, proven rather
   than assumed.

## 10. Risks

| Risk | Handling |
|---|---|
| `authorized_keys` does not persist as assumed | Precondition 1. Verified before code, not after |
| The agent drifts across twenty peers | The claimed verb list. One feature degrades, the node keeps working |
| SSH is disabled or root login refused on a peer | That node stays Tier 0. Every feature must already degrade |
| A future verb writes without meaning to | The structural no-mutation assertion over the verb table, plus P2b's confirm-token flow being a separate, reviewed change |
| Scope creep back into M5 | §8. The watchdog needs `mounts.list` and nothing else from this phase |
