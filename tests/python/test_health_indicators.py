import unittest

import context
import collector
import health


def array_payload(name='seed/array_populated.json'):
    return collector.parse_array(context.fixture_json(name)['data'])


class TestArrayState(unittest.TestCase):
    def test_started_is_ok(self):
        self.assertEqual(health.OK, health.evaluate_array_state({'state': 'STARTED'}).state)

    def test_stopped_is_watch_not_warn(self):
        # A stopped array is a deliberate operator action as often as a fault.
        self.assertEqual(health.WATCH, health.evaluate_array_state({'state': 'STOPPED'}).state)

    def test_anything_else_is_warn(self):
        self.assertEqual(health.WARN, health.evaluate_array_state({'state': 'ERROR'}).state)

    def test_missing_state_is_unknown_never_ok(self):
        self.assertEqual(health.UNKNOWN, health.evaluate_array_state({}).state)
        self.assertEqual(health.UNKNOWN, health.evaluate_array_state(None).state)

    def test_the_basis_names_the_state(self):
        self.assertIn('ERROR', health.evaluate_array_state({'state': 'ERROR'}).basis)


class TestCapacity(unittest.TestCase):
    T = health.DEFAULT_THRESHOLDS

    def cap(self, used, total):
        return health.evaluate_capacity({'capacity': {'used': used, 'total': total}}, self.T)

    def test_an_empty_array_is_ok_not_a_division(self):
        # Constraint 3, and 0/0 is not 100%.
        out = self.cap(0, 0)
        self.assertEqual(health.OK, out.state)
        self.assertIn('empty', out.basis)

    def test_comfortable_is_ok(self):
        self.assertEqual(health.OK, self.cap(50, 100).state)

    def test_ten_points_below_high_water_is_watch(self):
        self.assertEqual(health.WATCH, self.cap(80, 100).state)

    def test_at_high_water_is_warn(self):
        self.assertEqual(health.WARN, self.cap(90, 100).state)

    def test_the_value_is_a_percentage(self):
        self.assertEqual(93.0, self.cap(93, 100).value)

    def test_thresholds_are_configurable(self):
        loose = dict(health.DEFAULT_THRESHOLDS, capacity_high_water=99)
        self.assertEqual(health.OK, health.evaluate_capacity(
            {'capacity': {'used': 80, 'total': 100}}, loose).state)

    def test_the_real_golem_capture_is_warn(self):
        out = health.evaluate_capacity(array_payload(), self.T)
        self.assertEqual(health.WARN, out.state)

    def test_a_null_capacity_is_unknown_not_empty(self):
        # Value presence, not key presence: a reported null is not a reported zero.
        self.assertEqual(health.UNKNOWN, health.evaluate_capacity(
            {'capacity': None}, health.DEFAULT_THRESHOLDS).state)

    def test_a_reported_zero_is_still_an_empty_array(self):
        self.assertEqual(health.OK, health.evaluate_capacity(
            {'capacity': {'used': 0, 'total': 0}}, health.DEFAULT_THRESHOLDS).state)


