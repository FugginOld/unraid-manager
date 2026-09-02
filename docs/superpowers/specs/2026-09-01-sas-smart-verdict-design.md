# SAS SMART Verdict — Design

**Date:** 2026-09-01
**Phase:** follow-on to P2a (the Tier 1 agent)
**Status:** approved in chat 2026-09-01, pending written review

## Goal

Turn the `smart` domain that P2a already collects into a per-disk verdict the
operator can act on, and show it in the Disks pane. Today `api/disks.php` reads
only `('disks','array')`, so the entire Tier 1 payload is collected, stored, and
never seen. P2a is correct but invisible.

## Non-goals

- A full per-disk SMART detail screen. The verdict plus its reasons is the
  deliverable; every counter the agent captured is a later, larger job.
- ATA drives. The fleet is all SAS. The chain reads SCSI fields only, and an ATA
  doc arriving in future returns `UNKNOWN` rather than a wrong answer.
- Alerting or notification on a `FAIL`. The verdict is computed and stored so
  that an alert can later read it; wiring the alert is not this work.
- Changing what the agent collects. `smart.attributes` is unchanged.

## Background: what the hardware actually reports

Two devices captured from Golem on 2026-08-30, in
`tests/python/fixtures/agent-smart-golem-sda.json` and `-sdb.json`:

| field | sda (HITACHI H0H72121CLAR12T0) | sdb (SEAGATE ST14000NM002G) |
| --- | --- | --- |
| `smart_status.passed` | true | true |
| `scsi_grown_defect_list` | 0 | 0 |
| `scsi_pending_defects` | **absent** | `{count: 0}` |
| uncorrected read/write/verify | 0 / 0 / 0 | 0 / 0 / 0 |
| corrected read errors | 1 (of 17,636,562 ECC invocations) | 0 |
| `power_on_time.hours` | 55,161 | 28,682 |
| most recent self-test | Completed @ 33,845 h | Completed @ 22,992 h |
| `temperature` | `{current: 37, drive_trip: 60}` | `{current: 40, drive_trip: 60}` |
| `scsi_self_test_N` entries | 0 through 19 | 0 only |

Three facts drive the design:

1. **`scsi_pending_defects` is absent on sda and present on sdb.** The set of
   reported fields varies per drive model. A chain that reads a missing key as
   zero reports health it never measured.
2. **sda's most recent self-test ran 21,316 hours before its current power-on
   hour count** — about 2.4 years. Its `Completed` result is true and
   uninformative.
3. **The drive supplies its own trip temperature.** `temperature.drive_trip` is
   60 on both, so the temperature rule needs no configured threshold; the drive
   already knows its limit.

## Architecture

```text
agent-exec (peer)  ->  smart.attributes: {device: raw smartctl JSON}
        |
collector.parse_smart  ->  calls smart.verdict(doc) per device
        |                  stores verdict + reasons + summary, NOT the raw doc
store.node_state (domain 'smart')
        |
api/disks.php  ->  three-way join on device basename: disks + array + smart
        |
frontend/src/views/Disks.vue  ->  verdict column, reasons on expand
```

### New module: `daemon/smart.py`

One pure function, no I/O, no exceptions escaping:

```python
def verdict(doc):
    """doc is one device's parsed smartctl JSON, or None if unreadable.

    Returns {'verdict': 'OK'|'WATCH'|'FAIL'|'UNKNOWN',
             'reasons': [str, ...],
             'summary': {...}}
    """
```

It lives in its own module rather than in `collector.py` (already 495 lines) or
`health.py` (fleet-level health, not per-device). It has one responsibility and
one test file, and it can be reasoned about without reading the collector.

## The verdict chain

Every rule that fires appends its text to `reasons`. The verdict is `FAIL` if
any FAIL rule fired, else `WATCH` if any WATCH rule fired, else `OK` or
`UNKNOWN` per the rules below. `reasons` is ordered FAIL entries first, then
WATCH, then advisories, so `reasons[0]` is always the deciding one and the
operator still sees everything else that is notable.

### FAIL

| # | Condition | Reason text |
| --- | --- | --- |
| 1 | `smart_status.passed` is `false` | the drive reports SMART failure |
| 2 | any of read/write/verify `total_uncorrected_errors` > 0 | N uncorrected {read,write,verify} errors |
| 3 | `scsi_self_test_0.result.value` in 3..7 | last self-test failed: {result string} |
| 4 | `temperature.current` >= `temperature.drive_trip` | at the drive's own trip point ({trip} C) |

Rule 2 counts uncorrected errors only. An uncorrected error means the data did
not come back.

Rule 3 uses SCSI self-test result codes: 0 is completed without error, 1 and 2
are aborts by host and by other, 3 through 7 are failures, and 15 is a test in
progress. Only 3 through 7 are failures. An abort is not a failing drive, and a
test in progress is not a result.

