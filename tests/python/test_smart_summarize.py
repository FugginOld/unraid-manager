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
        # The summary is an allow-list, not a filter: only keys named here can
        # ever reach the rules or an API-bound payload. That is what keeps a
        # raw smartctl field - the ECC corrected total, a serial number - from
        # transiting into the summary just because it was added upstream.
        self.assertEqual(
            {'model', 'passed', 'power_on_hours', 'temperature', 'trip_temperature',
             'grown_defects', 'pending_defects', 'uncorrected', 'rereads',
             'self_test_result', 'self_test_value', 'self_test_hours'},
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
        self.assertEqual(['smartctl could not read this device'],
                         smart.verdict('')['reasons'])

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
