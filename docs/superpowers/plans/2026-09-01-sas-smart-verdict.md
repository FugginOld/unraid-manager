# SAS SMART Verdict Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `smart` domain the Tier 1 agent already collects into a per-disk verdict, and show it in the Disks pane.

**Architecture:** A new pure module `daemon/smart.py` flattens one smartctl document to a summary in which `None` means "the drive did not report this", then judges that summary against an ordered rule chain. `collector.parse_smart` stores the verdict instead of the raw document, `api/disks.php` joins it into the existing disks/array join, and `Disks.vue` renders it in the column that today shows Unraid's OK/UNKNOWN.

**Tech Stack:** Python 3.11 (stdlib only), PHP 8.2, Vue 3. Tests: `unittest`, hand-rolled PHP `check()` harnesses, Node SSR/happy-dom harnesses under `tests/js/`.

**Spec:** `docs/superpowers/specs/2026-09-01-sas-smart-verdict-design.md`

## Global Constraints

- **Python is stdlib-only.** Nothing under `daemon/` may import a third-party package.
- **Write LF, always.** In Python, `open(path, 'w', newline='')`. Never let a tool rewrite line endings.
- **No serial, ever.** `serial_number` and `logical_unit_id` are stripped in `parse_smart` before anything else touches the document, and never appear in `summary`. This payload is API-bound.
- **`OK` requires a present `smart_status`.** It is never the result of finding no negative signal. Every task that touches the chain preserves this.
- **Absent is not zero.** `summarize()` is the only place a missing key becomes `None`; every rule tests `is not None` (or truthiness, where `0` and `None` are both correctly skipped) before comparing.
- **Fixtures are evidence.** `tests/python/fixtures/agent-smart-golem-sda.json` and `-sdb.json` are captures from real hardware. Never edit them. Tests mutate an in-memory copy.
- **A rule with no test that makes it fire is not a rule.** Both real fixtures are healthy, so every rule needs a synthetic case built by mutating one field of a real capture.
- **Run BOTH suites on EVERY task, even one that changes no PHP.** Three PHP pins read Python sources and that has turned the suite red once already:

  ```bash
  python -m unittest discover -s tests/python
  export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
  bash tests/php/run.sh
  ```

  `tests/php/run.sh` also runs the `tests/js/*.mjs` harnesses. PHP 8.2.33 is installed on this machine; it is only absent from the default bash `PATH`.
- **Work on `dev`. Never push to `main`.**
- **No network, no live box, in any test.** Fixtures only.

## File Structure

| File | Responsibility |
| --- | --- |
| `source/usr/local/emhttp/plugins/unraid-manager/daemon/smart.py` | **New.** Pure. `summarize()` flattens a smartctl doc; `verdict()` judges it. No I/O, no exceptions escaping. |
| `tests/python/test_smart_summarize.py` | **New.** The reading layer and the three UNKNOWN paths. |
| `tests/python/test_smart_rules.py` | **New.** One test per rule, each mutating one field of a real capture. |
| `daemon/collector.py` (`parse_smart`, ~line 389) | Calls `smart.verdict()` per device; stores the verdict, not the raw document. |
| `tests/python/test_collector_agent.py` | Existing assertions updated to the new payload shape. |
| `api/disks.php` | Three-way join: `disks` + `array` + `smart`. Reads `tier` from the `nodes` table. |
| `tests/php/disks_test.php` | The join, the three tier cases, the `domain` field on stale entries. |
| `frontend/src/views/Disks.vue` | Verdict column, `(limited)` label for Tier 0, reasons on expand, per-domain stale copy. |
| `tests/js/views.mjs`, `tests/js/interact.mjs` | What the column renders, and what the expand does when clicked. |

Six tasks. Tasks 1-2 build and prove the chain against fixtures; Task 3 wires it into collection; Tasks 4-5 carry it to the screen; Task 6 documents it and verifies on hardware.

---

### Task 1: The reading layer — `summarize()` and the UNKNOWN paths

**Files:**
- Create: `source/usr/local/emhttp/plugins/unraid-manager/daemon/smart.py`
- Test: `tests/python/test_smart_summarize.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `smart.summarize(doc) -> dict` with the exact 12 keys listed in Step 3; `smart.verdict(doc) -> {'verdict': str, 'reasons': [str], 'summary': dict}`; module constants `LANES`, `SELF_TEST_FAILURES`, `SELF_TEST_STALE_HOURS`, `TRIP_MARGIN_C`. Task 2 adds rules to `verdict()`; Task 3 calls `verdict()`.

**Why this is its own task:** the absent-versus-zero invariant is enforceable only if exactly one function decides it. A reviewer can accept this reading layer and reject the rules built on it, or the reverse.

- [ ] **Step 1: Write the failing tests**

Create `tests/python/test_smart_summarize.py`:

```python
"""summarize() is the one place a missing key becomes None, and the three
paths on which a verdict is refused rather than guessed."""
import unittest

import context
import smart


class TestSummarize(unittest.TestCase):
    def setUp(self):
        self.sda = context.fixture_json('agent-smart-golem-sda.json')
        self.sdb = context.fixture_json('agent-smart-golem-sdb.json')

    def test_it_reads_the_real_hitachi(self):
        s = smart.summarize(self.sda)
        self.assertIs(True, s['passed'])
        self.assertEqual(0, s['grown_defects'])
        self.assertEqual(55161, s['power_on_hours'])
        self.assertEqual(37, s['temperature'])
        self.assertEqual(60, s['trip_temperature'])
        self.assertEqual('Completed', s['self_test_result'])
        self.assertEqual(0, s['self_test_value'])
        self.assertEqual(33845, s['self_test_hours'])

    def test_an_unreported_field_is_none_and_never_zero(self):
        # sda carries no scsi_pending_defects at all; sdb reports {'count': 0}.
        # Those are different facts. A summary that collapsed them would report
        # health that was never measured - this assertion is the whole reason
        # summarize() exists as a separate function.
        self.assertIsNone(smart.summarize(self.sda)['pending_defects'])
        self.assertEqual(0, smart.summarize(self.sdb)['pending_defects'])

    def test_every_lane_is_read_separately(self):
        s = smart.summarize(self.sda)
        self.assertEqual({'read': 0, 'write': 0, 'verify': 0}, s['uncorrected'])
        self.assertEqual({'read': 0, 'write': 0, 'verify': 0}, s['rereads'])

    def test_a_lane_the_drive_omits_is_none_not_zero(self):
        del self.sda['scsi_error_counter_log']['verify']
        self.assertEqual({'read': 0, 'write': 0, 'verify': None},
                         smart.summarize(self.sda)['uncorrected'])

    def test_the_summary_is_exactly_these_twelve_keys(self):
        # The summary is an allow-list, so no raw smartctl key can transit into
        # an API-bound payload by being added upstream - not a serial, and not
        # total_errors_corrected, of which sda carries 1 out of 17,636,562 ECC
        # invocations. A rule written against that count by accident would put
        # the whole fleet on WATCH for drives doing exactly what ECC is for.
        #
        # Asserted as a key SET, not as assertNotIn('corrected', ...): that
        # earlier form tested for a key literally named 'corrected', which no
        # implementation would ever emit, and passed unchanged when
        # total_errors_corrected was injected into the summary.
        self.assertEqual(
            {'model', 'passed', 'power_on_hours', 'temperature',
             'trip_temperature', 'grown_defects', 'pending_defects',
             'uncorrected', 'rereads', 'self_test_result', 'self_test_value',
             'self_test_hours'},
            set(smart.summarize(self.sda)))

    def test_a_none_doc_summarises_to_all_none(self):
        s = smart.summarize(None)
        self.assertIsNone(s['passed'])
        self.assertIsNone(s['grown_defects'])
        self.assertEqual({'read': None, 'write': None, 'verify': None},
                         s['uncorrected'])