### WATCH

| # | Condition | Reason text |
| --- | --- | --- |
| 5 | `scsi_grown_defect_list` > 0 | N grown defects |
| 6 | `scsi_pending_defects.count` > 0 | N sectors pending reallocation |
| 7 | any of read/write/verify `errors_corrected_by_rereads_rewrites` > 0 | N {read,write,verify} operations needed a retry |
| 8 | `temperature.current` >= `temperature.drive_trip` - 5 | within 5 C of the drive's trip point |

Rule 7 deliberately reads `errors_corrected_by_rereads_rewrites` and **not**
`total_errors_corrected`. A reread means the first attempt failed, which is a
signal. sda's single `total_errors_corrected` is an `errors_corrected_by_eccdelayed`
out of 17.6 million ECC invocations — a drive doing exactly what ECC is for.
Counting it would put the whole fleet on WATCH for nothing.

### UNKNOWN, and the absent-field invariant

**`OK` is returned only when a positive signal was actually present.** It is
never the result of finding no negative ones.

- `doc` is `None` (smartctl could not read the device, or returned empty) ->
  `UNKNOWN`, reason `smartctl could not read this device`.
- `smart_status` absent from the doc -> `UNKNOWN`, reason `no SMART status
  reported`. Not `OK`.
- An individual rule input absent -> that rule is skipped, and its absence is
  named in `reasons` (sda reports no `scsi_pending_defects` at all, so it gets
  `pending defect count not reported`). It is never read as zero.
- `doc` present but carrying no SCSI structures at all (an ATA drive, or a
  device type the chain does not understand) -> `UNKNOWN`, reason `not a SAS
  drive: no SCSI SMART data`.

### Self-test age is an advisory, not a verdict

When `scsi_self_test_0.power_on_time.hours` is present and the current
`power_on_time.hours` exceeds it by more than 2160 hours (90 days of continuous
running), `reasons` gains `last self-test {N} h ago` — but the verdict does not
move.

Rationale: if the fleet does not schedule SAS self-tests, every drive trips this
rule, and a pane where every row reads WATCH conveys nothing. The age of a test
is a fact about monitoring hygiene, not about the drive. A self-test *failure*
(rule 3) is a different thing and does set FAIL.

This is a judgement, recorded so it can be reversed deliberately. Reversing it
means moving the condition into the WATCH table.

### Result on the captured fixtures

Both devices return `OK`. sda additionally carries the advisory reasons
`last self-test 21316 h ago` and `pending defect count not reported`. Reason
text uses no thousands separator, so the strings are locale-free and pinnable
in a test exactly as written.

Because both real fixtures are healthy, the fixtures alone cannot prove any rule
fires. See Testing.

## Stored payload

`parse_smart` today stores the entire smartctl document per device: roughly 8 KB
across 37 devices, about 300 KB per node per poll, none of which any consumer
reads. It will instead store:

```python
{'count': N,
 'disks': {device: {'verdict': 'OK',
                    'reasons': ['last self-test 21316 h ago',
                                'pending defect count not reported'],
                    'summary': {'model': 'HITACHI H0H72121CLAR12T0',
                                'power_on_hours': 55161,
                                'temperature': 37,
                                'trip_temperature': 60,
                                'grown_defects': 0,
                                'pending_defects': None,
                                'uncorrected': {'read': 0, 'write': 0, 'verify': 0},
                                'rereads': {'read': 0, 'write': 0, 'verify': 0},
                                'self_test_result': 'Completed',
                                'self_test_hours': 33845}}}}
```

About 5 KB per node. `None` in `summary` means the drive did not report the
field, and is distinct from `0`.

The serial-stripping in `parse_smart` stays exactly as it is: `serial_number`
and `logical_unit_id` are removed before anything else touches the doc, and
`summary` never carries either. Plan section 12 forbids a raw serial in an
API-bound payload and this payload is API-bound.

Trade-off accepted: a future full-detail view will need a fresh poll (13 s)
rather than reading a stored document. Nothing consumes the raw doc today.

## API

`api/disks.php` extends its domain query from `('disks','array')` to
`('disks','array','smart')` and joins the third payload on the same device
basename `um_device_key()` already produces. Each disk row gains:

- `verdict` — the string, or `null` when there is no smart payload for this disk
- `reasons` — the list, or `[]`
- `smart_tier` — the node's tier, `0` or `1`

**`smart_tier` comes from the `nodes` table's `tier` column, never from whether
a payload happens to exist.** The node query becomes
`SELECT id, name, tier FROM nodes ORDER BY name`. Deriving the tier from payload
presence would label a Tier 1 node that has not been polled yet as Tier 0 —
absence of data read as a fact about the node, the exact defect this repo keeps
closing.

