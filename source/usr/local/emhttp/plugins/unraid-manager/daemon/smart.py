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

import math

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


def _number(value):
    """value if it is a finite number, else None.

    A string, list, or dict is not a count: the drive answered the question
    with something that is not a number, so we do not have a number, and the
    operator is told which questions went unanswered (summarize()'s None
    already means "not reported") rather than being shown a value that was
    never actually measured. bool is excluded even though Python treats it as
    a subclass of int - True/False answers a yes/no question, not a count.
    NaN and Infinity are excluded too: json.loads parses them from the raw
    document by default, and neither is a count either - a NaN reaches a %d
    formatter as a ValueError, an Infinity as an OverflowError.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _lanes(doc, field):
    """One value per SCSI lane, None for a lane the drive did not report."""
    return {lane: _number(_dig(doc, 'scsi_error_counter_log', lane, field))
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
    passed = _dig(doc, 'smart_status', 'passed')
    return {
        'model': doc.get('model_name'),
        # Not a count, so not through _number(): a non-bool reading (a stray
        # "false" string, a 0) is not a positive-or-negative signal either,
        # and the existing passed-is-None -> UNKNOWN gate already refuses to
        # judge on it once it collapses to None here.
        'passed': passed if isinstance(passed, bool) else None,
        'power_on_hours': _number(_dig(doc, 'power_on_time', 'hours')),
        'temperature': _number(_dig(doc, 'temperature', 'current')),
        'trip_temperature': _number(_dig(doc, 'temperature', 'drive_trip')),
        'grown_defects': _number(doc.get('scsi_grown_defect_list')),
        'pending_defects': _number(_dig(doc, 'scsi_pending_defects', 'count')),
        'uncorrected': _lanes(doc, 'total_uncorrected_errors'),
        'rereads': _lanes(doc, 'errors_corrected_by_rereads_rewrites'),
        'self_test_result': _dig(doc, 'scsi_self_test_0', 'result', 'string'),
        'self_test_value': _number(_dig(doc, 'scsi_self_test_0', 'result', 'value')),
        'self_test_hours': _number(_dig(doc, 'scsi_self_test_0', 'power_on_time', 'hours')),
    }


def _result(state, reasons, summary):
    return {'verdict': state, 'reasons': reasons, 'summary': summary}


def _is_scsi(doc):
    """Does this document carry any SCSI structure at all?

    Every rule reads a scsi_ key. A document with none of them cannot be
    judged by this chain, whatever else it contains.
    """
    return any(str(key).startswith('scsi_') for key in doc)


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
        if count is not None and count > 0:
            # Uncorrected means the data did not come back. Corrected counts
            # are ECC doing its job and are not in the summary at all.
            out.append('uncorrected %s errors: %d' % (lane, count))
    if summary['self_test_value'] in SELF_TEST_FAILURES:
        out.append('last self-test failed: %s'
                   % (summary['self_test_result']
                      or 'result code %s' % summary['self_test_value']))
    temp, trip = summary['temperature'], summary['trip_temperature']
    if temp is not None and trip is not None and temp >= trip:
        out.append("at the drive's own trip point (%d C)" % trip)
    return out


def _watch_reasons(summary):
    """Conditions worth an operator's attention before they are a failure."""
    out = []
    grown = summary['grown_defects']
    if grown is not None and grown > 0:
        out.append('grown defects: %d' % grown)
    pending = summary['pending_defects']
    if pending is not None and pending > 0:
        out.append('sectors pending reallocation: %d' % pending)
    for lane in LANES:
        count = summary['rereads'][lane]
        if count is not None and count > 0:
            # A reread means the first attempt failed. This is the counter that
            # carries signal; total_errors_corrected is not.
            out.append('%s operations needing a retry: %d' % (lane, count))
    temp, trip = summary['temperature'], summary['trip_temperature']
    if (temp is not None and trip is not None
            and trip - TRIP_MARGIN_C <= temp < trip):
        # Half-open on purpose. Open-ended, a drive at or above its trip point
        # would fire this rule and the FAIL one together, printing two reasons
        # for one fact.
        out.append("within %d C of the drive's trip point" % TRIP_MARGIN_C)
    return out


def _advisories(summary):
    """Facts worth printing that are not verdicts.

    Self-test AGE is here rather than in _watch_reasons deliberately. Unraid
    schedules no SAS self-tests, so both captured drives are already years past
    their last one; made a WATCH it would flag the entire fleet and say
    nothing. The age of a test is a fact about monitoring hygiene, not about
    the drive. A self-test FAILURE is a different thing and does set FAIL.

    The "not reported" lines are the absent-versus-zero invariant made
    visible: the operator is told which questions the drive declined to
    answer, rather than being shown a clean number the drive never gave.
    Seven possible strings across five categories - error counters split into
    three mutually exclusive variants (both absent, only uncorrected absent,
    only rereads absent) depending on which of the two structures the drive
    reported, so at most one of the three ever fires alongside the other four.
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
    uncorrected_absent = all(summary['uncorrected'][lane] is None for lane in LANES)
    rereads_absent = all(summary['rereads'][lane] is None for lane in LANES)
    if uncorrected_absent and rereads_absent:
        # The common case: scsi_error_counter_log is missing outright, so
        # both structures vanish together and one line covers the fact.
        out.append('error counters not reported')
    elif uncorrected_absent:
        # Rule 2 (FAIL) can never fire; rule 7 (WATCH) still can. A drive
        # reporting one structure but not the other is a different fact from
        # reporting neither, and gets its own line rather than going unsaid.
        out.append('uncorrected error counters not reported')
    elif rereads_absent:
        # Rule 7 (WATCH) can never fire; rule 2 (FAIL) still can.
        out.append('retry counters not reported')
    if summary['self_test_value'] is None:
        out.append('no self-test on record')
    if summary['temperature'] is None:
        out.append('temperature not reported')
    return out


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
    fails = _fail_reasons(summary)
    watches = _watch_reasons(summary)
    state = 'FAIL' if fails else ('WATCH' if watches else 'OK')
    # reasons[0] is the deciding one for FAIL and WATCH, where fails/watches is
    # non-empty and sorts first. It is NOT true for OK: fails and watches are
    # both empty there, so reasons[0] (if there is one at all) is just the
    # first advisory - sda's is the self-test-age note - not evidence for
    # anything. Nothing notable is discarded either way.
    return _result(state, fails + watches + _advisories(summary), summary)