class TestThermal(unittest.TestCase):
    T = health.DEFAULT_THRESHOLDS

    def test_cool_is_ok(self):
        self.assertEqual(health.OK, health.evaluate_thermal({'temp_max': 35}, self.T).state)

    def test_at_temp_warn_is_watch(self):
        self.assertEqual(health.WATCH, health.evaluate_thermal({'temp_max': 50}, self.T).state)

    def test_at_temp_crit_is_warn(self):
        self.assertEqual(health.WARN, health.evaluate_thermal({'temp_max': 60}, self.T).state)

    def test_no_temperature_is_unknown_never_ok(self):
        # A box that reports no temperature must not read as healthy.
        self.assertEqual(health.UNKNOWN, health.evaluate_thermal({'temp_max': None}, self.T).state)

    def test_an_empty_array_reports_no_temperature(self):
        out = health.evaluate_thermal(array_payload('seed/array_empty.json'), self.T)
        self.assertEqual(health.UNKNOWN, out.state)

    # -- P1 exit finding F-4 -------------------------------------------------
    # Raven's array is empty, so array.temp_max is None and every one of its
    # eleven disks was invisible here: the card read "no disk temperature
    # reported" while the Disks tab showed 33-40 C for the same box. A box with
    # no thermal monitoring at all is what this indicator exists to prevent.
    INVENTORY = {'disks': [{'device': '/dev/sda', 'temp': 37},
                           {'device': '/dev/sdb', 'temp': 41},
                           {'device': '/dev/sdc', 'temp': None}]}

    def test_unassigned_disks_are_still_watched(self):
        out = health.evaluate_thermal(array_payload('seed/array_empty.json'), self.T,
                                      self.INVENTORY)
        self.assertEqual(health.OK, out.state)
        self.assertEqual(41, out.value)

    def test_the_basis_says_the_reading_came_from_the_slower_inventory(self):
        # It can be up to ten minutes old; an operator comparing it against the
        # Disks tab deserves to know which number they are looking at.
        out = health.evaluate_thermal({'temp_max': None}, self.T, self.INVENTORY)
        self.assertIn('inventory', out.basis)

    def test_a_hot_unassigned_disk_beats_a_cool_array(self):
        # The MAX of both, not a fallback: an unassigned disk cooking in a bay
        # is exactly as much of a problem as an array one.
        out = health.evaluate_thermal({'temp_max': 30}, self.T,
                                      {'disks': [{'temp': 62}]})
        self.assertEqual(health.WARN, out.state)
        self.assertEqual(62, out.value)

    def test_the_array_reading_wins_when_it_is_the_hotter_one(self):
        out = health.evaluate_thermal({'temp_max': 55}, self.T, self.INVENTORY)
        self.assertEqual(health.WATCH, out.state)
        self.assertEqual(55, out.value)
        self.assertNotIn('inventory', out.basis)

    def test_an_inventory_with_no_readings_is_still_unknown(self):
        # Not 0 C, and not OK: no reading is no reading.
        out = health.evaluate_thermal({'temp_max': None}, self.T,
                                      {'disks': [{'temp': None}, {'device': '/dev/sdb'}]})
        self.assertEqual(health.UNKNOWN, out.state)

    def test_evaluate_passes_the_inventory_through(self):
        # The wiring, not just the function: evaluate() is what the daemon
        # calls, and a fix that never reaches it fixes nothing.
        out = health.evaluate({'array': array_payload('seed/array_empty.json'),
                               'disks': self.INVENTORY}, self.T)
        self.assertEqual(health.OK, out['thermal'].state)
        self.assertEqual(41, out['thermal'].value)


class TestDiskErrors(unittest.TestCase):
    def test_no_history_is_unknown(self):
        self.assertEqual(health.UNKNOWN, health.evaluate_disk_errors([]).state)

    def test_one_sample_is_not_enough_to_judge(self):
        self.assertEqual(health.UNKNOWN,
                         health.evaluate_disk_errors([('2026-08-27T00:00:00Z', 4)]).state)

    def test_a_flat_counter_is_ok_however_large(self):
        # Three errors logged in 2019 are not a problem.
        out = health.evaluate_disk_errors([('2026-08-27T00:00:00Z', 4210),
                                           ('2026-08-27T00:05:00Z', 4210)])
        self.assertEqual(health.OK, out.state)

    def test_a_flat_counter_still_names_the_count(self):
        """P1 exit finding F-6.

        The card read "OK disk errors - no new disk errors in the window" while
        the Disks tab showed 192 on Golem's disk15, one tab over. Both true;
        together they read as a contradiction. This indicator judges CHANGE on
        purpose, so it says what the standing count is rather than leaving the
        operator to reconcile two screens.
        """
        out = health.evaluate_disk_errors([('2026-08-27T00:00:00Z', 192),
                                           ('2026-08-27T00:05:00Z', 192)])
        self.assertEqual(health.OK, out.state)
        self.assertIn('192', out.basis)
        self.assertIn('no new disk errors', out.basis)

    def test_a_clean_disk_does_not_report_a_count_of_nothing(self):
        # "0 recorded in total" is noise on a healthy box.
        out = health.evaluate_disk_errors([('2026-08-27T00:00:00Z', 0),
                                           ('2026-08-27T00:05:00Z', 0)])
        self.assertEqual(health.OK, out.state)
        self.assertNotIn('0', out.basis)

    def test_any_increase_inside_the_window_is_warn(self):
        out = health.evaluate_disk_errors([('2026-08-27T00:00:00Z', 0),
                                           ('2026-08-27T00:05:00Z', 0),
                                           ('2026-08-27T00:10:00Z', 3)])
        self.assertEqual(health.WARN, out.state)
        self.assertEqual(3, out.value)

    def test_the_increase_stays_visible_across_later_samples(self):
        # The whole reason this is a window and not a since-last-sample delta:
        # a one-off jump must survive long enough to clear 2-sample escalation.
        out = health.evaluate_disk_errors([('2026-08-27T00:00:00Z', 0),
                                           ('2026-08-27T00:10:00Z', 3),
                                           ('2026-08-27T00:15:00Z', 3),
                                           ('2026-08-27T00:20:00Z', 3)])
        self.assertEqual(health.WARN, out.state)

    def test_a_counter_reset_is_not_an_error(self):
        # A peer rebooted and its counters went to zero.
        out = health.evaluate_disk_errors([('2026-08-27T00:00:00Z', 90),
                                           ('2026-08-27T00:05:00Z', 0)])
        self.assertEqual(health.OK, out.state)