The three cases are therefore distinct:

| node tier | smart payload | result |
| --- | --- | --- |
| 0 | none, correctly (`min_tier=1` means the domain never runs) | `smart_tier: 0`, `verdict: null`. Not an error, no `stale` entry. |
| 1 | present | `smart_tier: 1`, the verdict. |
| 1 | absent — enrolled but not yet polled | `smart_tier: 1`, `verdict: null`, **and a `stale` entry** reading `no SMART poll recorded yet`, matching how a missing `disks` payload is already handled. |

The `smart` domain has its own `fetched_at`, distinct from the `disks` domain's.
Where the two disagree the row keeps `fetched_at` from `disks` (it is the row's
primary source) and carries `smart_fetched_at` separately, for the same reason
the orphan rows carry the array timestamp rather than the disks one: stamping a
value with another domain's clock misreports its age.

A `smart` domain whose status is not `ok` goes in `stale` with its own message,
alongside the existing `disks` entries.

## Frontend

The existing SMART column becomes the verdict column.

- **Tier 1 rows:** `OK` / `WATCH` / `FAIL` / `UNKNOWN`, styled with the existing
  `um-ok`, `um-warn`, `um-unknown` classes. Clicking the row reveals a details
  row listing `reasons`, in the table itself; no new screen.
- **Tier 0 rows:** `OK (limited)` / `UNKNOWN (limited)`, derived from the
  existing Unraid `smart_status`. The `(limited)` suffix exists so a Tier 0 `OK`
  can never be read as an assessed one.
- **Tier 1 rows with no verdict yet** (enrolled, not yet polled) render `—`, not
  `(limited)`: the node is capable of an assessment and has not produced one.
  The `stale` line above the table is what explains it.
- **Orphan rows** (`model === null`, an array slot with no disk behind it) keep
  today's `no disk` handling untouched. It is a different fact and already right.
- The standing hint about needing a Tier 1 agent is shown only while at least one
  node in the payload is Tier 0.
- Filter buttons follow the verdict states.

## Error handling

- `smart.verdict` never raises. A doc of any shape returns a verdict; an
  unparseable one returns `UNKNOWN` with a reason rather than propagating.
- `parse_smart` keeps its existing contract: a device that could not be read
  keeps its key with a null-equivalent value, so a dead disk is visibly dead
  rather than absent.
- `api/disks.php` treats a missing or non-array smart payload as Tier 0, not as
  an error.

## Testing

**The two real fixtures are both healthy, so they cannot prove a single rule
fires.** Every rule therefore gets a synthetic case built by mutating one field
of a real fixture, so the case is a real drive with one thing wrong rather than
an invented document.

- One test per rule (1 through 8), each flipping exactly one field and asserting
  both the verdict and the reason text.
- Order tests: a doc that trips both a FAIL and a WATCH rule returns FAIL, with
  the FAIL reason first.
- Absent-field tests: `smart_status` absent -> `UNKNOWN` not `OK`; absent
  `scsi_grown_defect_list` -> skipped and named, not zero; `doc = None` ->
  `UNKNOWN`; an ATA-shaped doc -> `UNKNOWN`.
- Both real fixtures -> `OK`, with sda's two advisory reasons asserted exactly.
- PHP: the three-way join; a Tier 0 node yielding `smart_tier: 0` with no stale
  entry; a **Tier 1 node with no smart payload yielding `smart_tier: 1` and a
  stale entry** — the case that proves the tier is read from the `nodes` table
  and not inferred from payload presence; a `smart` domain in error yielding a
  stale entry; and `smart_fetched_at` distinct from `fetched_at`.
- JS: the verdict column renders each state, the `(limited)` suffix appears only
  on Tier 0 rows, an unpolled Tier 1 row renders `—` rather than `(limited)`, the
  expand shows reasons, and the filters select correctly.

Run the Python, PHP and JS suites on every task. Three PHP pins read Python
sources, so a Python-only change can turn the PHP suite red.

## Hardware verification

Desk work through implementation; the fixtures cover every rule. One hardware
pass at the end, on the live pane:

1. Golem's disks show a verdict, and at least one row's reasons expand.
2. Raven's disks show `(limited)` and no verdict.
3. The stored smart payload is the summary shape, not the raw document.

## Exit criteria

1. `daemon/smart.py` exists, is pure, and every rule has a test that proves it
   can fire.
2. `OK` is unreachable without a present `smart_status`, proven by test.
3. The Disks pane shows verdicts for Tier 1 nodes and labelled limited status for
   Tier 0 nodes, verified on the live pane.
4. Python, PHP and JS suites green.
5. The stored payload is the summary shape and carries no serial.
