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

    def test_partial_thresholds_fall_back_to_the_defaults(self):
        out = health.evaluate({'array': array_payload()}, thresholds={'temp_warn': 45})
        self.assertIn(out['capacity'].state, health.LADDER)


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
        self.assertEqual(22, len(out['disks']))
        first = out['disks'][0]
        self.assertEqual('disk1', first['slot'])
        self.assertEqual('sdc', first['device'])
        self.assertIn('numErrors', first)

    def test_an_empty_array_has_no_disk_rows(self):
        out = collector.parse_array(context.fixture_json('seed/array_empty.json')['data'])
        self.assertEqual([], out['disks'])


if __name__ == '__main__':
    unittest.main()
