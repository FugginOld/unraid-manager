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


class TestUntypedNumericFields(unittest.TestCase):
    # smart_status.passed==False is a fail already covered by rule 1; here we
    # push a non-number into a field summarize() treats as a count, and check
    # that it is read as "not reported" rather than raising.

    def test_a_string_reading_summarizes_to_none_not_the_string(self):
        doc = sda(lambda d: d.update(scsi_grown_defect_list='oops'))
        self.assertIsNone(smart.summarize(doc)['grown_defects'])

    def test_a_string_grown_defect_count_does_not_raise_and_reads_as_absent(self):
        got = smart.verdict(sda(lambda d: d.update(scsi_grown_defect_list='oops')))
        self.assertEqual('OK', got['verdict'])
        self.assertIn('grown defect count not reported', got['reasons'])

    def test_bool_is_not_treated_as_a_number_here(self):
        # bool is a subclass of int in Python, but True/False answers a
        # yes/no question, not a count - it must not survive as one.
        doc = sda(lambda d: d.update(scsi_grown_defect_list=True))
        self.assertIsNone(smart.summarize(doc)['grown_defects'])


if __name__ == '__main__':
    unittest.main()