class TestUnknownPaths(unittest.TestCase):
    def test_an_unreadable_device_is_unknown(self):
        got = smart.verdict(None)
        self.assertEqual('UNKNOWN', got['verdict'])
        self.assertEqual(['smartctl could not read this device'], got['reasons'])

    def test_an_empty_string_is_the_same_fact_as_none(self):
        self.assertEqual('UNKNOWN', smart.verdict('')['verdict'])

    def test_an_ata_drive_is_unknown_not_ok(self):
        # Checked BEFORE smart_status, which an ATA drive does report. In the
        # reverse order an ATA document would reach the rules, trip none of
        # them (every rule reads a scsi_ structure) and come back OK - a
        # verdict returned from zero evidence.
        got = smart.verdict({'smart_status': {'passed': True},
                             'ata_smart_attributes': {'table': []}})
        self.assertEqual('UNKNOWN', got['verdict'])
        self.assertEqual(['not a SAS drive: no SCSI SMART data'], got['reasons'])

    def test_a_scsi_doc_with_no_smart_status_is_unknown_not_ok(self):
        # The invariant. OK requires a positive signal; it is never what is
        # left over when nothing negative was found.
        doc = context.fixture_json('agent-smart-golem-sda.json')
        del doc['smart_status']
        got = smart.verdict(doc)
        self.assertEqual('UNKNOWN', got['verdict'])
        self.assertEqual(['no SMART status reported'], got['reasons'])

    def test_the_summary_survives_an_unknown_verdict(self):
        # A drive we could not judge still shows its model and hours in the
        # pane. UNKNOWN is a refusal to judge, not a refusal to report.
        doc = context.fixture_json('agent-smart-golem-sda.json')
        del doc['smart_status']
        got = smart.verdict(doc)
        self.assertEqual(55161, got['summary']['power_on_hours'])
        self.assertEqual('HITACHI H0H72121CLAR12T0', got['summary']['model'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest discover -s tests/python -p "test_smart_summarize.py" -v`

Expected: every test errors with `ModuleNotFoundError: No module named 'smart'`.

- [ ] **Step 3: Write the implementation**

Create `source/usr/local/emhttp/plugins/unraid-manager/daemon/smart.py`:

```python
"""Judge one SAS disk from the smartctl document the Tier 1 agent captured.

The fleet is all SAS. There is no ata_smart_attributes table anywhere in it,
so every rule here reads a SCSI structure, and an ATA document returns UNKNOWN
rather than a wrong answer.

The invariant this module exists to hold: OK is returned only when a positive
signal - smart_status - was actually present. OK is never what is left over
when no negative signal was found. summarize() is the single place a missing
key becomes None; the rules then read a flat summary in which None already
means "the drive did not report this", so no rule can reintroduce the defect
by reaching for a raw key with a default.
"""

LANES = ('read', 'write', 'verify')

# SCSI self-test result codes: 0 completed without error, 1 aborted by host,
# 2 aborted by another initiator, 3-7 failures, 15 in progress. An abort is
# not a failing drive, and a running test is not a result.
SELF_TEST_FAILURES = (3, 4, 5, 6, 7)

# 90 days of continuous running. Advisory only - see the module docstring of
# _advisories() for why this never moves a verdict.
SELF_TEST_STALE_HOURS = 2160

# The drive reports its own trip point, so this is a margin, not a threshold.
TRIP_MARGIN_C = 5


def _dig(doc, *keys):
    """Walk nested dicts, returning None the moment anything is missing.

    Missing and null collapse to the same None deliberately: smartctl omits a
    structure it has nothing for, and both readings mean "not reported".
    """
    node = doc
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _lanes(doc, field):
    """One value per SCSI lane, None for a lane the drive did not report."""
    return {lane: _dig(doc, 'scsi_error_counter_log', lane, field)
            for lane in LANES}


def summarize(doc):
    """Flatten one smartctl doc to exactly the fields the rules read.

    Every value is None where the drive did not report it. sda reports no
    scsi_pending_defects at all while sdb reports {'count': 0}; those are
    different facts and this is the only function that decides so.

    total_errors_corrected is deliberately absent from the summary: sda carries
    1 of them out of 17,636,562 ECC invocations, which is a drive doing exactly
    what ECC is for. Leaving it out means no rule can be written against it by
    accident.
    """
    doc = doc if isinstance(doc, dict) else {}
    return {
        'model': doc.get('model_name'),
        'passed': _dig(doc, 'smart_status', 'passed'),
        'power_on_hours': _dig(doc, 'power_on_time', 'hours'),
        'temperature': _dig(doc, 'temperature', 'current'),
        'trip_temperature': _dig(doc, 'temperature', 'drive_trip'),
        'grown_defects': doc.get('scsi_grown_defect_list'),
        'pending_defects': _dig(doc, 'scsi_pending_defects', 'count'),
        'uncorrected': _lanes(doc, 'total_uncorrected_errors'),
        'rereads': _lanes(doc, 'errors_corrected_by_rereads_rewrites'),
        'self_test_result': _dig(doc, 'scsi_self_test_0', 'result', 'string'),
        'self_test_value': _dig(doc, 'scsi_self_test_0', 'result', 'value'),
        'self_test_hours': _dig(doc, 'scsi_self_test_0', 'power_on_time', 'hours'),
    }


def _result(state, reasons, summary):
    return {'verdict': state, 'reasons': reasons, 'summary': summary}


def _is_scsi(doc):
    """Does this document carry any SCSI structure at all?

    Every rule reads a scsi_ key. A document with none of them cannot be
    judged by this chain, whatever else it contains.
    """
    return any(str(key).startswith('scsi_') for key in doc)


def verdict(doc):
    """doc is one device's parsed smartctl JSON, or None if unreadable.

    Returns {'verdict': 'OK'|'WATCH'|'FAIL'|'UNKNOWN',
             'reasons': [str, ...],
             'summary': {...}}

    Never raises. A document of any shape gets a verdict; UNKNOWN is the
    refusal to judge, and it still carries the summary so the pane can show
    the model and the hours of a drive nothing could be concluded about.
    """
    summary = summarize(doc)
    if not isinstance(doc, dict):
        return _result('UNKNOWN', ['smartctl could not read this device'], summary)
    if not _is_scsi(doc):
        # Before the smart_status check, never after: an ATA drive DOES report
        # smart_status, so the reverse order lets an ATA document through to
        # rules that all read scsi_ structures, trip none of them, and return
        # OK from no evidence whatsoever.
        return _result('UNKNOWN', ['not a SAS drive: no SCSI SMART data'], summary)
    if summary['passed'] is None:
        return _result('UNKNOWN', ['no SMART status reported'], summary)
    return _result('OK', [], summary)
```

The final `return` is a placeholder for the shape only — Task 2 replaces it with the rule chain. Nothing between here and Task 2 depends on a healthy document's verdict, and no test in this task asserts one.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest discover -s tests/python -p "test_smart_summarize.py" -v`
Expected: 12 tests, all PASS.

- [ ] **Step 5: Run both full suites**

```bash
python -m unittest discover -s tests/python
export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash tests/php/run.sh
```

Expected: Python all pass; `php suite: all pass`. A new file under `daemon/` can trip the PHP policy pins that read Python sources — if `policy_test.php` fails, read what it pins before changing anything.

- [ ] **Step 6: Commit**

```bash
git add source/usr/local/emhttp/plugins/unraid-manager/daemon/smart.py tests/python/test_smart_summarize.py
git commit -m "feat(smart): one place where a missing key becomes None"
```

---

### Task 2: The rule chain

**Files:**
- Modify: `source/usr/local/emhttp/plugins/unraid-manager/daemon/smart.py` (replace the placeholder `return` in `verdict()`, add three rule functions)
- Test: `tests/python/test_smart_rules.py`

**Interfaces:**
- Consumes: `smart.summarize(doc)`, `smart._result()`, `LANES`, `SELF_TEST_FAILURES`, `SELF_TEST_STALE_HOURS`, `TRIP_MARGIN_C` from Task 1.
- Produces: `verdict()` returning `FAIL`/`WATCH`/`OK` with the exact reason strings below. Task 4's PHP and Task 5's Vue render these strings verbatim; do not reword them.

**The reason strings are an interface.** Written exactly:

| | string |
| --- | --- |
| FAIL 1 | `the drive reports SMART failure` |
| FAIL 2 | `uncorrected {lane} errors: {n}` |
| FAIL 3 | `last self-test failed: {result string}` |
| FAIL 4 | `at the drive's own trip point ({trip} C)` |
| WATCH 5 | `grown defects: {n}` |
| WATCH 6 | `sectors pending reallocation: {n}` |
| WATCH 7 | `{lane} operations needing a retry: {n}` |
| WATCH 8 | `within 5 C of the drive's trip point` |
| advisory | `last self-test {n} h ago` |
| advisory | `grown defect count not reported` |
| advisory | `pending defect count not reported` |
| advisory | `error counters not reported` |
| advisory | `uncorrected error counters not reported` |
| advisory | `retry counters not reported` |
| advisory | `no self-test on record` |
| advisory | `temperature not reported` |

The last two advisories were added during Task 2's review. `error counters not
reported` covers a drive that omits `scsi_error_counter_log` entirely; the two
asymmetric cases — a drive reporting `total_uncorrected_errors` but not
`errors_corrected_by_rereads_rewrites`, or the reverse — each get their own
line, because either one silently disables a rule and the operator should be
told which. Tasks 4 and 5 render `reasons` as an opaque list, so a new string
is additive and breaks nothing downstream.

- [ ] **Step 1: Write the failing tests**

Create `tests/python/test_smart_rules.py`:

```python
"""One test per rule, each mutating exactly one field of a real capture.

Both captured drives are healthy, so the fixtures alone cannot prove a single
rule fires. Mutating a real document rather than writing one by hand keeps
every other field truthful: a rule that fires does so for the reason the test
names, and not because the rest of the document is fiction.
"""
import unittest

import context
import smart


def sda(mutate=None):
    doc = context.fixture_json('agent-smart-golem-sda.json')
    if mutate is not None:
        mutate(doc)
    return doc


class TestFailRules(unittest.TestCase):
    def test_1_a_failed_smart_status_is_fail(self):
        got = smart.verdict(sda(lambda d: d['smart_status'].update(passed=False)))
        self.assertEqual('FAIL', got['verdict'])
        self.assertEqual('the drive reports SMART failure', got['reasons'][0])

    def test_2_an_uncorrected_error_on_any_lane_is_fail(self):
        for lane in ('read', 'write', 'verify'):
            def flip(d, lane=lane):
                d['scsi_error_counter_log'][lane]['total_uncorrected_errors'] = 3
            got = smart.verdict(sda(flip))
            self.assertEqual('FAIL', got['verdict'], lane)
            self.assertEqual('uncorrected %s errors: 3' % lane, got['reasons'][0])

    def test_3_a_failed_self_test_is_fail(self):
        def flip(d):
            d['scsi_self_test_0']['result'] = {'string': 'Failed in segment',
                                               'value': 5}
        got = smart.verdict(sda(flip))
        self.assertEqual('FAIL', got['verdict'])
        self.assertEqual('last self-test failed: Failed in segment',
                         got['reasons'][0])

    def test_3_an_aborted_self_test_is_not_a_failure(self):
        # Result codes 1 and 2 are aborts, by the host and by another
        # initiator. Someone cancelling a test is not a dying drive, and
        # calling it one is how a pane loses an operator's trust.
        for value in (1, 2):
            def flip(d, value=value):
                d['scsi_self_test_0']['result'] = {'string': 'Aborted',
                                                   'value': value}
            self.assertEqual('OK', smart.verdict(sda(flip))['verdict'], value)

    def test_3_a_running_self_test_is_not_a_result(self):
        def flip(d):
            d['scsi_self_test_0']['result'] = {'string': 'In progress',
                                               'value': 15}
        self.assertEqual('OK', smart.verdict(sda(flip))['verdict'])

    def test_4_reaching_the_trip_point_is_fail(self):
        got = smart.verdict(sda(lambda d: d['temperature'].update(current=60)))
        self.assertEqual('FAIL', got['verdict'])
        self.assertEqual("at the drive's own trip point (60 C)",
                         got['reasons'][0])

    def test_4_the_trip_point_comes_from_the_drive_not_a_constant(self):
        # A drive that reports a lower trip point trips earlier. No configured
        # threshold is involved anywhere in this rule.
        def flip(d):
            d['temperature'] = {'current': 46, 'drive_trip': 45}
        self.assertEqual('FAIL', smart.verdict(sda(flip))['verdict'])


class TestWatchRules(unittest.TestCase):
    def test_5_a_grown_defect_is_watch(self):
        got = smart.verdict(sda(lambda d: d.update(scsi_grown_defect_list=4)))
        self.assertEqual('WATCH', got['verdict'])
        self.assertEqual('grown defects: 4', got['reasons'][0])

    def test_6_a_pending_defect_is_watch(self):
        got = smart.verdict(sda(lambda d: d.update(scsi_pending_defects={'count': 2})))
        self.assertEqual('WATCH', got['verdict'])
        self.assertEqual('sectors pending reallocation: 2', got['reasons'][0])

    def test_7_a_reread_on_any_lane_is_watch(self):
        for lane in ('read', 'write', 'verify'):
            def flip(d, lane=lane):
                d['scsi_error_counter_log'][lane][
                    'errors_corrected_by_rereads_rewrites'] = 5
            got = smart.verdict(sda(flip))
            self.assertEqual('WATCH', got['verdict'], lane)
            self.assertEqual('%s operations needing a retry: 5' % lane,
                             got['reasons'][0])

    def test_7_the_ecc_corrected_total_is_not_a_signal(self):
        # sda already carries total_errors_corrected: 1 and errors_corrected_by
        # _eccdelayed: 1 in the real capture, and comes back OK. Raising them
        # further must still not move the verdict - ECC correcting a read is
        # what ECC is for, and counting it would put the fleet on WATCH.
        def flip(d):
            d['scsi_error_counter_log']['read']['total_errors_corrected'] = 900
            d['scsi_error_counter_log']['read']['errors_corrected_by_eccdelayed'] = 900
            d['scsi_error_counter_log']['read']['errors_corrected_by_eccfast'] = 900
        self.assertEqual('OK', smart.verdict(sda(flip))['verdict'])

    def test_8_nearing_the_trip_point_is_watch(self):
        got = smart.verdict(sda(lambda d: d['temperature'].update(current=56)))
        self.assertEqual('WATCH', got['verdict'])
        self.assertEqual("within 5 C of the drive's trip point",
                         got['reasons'][0])

    def test_8_does_not_double_report_with_rule_4(self):
        # The band is half-open. Open-ended, a drive at 60 would read "at the
        # drive's own trip point (60 C)" immediately followed by "within 5 C of
        # the drive's trip point" - two reasons for one fact.
        got = smart.verdict(sda(lambda d: d['temperature'].update(current=61)))
        self.assertEqual('FAIL', got['verdict'])
        self.assertNotIn("within 5 C of the drive's trip point", got['reasons'])


class TestOrdering(unittest.TestCase):
    def test_a_fail_outranks_a_watch_and_leads_the_reasons(self):
        def flip(d):
            d['scsi_grown_defect_list'] = 7                       # WATCH
            d['smart_status']['passed'] = False                   # FAIL
        got = smart.verdict(sda(flip))
        self.assertEqual('FAIL', got['verdict'])
        self.assertEqual('the drive reports SMART failure', got['reasons'][0])
        # The watch reason is kept, not discarded: the operator sees
        # everything notable, with the deciding reason first.
        self.assertIn('grown defects: 7', got['reasons'])


class TestAdvisories(unittest.TestCase):
    def test_a_stale_self_test_never_moves_the_verdict(self):
        # sda last ran a self-test at 33,845 power-on hours and is now at
        # 55,161 - 21,316 hours, about 2.4 years. Its Completed result is true
        # and uninformative. Made WATCH, it would flag every drive in a fleet
        # that schedules no SAS self-tests, and a pane where every row reads
        # WATCH conveys nothing.
        got = smart.verdict(sda())
        self.assertEqual('OK', got['verdict'])
        self.assertIn('last self-test 21316 h ago', got['reasons'])

    def test_an_absent_field_is_named_and_never_read_as_zero(self):
        got = smart.verdict(sda())
        self.assertIn('pending defect count not reported', got['reasons'])

    def test_the_five_absences_each_get_one_line(self):
        def strip(d):
            del d['scsi_grown_defect_list']
            del d['scsi_error_counter_log']
            del d['scsi_self_test_0']
            del d['temperature']
        got = smart.verdict(sda(strip))
        self.assertEqual('OK', got['verdict'])
        for text in ('grown defect count not reported',
                     'pending defect count not reported',
                     'error counters not reported',
                     'no self-test on record',
                     'temperature not reported'):
            self.assertIn(text, got['reasons'])

    def test_absent_error_counters_are_one_line_not_six(self):
        # Named per lane, a merely terse drive would print six lines. One line
        # per absent structure carries the same information without the noise.
        got = smart.verdict(sda(lambda d: d.pop('scsi_error_counter_log')))
        self.assertEqual(1, len([r for r in got['reasons']
                                 if 'error counters' in r]))


class TestTheRealDrives(unittest.TestCase):
    def test_the_hitachi_is_ok_with_two_advisories(self):
        got = smart.verdict(context.fixture_json('agent-smart-golem-sda.json'))
        self.assertEqual('OK', got['verdict'])
        self.assertEqual(['last self-test 21316 h ago',
                          'pending defect count not reported'], got['reasons'])

    def test_the_seagate_is_ok_with_one_advisory(self):
        # 28,682 current hours against a self-test at 22,992: 5,690 hours, well
        # past the 2160-hour line. Two drives out of two trip it, which is the
        # argument for keeping self-test age advisory rather than WATCH, made
        # by the only two samples the fleet has given us.
        got = smart.verdict(context.fixture_json('agent-smart-golem-sdb.json'))
        self.assertEqual('OK', got['verdict'])
        self.assertEqual(['last self-test 5690 h ago'], got['reasons'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest discover -s tests/python -p "test_smart_rules.py" -v`

Expected: every rule test FAILS with `'OK' != 'FAIL'` or `IndexError: list index out of range` on `reasons[0]` — Task 1's placeholder returns `OK` with no reasons for any healthy-shaped document.

- [ ] **Step 3: Write the implementation**

In `daemon/smart.py`, add these three functions after `_is_scsi()`:

```python
def _fail_reasons(summary):
    """Conditions under which the drive should be replaced.

    Every comparison guards against None first: an absent counter is not a
    zero counter, and a rule that read it as one would report a drive healthy
    on evidence it never had.
    """
    out = []
    if summary['passed'] is False:
        out.append('the drive reports SMART failure')
    for lane in LANES:
        count = summary['uncorrected'][lane]
        if count:
            # Uncorrected means the data did not come back. Corrected counts
            # are ECC doing its job and are not in the summary at all.
            out.append('uncorrected %s errors: %d' % (lane, count))
    if summary['self_test_value'] in SELF_TEST_FAILURES:
        out.append('last self-test failed: %s'
                   % (summary['self_test_result']
                      or 'result code %s' % summary['self_test_value']))
    temp, trip = summary['temperature'], summary['trip_temperature']
    if temp is not None and trip is not None and temp >= trip:
        out.append("at the drive's own trip point (%s C)" % trip)
    return out


def _watch_reasons(summary):
    """Conditions worth an operator's attention before they are a failure."""
    out = []
    if summary['grown_defects']:
        out.append('grown defects: %d' % summary['grown_defects'])
    if summary['pending_defects']:
        out.append('sectors pending reallocation: %d' % summary['pending_defects'])
    for lane in LANES:
        count = summary['rereads'][lane]
        if count:
            # A reread means the first attempt failed. This is the counter that
            # carries signal; total_errors_corrected is not.
            out.append('%s operations needing a retry: %d' % (lane, count))
    temp, trip = summary['temperature'], summary['trip_temperature']
    if (temp is not None and trip is not None
            and trip - TRIP_MARGIN_C <= temp < trip):
        # Half-open on purpose. Open-ended, a drive at or above its trip point
        # would fire this rule and the FAIL one together, printing two reasons
        # for one fact.
        out.append('within %d C of the drive%s trip point' % (TRIP_MARGIN_C, "'s"))
    return out


def _advisories(summary):
    """Facts worth printing that are not verdicts.

    Self-test AGE is here rather than in _watch_reasons deliberately. Unraid
    schedules no SAS self-tests, so both captured drives are already years past
    their last one; made a WATCH it would flag the entire fleet and say
    nothing. The age of a test is a fact about monitoring hygiene, not about
    the drive. A self-test FAILURE is a different thing and does set FAIL.

    The five "not reported" lines are the absent-versus-zero invariant made
    visible: the operator is told which questions the drive declined to answer,
    rather than being shown a clean number the drive never gave.
    """
    out = []
    hours, tested = summary['power_on_hours'], summary['self_test_hours']
    if (hours is not None and tested is not None
            and hours - tested > SELF_TEST_STALE_HOURS):
        out.append('last self-test %d h ago' % (hours - tested))
    if summary['grown_defects'] is None:
        out.append('grown defect count not reported')
    if summary['pending_defects'] is None:
        out.append('pending defect count not reported')
    if all(summary['uncorrected'][lane] is None for lane in LANES):
        out.append('error counters not reported')
    if summary['self_test_value'] is None:
        out.append('no self-test on record')
    if summary['temperature'] is None:
        out.append('temperature not reported')
    return out
```

Then replace the placeholder final line of `verdict()`:

```python
    return _result('OK', [], summary)
```

with:

```python
    fails = _fail_reasons(summary)
    watches = _watch_reasons(summary)
    state = 'FAIL' if fails else ('WATCH' if watches else 'OK')
    # reasons[0] is always the deciding one, and nothing notable is discarded.
    return _result(state, fails + watches + _advisories(summary), summary)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest discover -s tests/python -p "test_smart_rules.py" -v`
Expected: 18 tests, all PASS.

- [ ] **Step 5: Prove the rules can fail**

Mutation check — a rule with no test that makes it fire is not a rule. For each of the eight rule conditions, temporarily comment out its `out.append(...)` in `smart.py`, run `python -m unittest discover -s tests/python -p "test_smart_rules.py"`, and confirm a test FAILS. Restore the line before moving on. Record in the commit body which rules were checked.

- [ ] **Step 6: Run both full suites**

```bash
python -m unittest discover -s tests/python
export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash tests/php/run.sh
```

- [ ] **Step 7: Commit**

```bash
git add source/usr/local/emhttp/plugins/unraid-manager/daemon/smart.py tests/python/test_smart_rules.py
git commit -m "feat(smart): eight rules, each with a test that makes it fire"
```

---

### Task 3: Store the verdict instead of the raw document

**Files:**
- Modify: `source/usr/local/emhttp/plugins/unraid-manager/daemon/collector.py` — `parse_smart`, around line 389
- Modify: `tests/python/test_collector_agent.py`

**Interfaces:**
- Consumes: `smart.verdict(doc)` from Tasks 1-2.
- Produces: the stored `smart` payload shape that Task 4's PHP reads —
  `{'count': int, 'disks': {device: {'verdict': str, 'reasons': [str], 'summary': {...}}}}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/python/test_collector_agent.py`, inside `class TestCollectAgent`:

```python
    def test_a_parsed_disk_carries_a_verdict_not_a_raw_document(self):
        raw = context.fixture('agent-smart-golem-sda.json')
        got = self.collect(lambda node, verb, args, timeout: {'/dev/sda': raw})
        disk = got.payload['disks']['/dev/sda']
        self.assertEqual('OK', disk['verdict'])
        self.assertEqual(55161, disk['summary']['power_on_hours'])
        # The raw document is gone. Storing 8 KB per device that no consumer
        # reads cost about 300 KB per node per poll.
        self.assertNotIn('scsi_error_counter_log', disk)

    def test_an_unreadable_disk_keeps_its_key_as_unknown(self):
        # Both None and the empty string mean "no data" - smartctl exits
        # non-zero on a healthy drive with prefail attributes set, so the agent
        # sends '' for a disk it read just fine. Dropping the key would make a
        # dead disk look like one that was never installed.
        got = self.collect(lambda node, verb, args, timeout:
                           {'/dev/sda': None, '/dev/sdb': ''})
        for device in ('/dev/sda', '/dev/sdb'):
            self.assertEqual('UNKNOWN',
                             got.payload['disks'][device]['verdict'], device)
        self.assertEqual(2, got.payload['count'])

    def test_no_serial_survives_into_the_payload(self):
        import json as _json
        doc = context.fixture_json('agent-smart-golem-sda.json')
        doc['serial_number'] = 'SENTINEL-SERIAL-NOT-FOR-EXPORT'
        doc['logical_unit_id'] = 'SENTINEL-LUN-NOT-FOR-EXPORT'
        got = self.collect(lambda node, verb, args, timeout:
                           {'/dev/sda': _json.dumps(doc)})
        self.assertNotIn('SENTINEL', _json.dumps(got.payload))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest discover -s tests/python -p "test_collector_agent.py" -v`
Expected: `KeyError: 'verdict'` on the first two, and the third passes already (the strip is existing behaviour — it is here as a regression pin, and Step 3 must not break it).

- [ ] **Step 3: Write the implementation**

In `collector.py`, add `import smart` to the existing imports, then replace `parse_smart` entirely:

```python
def parse_smart(data):
    """`data` is {device: raw smartctl JSON text, or None/'' if unreadable}.

    A present-but-failing smartctl still exits non-zero on a healthy disk with
    prefail attributes set (its exit status is a bitmask), so the agent can
    send an EMPTY STRING for a disk it read just fine. That is the same "no
    data" fact as None, not a different one, so both reach smart.verdict(None)
    and come back UNKNOWN rather than one becoming a value and the other
    vanishing. A drive we truly could not read must still keep its key:
    dropping it would make a dead disk look like a disk that was never
    installed.

    serial_number and logical_unit_id are stripped before the document reaches
    the verdict chain: plan section 12 forbids a raw serial in an API-bound
    payload, and this payload is served to the browser. smart.summarize() reads
    neither, so the stripped document and the summary are both clean.

    What is stored is the verdict, its reasons and a small summary - not the
    raw document. The raw form ran about 8 KB per device, 300 KB per node per
    poll, and no consumer ever read it.
    """
    disks = {}
    for device, raw in (data or {}).items():
        if not raw:
            disks[device] = smart.verdict(None)
            continue
        doc = json.loads(raw)
        doc.pop('serial_number', None)
        doc.pop('logical_unit_id', None)
        disks[device] = smart.verdict(doc)
    return {'count': len(disks), 'disks': disks}
```

`json.loads` on a malformed reply still raises, and `collect_agent` still catches it into an `error` Result — that path is unchanged and already tested.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest discover -s tests/python -p "test_collector_agent.py" -v`
Expected: all PASS. Existing assertions such as `assertIn('/dev/sda', got.payload['disks'])` still hold — the keys did not move, only what sits behind them.

- [ ] **Step 5: Run both full suites**

```bash
python -m unittest discover -s tests/python
export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash tests/php/run.sh
```

Expected: both green. Any other test asserting the old raw-document shape must be updated here, not deferred.

- [ ] **Step 6: Commit**

```bash
git add source/usr/local/emhttp/plugins/unraid-manager/daemon/collector.py tests/python/test_collector_agent.py
git commit -m "feat(collector): store the verdict, not 300KB nothing reads"
```

---

### Task 4: The three-way join in `api/disks.php`

**Files:**
- Modify: `source/usr/local/emhttp/plugins/unraid-manager/api/disks.php`
- Test: `tests/php/disks_test.php`

**Interfaces:**
- Consumes: the stored `smart` payload shape from Task 3.
- Produces: each disk row gains `verdict` (string or null), `reasons` (array), `smart_tier` (int 0 or 1), `smart_fetched_at` (string or null); every `stale` entry gains `domain` (`'disks'` or `'smart'`). Task 5's Vue reads all five.

**The one thing not to get wrong:** `smart_tier` comes from the `nodes` table's `tier` column, **never** from whether a payload happens to exist. Inferring it from payload presence would label a Tier 1 node that has not been polled yet as Tier 0 — telling the operator the node *cannot* be assessed when it simply *has not been*.

- [ ] **Step 1: Write the failing tests**

In `tests/php/disks_test.php`, the fixture setup already inserts four nodes. Change Golem's row to tier 1 and add the smart payload. Replace the Golem insert:

```php
$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','1.2.3.4',1,0,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
```

with:

```php
/* Golem is the Tier 1 node: it runs the agent, so it has a smart payload.
   Raven, Ash and Bramble stay at tier 0 and correctly have none. */
$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','1.2.3.4',1,1,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
```

Then add the smart payload next to the existing `$disks` / `$array` fixtures:

```php
/* parse_smart's shape after the verdict chain: the verdict, its reasons and a
   small summary per device. Keyed by the full device path, exactly as the
   agent reports it - um_device_key() reduces it to the same basename the
   other two payloads join on. */
$smart = json_encode(['count' => 2, 'disks' => [
    '/dev/sdc' => ['verdict' => 'WATCH',
                   'reasons' => ['grown defects: 4', 'last self-test 21316 h ago'],
                   'summary' => ['model' => 'ST10000NM0226', 'power_on_hours' => 55161]],
    '/dev/sdz' => ['verdict' => 'OK', 'reasons' => [],
                   'summary' => ['model' => 'MG07SCA14TE', 'power_on_hours' => 100]]]]);
$db->exec("INSERT INTO node_state VALUES('a1b2','smart','ok',NULL,'2026-09-01T02:00:00Z','"
          . SQLite3::escapeString($smart) . "')");
```

And add these checks at the end, before the summary line:

```php
$out = um_fleet_disks($db);
$byDevice = [];
foreach ($out['disks'] as $row) $byDevice[$row['node'] . ':' . $row['device']] = $row;

$golem = $byDevice['Golem:/dev/sdc'];
check('a tier 1 disk carries its verdict', $golem['verdict'] === 'WATCH');
check('a tier 1 disk carries its reasons', $golem['reasons'][0] === 'grown defects: 4');
check('a tier 1 disk is marked tier 1', $golem['smart_tier'] === 1);
/* The smart domain has its own clock. Stamping a smart reading with the disks
   timestamp misreports its age, the same way stamping an orphan row with it
   already would. */
check('the smart reading keeps its own timestamp',
      $golem['smart_fetched_at'] === '2026-09-01T02:00:00Z'
      && $golem['fetched_at'] !== $golem['smart_fetched_at']);

$raven = $byDevice['Raven:/dev/sdc'] ?? null;
check('a tier 0 disk is marked tier 0', $raven !== null && $raven['smart_tier'] === 0);
check('a tier 0 disk has no verdict', $raven !== null && $raven['verdict'] === null);
$ravenStale = array_filter($out['stale'], fn($s) => $s['node'] === 'Raven'
                                                && $s['domain'] === 'smart');
check('a tier 0 node is not stale for a domain it never runs', $ravenStale === []);

/* The case that proves the tier is READ, not inferred. Cedar is tier 1 with a
   disks payload and no smart payload at all - enrolled and not yet polled.
   Inferred from payload presence it would read tier 0 and be labelled
   "(limited)", telling the operator it CANNOT be assessed when it merely has
   not been. */
$db->exec("INSERT INTO nodes VALUES('e5f6','Cedar','1.2.3.8',1,1,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO node_state VALUES('e5f6','disks','ok',NULL,'2026-09-01T01:00:00Z','"
          . SQLite3::escapeString($disks) . "')");
$out = um_fleet_disks($db);
$cedar = null;
foreach ($out['disks'] as $row) if ($row['node'] === 'Cedar') { $cedar = $row; break; }
check('an unpolled tier 1 node is still tier 1', $cedar !== null && $cedar['smart_tier'] === 1);
check('an unpolled tier 1 node has no verdict yet', $cedar !== null && $cedar['verdict'] === null);
$cedarStale = array_values(array_filter($out['stale'],
    fn($s) => $s['node'] === 'Cedar' && $s['domain'] === 'smart'));
check('an unpolled tier 1 node is listed as stale for smart',
      count($cedarStale) === 1
      && $cedarStale[0]['error'] === 'no SMART poll recorded yet');
check('every stale entry names its domain',
      count(array_filter($out['stale'], fn($s) => !isset($s['domain']))) === 0);
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
php -f tests/php/disks_test.php
```

Expected: FAIL on `a tier 1 disk carries its verdict` and every check after it — `$row['verdict']` does not exist yet.

- [ ] **Step 3: Write the implementation**

Three edits in `api/disks.php`.

**3a.** Widen the domain query in `um_disk_payloads()`:

```php
    foreach (um_query($db, "SELECT node_id, domain, status, error, fetched_at, payload "
                         . "FROM node_state WHERE domain IN ('disks','array','smart')") as $row) {
```

**3b.** Read the tier, and add the smart lookup and its stale entry. In `um_fleet_disks()`, change the node query and add the smart block after the existing `$slots` block:

```php
    foreach (um_query($db, 'SELECT id, name, tier FROM nodes ORDER BY name') as $node) {
```

```php
        /* The tier is READ from the registry, never inferred from whether a
           payload happens to exist. A tier 1 node that has not been polled yet
           has no smart row, and calling that tier 0 would tell the operator the
           node cannot be assessed when it merely has not been. */
        $tier = (int) ($node['tier'] ?? 0);
        $smartRow = $rows['smart'] ?? null;
        $verdicts = [];
        $smartPayload = json_decode((string) ($smartRow['payload'] ?? ''), true);
        foreach ((is_array($smartPayload) ? $smartPayload['disks'] ?? [] : []) as $dev => $v) {
            $k = um_device_key($dev);
            if ($k !== '' && is_array($v)) $verdicts[$k] = $v;
        }
        if ($tier === 1 && $smartRow === null) {
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'domain' => 'smart', 'status' => 'unknown',
                        'error' => 'no SMART poll recorded yet', 'fetched_at' => null];
        } elseif ($smartRow !== null && ($smartRow['status'] ?? '') !== 'ok') {
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'domain' => 'smart', 'status' => $smartRow['status'],
                        'error' => (string) $smartRow['error'],
                        'fetched_at' => $smartRow['fetched_at']];
        }
```

**3c.** Add `'domain' => 'disks'` to the two existing `$stale[]` pushes, and the four new keys to all three row builders. The physical-disk row:

```php
                'array_status' => $slot['status'] ?? null,
                'verdict' => $verdicts[$key]['verdict'] ?? null,
                'reasons' => $verdicts[$key]['reasons'] ?? [],
                'smart_tier' => $tier,
                'smart_fetched_at' => $smartRow['fetched_at'] ?? null,
                'fetched_at' => $diskRow['fetched_at'],
```

The orphan row (an array slot with no physical disk) gets the same four, with `$verdicts[$key]` looked up on its own `$key`: a drive that fell off the bus has no smartctl reading, so `verdict` is null there, and that is correct rather than incidental.

The spare row gets the same four, looked up on `um_device_key($spare['device'] ?? null)`.

- [ ] **Step 4: Run the test to verify it passes**

```bash
php -f tests/php/disks_test.php
```
Expected: `disks: all pass`.

- [ ] **Step 5: Run both full suites**

```bash
python -m unittest discover -s tests/python
export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash tests/php/run.sh
```

Expected: `php suite: all pass`. `tests/js/views.mjs` renders `Disks.vue` against fixture rows that do not yet carry the new keys — if it goes red here, the view is reading a key the fixture lacks; that is Task 5's work, so note it and do not patch the view from this task.

- [ ] **Step 6: Commit**

```bash
git add source/usr/local/emhttp/plugins/unraid-manager/api/disks.php tests/php/disks_test.php
git commit -m "feat(api): join the smart domain, and read the tier rather than guess it"
```

---

### Task 5: The verdict column

**Files:**
- Modify: `frontend/src/views/Disks.vue`
- Test: `tests/js/views.mjs`, `tests/js/interact.mjs`

**Interfaces:**
- Consumes: `verdict`, `reasons`, `smart_tier`, `smart_fetched_at` on each disk row, and `domain` on each stale entry, from Task 4.
- Produces: the rendered pane. Nothing consumes this.

**Class mapping** — the verdict names match the stylesheet's existing classes, defined in `StatusChip.vue` and shipped in the global bundle: `OK` → `um-ok`, `WATCH` → `um-watch`, `FAIL` → `um-crit`, everything else → `um-unknown`.

- [ ] **Step 1: Write the failing tests**

In `tests/js/views.mjs`, extend the `DISK` and `ORPHAN` fixtures with the new keys and add rows for each case:

```js
const DISK = {
  node: 'Raven', node_id: 'n1', model: 'ST10000NM0226', device: '/dev/sda',
  vendor: 'Seagate', size: 10000831348736, temp: 34, smart_status: 'OK',
  interface: 'SATA', slot: 'disk1', errors: 0, array_status: 'DISK_OK',
  verdict: null, reasons: [], smart_tier: 0, smart_fetched_at: null,
  fetched_at: '2026-08-28T00:00:00Z',
}
/* Golem runs the agent, so its rows carry a real assessment. */
const ASSESSED = {
  ...DISK, node: 'Golem', node_id: 'n2', device: '/dev/sdc',
  verdict: 'WATCH', reasons: ['grown defects: 4', 'last self-test 21316 h ago'],
  smart_tier: 1, smart_fetched_at: '2026-09-01T02:00:00Z',
}
/* Tier 1, enrolled, not yet polled. NOT the same as tier 0. */
const UNPOLLED = {
  ...DISK, node: 'Cedar', node_id: 'n3', device: '/dev/sdd',
  verdict: null, reasons: [], smart_tier: 1,
}
```

Add these checks:

```js
{
  const html = await renderView(Disks, { disks: [ASSESSED], spares: [], stale: [] })
  check('an assessed disk shows its verdict', html.includes('WATCH'))
  check('a WATCH verdict is styled as a watch', html.includes('um-watch'))
  check('an assessed disk is not labelled limited', !html.includes('(limited)'))
}
{
  /* A tier 0 OK must never read as an assessed OK. Unraid's API reports
     OK|UNKNOWN and nothing behind it. */
  const html = await renderView(Disks, { disks: [DISK], spares: [], stale: [] })
  check('a tier 0 disk is labelled limited', html.includes('OK (limited)'))
}
{
  /* Tier 1 with nothing collected yet renders a dash, NOT "(limited)": the
     node is capable of an assessment and has not produced one. Rendering
     these two the same way is the absent-versus-unable defect on screen. */
  const html = await renderView(Disks, { disks: [UNPOLLED], spares: [], stale: [] })
  check('an unpolled tier 1 disk is not labelled limited', !html.includes('(limited)'))
}
{
  /* The stale copy is per-domain. Today's sentence - "no disk list yet, this
     node has not been polled since it was enrolled" - is simply false for a
     node whose disk list is fine and whose SMART call failed. */
  const html = await renderView(Disks, { disks: [], spares: [], stale: [
    { node: 'Golem', node_id: 'n2', domain: 'smart', status: 'error',
      error: 'ssh exited 255', fetched_at: '2026-09-01T02:00:00Z' }] })
  check('a smart staleness says SMART, not disk list',
        html.includes('SMART') && !html.includes('no disk list yet'))
}
```

In `tests/js/interact.mjs`, add a click test for the expand:

```js
{
  /* An unexplained WATCH sends the operator to a shell with smartctl, which
     defeats the pane. The reason has to be reachable without leaving it. */
  const { html, click } = await mountView(Disks, { disks: [ASSESSED], spares: [], stale: [] })
  check('reasons are hidden until asked for', !html().includes('grown defects: 4'))
  await click('tbody tr')
  check('clicking a row reveals its reasons', html().includes('grown defects: 4'))
  await click('tbody tr')
  check('clicking again hides them', !html().includes('grown defects: 4'))
}
```

Match `mountView`/`click` to whatever helper `interact.mjs` already defines — read the top of that file and reuse its existing harness rather than adding a second one. Define `ASSESSED` there too, or export it from `views.mjs` if that file already exports fixtures.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
node tests/js/views.mjs
node tests/js/interact.mjs
```
Expected: FAIL on the new checks — the column still renders `smartOf()`, which knows nothing about verdicts.

- [ ] **Step 3: Write the implementation**

In `Disks.vue`'s `<script setup>`, replace `smartOf` and add the expand state:

```js
// Controller amendment B: model === null is an array slot with no physical
// disk behind it. It has no SMART status because there is nothing to ask,
// which is not the same as a disk that answered UNKNOWN. One spelling, used by
// both the cell and the filter button: with the literal repeated in the
// template, changing this function left the "No disk" filter selecting nothing
// and the whole suite green.
const NO_DISK = 'no disk'
const LIMITED = 'limited'
const NOT_YET = 'not assessed yet'

// The key the filters compare, so display text and filter value can never
// drift apart the way they did once already.
function verdictKey (disk) {
  if (disk.model === null) return NO_DISK
  if (disk.smart_tier !== 1) return LIMITED
  return disk.verdict || NOT_YET
}

// What the cell shows. A tier 0 node reports Unraid's OK|UNKNOWN and nothing
// behind it; the suffix exists so a tier 0 OK can never be read as an assessed
// one. A tier 1 row with no verdict yet gets a dash instead - that node CAN be
// assessed and simply has not been, and rendering the two alike would be the
// same absent-versus-unable defect this pane keeps closing.
function verdictText (disk) {
  const key = verdictKey(disk)
  if (key === LIMITED) return `${disk.smart_status || 'UNKNOWN'} (limited)`
  if (key === NOT_YET) return '—'
  return key
}

const VERDICT_CLASS = { OK: 'um-ok', WATCH: 'um-watch', FAIL: 'um-crit' }
function verdictClass (disk) {
  return VERDICT_CLASS[verdictKey(disk)] || 'um-unknown'
}

const expanded = ref(null)
function rowKey (disk) { return disk.node_id + ':' + disk.device }
function toggle (disk) {
  const key = rowKey(disk)
  expanded.value = expanded.value === key ? null : key
}
```

Update the filter to compare `verdictKey`:

```js
    .filter(d => !smartFilter.value || verdictKey(d) === smartFilter.value)
```

In the template, replace the SMART cell and add the details row:

```html
          <tr v-for="disk in rows" :key="rowKey(disk)" @click="toggle(disk)">
            ...
            <td :class="verdictClass(disk)">{{ verdictText(disk) }}</td>
          </tr>
          <tr v-if="expanded === rowKey(disk) && disk.reasons.length"
              :key="rowKey(disk) + ':why'">
            <td colspan="10">{{ disk.reasons.join(' · ') }}</td>
          </tr>
```

Two `<tr>` elements cannot both carry the same `v-for`; wrap them in a `<template v-for="disk in rows" :key="rowKey(disk)">` and move `:key` onto each `<tr>` inside it, which is the standard Vue 3 form for a row plus its detail row.

Replace the filter buttons:

```html
        <button type="button" @click="smartFilter = ''">Any SMART</button>
        <button type="button" @click="smartFilter = 'OK'">OK</button>
        <button type="button" @click="smartFilter = 'WATCH'">WATCH</button>
        <button type="button" @click="smartFilter = 'FAIL'">FAIL</button>
        <button type="button" @click="smartFilter = 'UNKNOWN'">UNKNOWN</button>
        <button type="button" @click="smartFilter = LIMITED">Tier 0 only</button>
        <button type="button" @click="smartFilter = NO_DISK">No disk</button>
```

Split the stale copy by domain — today's sentence is false for a SMART failure:

```html
      <p v-for="entry in stale" :key="entry.node_id + ':' + entry.domain"
         class="um-node-stale">
        <template v-if="entry.domain === 'smart'">
          <template v-if="entry.fetched_at">
            {{ entry.node }}: showing the SMART assessment collected
            {{ localTime(entry.fetched_at, tz, clock12) }} — the latest agent call
            did not complete ({{ entry.error }}).
          </template>
          <template v-else>
            {{ entry.node }}: no SMART assessment yet — this node runs the agent
            but has not been polled for SMART. The inventory poll is slow; give
            it ten minutes.
          </template>
        </template>
        <template v-else>
          <template v-if="entry.fetched_at">
            {{ entry.node }}: showing the disk list collected
            {{ localTime(entry.fetched_at, tz, clock12) }} — the latest poll did not complete
            ({{ entry.error }}).
          </template>
          <template v-else>
            {{ entry.node }}: no disk list yet — this node has not been polled
            since it was enrolled. The inventory poll is slow; give it ten
            minutes.
          </template>
        </template>
      </p>
```

And gate the standing hint on there still being a Tier 0 node:

```js
const anyTier0 = computed(() =>
  (data.value?.disks ?? []).some(d => d.smart_tier !== 1))
```

```html
      <p v-if="anyTier0" class="um-hint">
        Unraid's API reports SMART health as OK or UNKNOWN only. Full SMART
        attributes, and the disk assessment they support, need a Tier 1 agent
        on each node.
      </p>
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
node tests/js/views.mjs
node tests/js/interact.mjs
```
Expected: `views: all pass`, `interact: all pass`.

- [ ] **Step 5: Rebuild the bundle**

The plugin ships a built bundle under `source/.../ui/assets/`. Run the repo's build (`bash build.sh` or the npm build it wraps — read `build.sh` first) and commit the rebuilt asset alongside the source change, or the pane on the box will not show any of this.

- [ ] **Step 6: Run both full suites**

```bash
python -m unittest discover -s tests/python
export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash tests/php/run.sh
```

`frontend_test.php` and `build_test.php` pin facts about the built bundle; if either goes red, the rebuild in Step 5 is what they are looking at.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Disks.vue tests/js/views.mjs tests/js/interact.mjs source/usr/local/emhttp/plugins/unraid-manager/ui
git commit -m "feat(disks): a verdict column that never dresses tier 0 as assessed"
```

---

### Task 6: Documentation and hardware verification

**Files:**
- Modify: `ARCHITECTURE.md`, `docs/verification/tier0-coverage.md`, `docs/verification/p2-checks.md`, `README.md`
- Modify: `.remember/remember.md` (the handoff)

**Interfaces:** none. This task ships no code.

- [ ] **Step 1: Update the docs**

- `ARCHITECTURE.md` — add `daemon/smart.py` to the module list, one line on what it owns, and note that the `smart` domain's stored payload is a verdict rather than a raw document.
- `docs/verification/tier0-coverage.md` — the `smart` domain now has a consumer; record what the pane shows for a Tier 0 node and why it is labelled.
- `README.md` — if it lists what the Disks screen shows, add the verdict.
- `docs/verification/p2-checks.md` — add the three hardware checks below with their results once Step 2 is done.

- [ ] **Step 2: Verify on hardware**

This is the only step that wants the box. **Stop here and hand these to Joe rather than assuming a result** — the three checks, in order:

1. **Golem shows verdicts.** Open the Disks pane. Golem's rows show `OK` / `WATCH` / `FAIL`, not `(limited)`. Click a row with reasons; the reasons appear.
2. **Raven shows `(limited)`.** Raven's rows read `OK (limited)` or `UNKNOWN (limited)` and show no verdict, and the standing hint about needing a Tier 1 agent is visible.
3. **The stored payload is the summary shape.** On Raven:

   ```bash
   sqlite3 /boot/config/plugins/unraid-manager/manager.db \
     "SELECT length(payload) FROM node_state WHERE domain='smart';"
   ```

   Expected: single-digit KB, not ~300 KB. And:

   ```bash
   sqlite3 /boot/config/plugins/unraid-manager/manager.db \
     "SELECT payload LIKE '%serial%' OR payload LIKE '%logical_unit%' FROM node_state WHERE domain='smart';"
   ```

   Expected: `0`. Read the db path from the manager config rather than assuming it if that command finds nothing.

- [ ] **Step 3: Record the results**

Write what the box actually printed into `docs/verification/p2-checks.md` — the output, not a summary of it. A check recorded as "passed" with no evidence is not a check.

- [ ] **Step 4: Commit**

```bash
git add ARCHITECTURE.md README.md docs/verification .remember/remember.md
git commit -m "docs(smart): what the verdict column claims, and what the box said"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: the module and its two stages → Tasks 1-2; the eight rules, the UNKNOWN paths, the absent-field table and the self-test advisory → Tasks 1-2; the stored payload shape and serial stripping → Task 3; the API join, the tier-from-registry rule and the `domain` field on stale entries → Task 4; the frontend, the `(limited)` label and the expand → Task 5; hardware verification and the five exit criteria → Task 6. No spec requirement is unassigned.

**Type consistency.** `summarize()`'s 12 keys are used under the same names in `_fail_reasons`, `_watch_reasons` and `_advisories`. `verdict()` returns `{'verdict', 'reasons', 'summary'}` in Task 1 and is read under those names in Tasks 3, 4 and 5. The four row keys added in Task 4 (`verdict`, `reasons`, `smart_tier`, `smart_fetched_at`) are the four read in Task 5. The reason strings in Task 2's interface table are the strings asserted in Task 2's tests and rendered verbatim in Tasks 4 and 5.

**Known soft spot.** Task 5's `interact.mjs` snippet names `mountView` and `click` without pinning their signatures, because that harness already exists in the file and its shape should be read there rather than guessed here. That is the one place the implementer must read surrounding code rather than transcribe.
