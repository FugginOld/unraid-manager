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