class TestEvaluate(unittest.TestCase):
    def test_it_returns_all_four_indicators(self):
        out = health.evaluate({'array': array_payload()})
        self.assertEqual({'array_state', 'capacity', 'thermal', 'disk_errors'}, set(out))

    def test_a_missing_array_payload_is_unknown_not_a_crash(self):
        out = health.evaluate({})
        self.assertEqual(health.UNKNOWN, out['array_state'].state)
        self.assertEqual(health.UNKNOWN, out['thermal'].state)
        # Absence is not emptiness. A blind domain must never inherit the empty
        # array's ok.
        self.assertEqual(health.UNKNOWN, out['capacity'].state)

    def test_unknown_is_not_on_the_severity_ladder(self):
        self.assertNotIn(health.UNKNOWN, health.LADDER)

    def test_partial_thresholds_override_and_the_rest_fall_back(self):
        # Observes BOTH halves: the override changes thermal, and capacity still
        # uses the default high-water mark.
        out = health.evaluate({'array': {'temp_max': 47,
                                         'capacity': {'used': 10, 'total': 100}}},
                              thresholds={'temp_warn': 45})
        self.assertEqual(health.WATCH, out['thermal'].state, 'the override applied')
        self.assertEqual(health.OK, out['capacity'].state, 'the default applied')


class TestErrorsSample(unittest.TestCase):
    def test_the_collector_emits_an_errors_total_sample(self):
        # evaluate_disk_errors has no history without it.
        data = context.fixture_json('seed/array_populated.json')['data']
        result = collector.collect(lambda *a, **k: data,
                                   {'address': 'h', 'port': 1, 'key': 'k'},
                                   collector.DOMAINS['array'])
        self.assertIn('array.errors_total', dict(result.samples))

    def test_the_array_payload_carries_per_disk_rows(self):
        # The fleet disk table joins on these; array.disks is the only source
        # of slot and numErrors.
        out = collector.parse_array(context.fixture_json('seed/array_populated.json')['data'])
        self.assertEqual(23, len(out['disks']), '22 data disks plus the parity drive')
        first = out['disks'][0]
        self.assertEqual('disk1', first['slot'])
        self.assertEqual('sdc', first['device'])
        self.assertEqual(0, first['numErrors'], 'the value, not just the key')
        self.assertEqual('parity', out['disks'][-1]['slot'])

    def test_disk_row_size_is_bytes_like_every_other_capacity(self):
        # array.disks reports kilobytes; the physical disks payload reports
        # bytes. Two merged payloads with a `size` key in different units is how
        # a table shows a 14 TB drive as 13 GB.
        out = collector.parse_array(context.fixture_json('seed/array_populated.json')['data'])
        self.assertEqual(13672382412 * 1024, out['disks'][0]['size'])

    def test_an_empty_array_has_no_disk_rows(self):
        out = collector.parse_array(context.fixture_json('seed/array_empty.json')['data'])
        self.assertEqual([], out['disks'])


if __name__ == '__main__':
    unittest.main()
