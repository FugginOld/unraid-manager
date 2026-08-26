import unittest

import context  # noqa: F401
import managerd


class TestDueness(unittest.TestCase):
    def setUp(self):
        self.s = managerd.Scheduler(poll_fast=30, poll_slow=600)
        self.s.set_nodes(['a'])

    def test_everything_is_due_at_the_first_tick(self):
        self.assertEqual([('a', 'fast'), ('a', 'slow')], sorted(self.s.due(1000.0)))

    def test_nothing_is_due_again_immediately(self):
        self.s.due(1000.0)
        self.assertEqual([], self.s.due(1001.0))

    def test_fast_comes_due_after_its_interval(self):
        self.s.due(1000.0)
        self.assertEqual([], self.s.due(1029.0))
        self.assertEqual([('a', 'fast')], self.s.due(1030.0))

    def test_slow_comes_due_after_its_own_interval(self):
        self.s.due(1000.0)
        self.assertEqual([('a', 'fast')], self.s.due(1030.0))
        self.assertEqual([('a', 'fast'), ('a', 'slow')], sorted(self.s.due(1600.0)))

    def test_due_marks_dispatched_so_a_slow_poll_is_not_started_twice(self):
        # The 90s disks query outlives several one-second ticks. Handing it out
        # again mid-flight is how you get eight concurrent 90s requests.
        self.s.due(1000.0)
        self.s.due(1600.0)
        self.assertEqual([], self.s.due(1601.0))

    def test_two_nodes_are_independent(self):
        self.s.set_nodes(['a', 'b'])
        self.assertEqual(4, len(self.s.due(1000.0)))
        self.s.record('a', any_ok=False)
        self.assertEqual(30, self.s.interval('b'))


class TestNodeMembership(unittest.TestCase):
    def setUp(self):
        self.s = managerd.Scheduler(poll_fast=30, poll_slow=600)

    def test_a_new_node_is_due_at_once(self):
        self.s.set_nodes(['a'])
        self.s.due(1000.0)
        self.s.set_nodes(['a', 'b'])
        self.assertEqual([('b', 'fast'), ('b', 'slow')], sorted(self.s.due(1001.0)))

    def test_a_removed_node_stops_being_scheduled(self):
        self.s.set_nodes(['a', 'b'])
        self.s.due(1000.0)
        self.s.set_nodes(['a'])
        self.assertEqual([('a', 'fast')], self.s.due(1030.0))

    def test_resync_keeps_backoff_for_a_node_that_stayed(self):
        self.s.set_nodes(['a'])
        for _ in range(2):
            self.s.record('a', any_ok=False)
        self.s.set_nodes(['a', 'b'])
        self.assertEqual(2, self.s.consecutive_failures('a'))


class TestBackoff(unittest.TestCase):
    def setUp(self):
        self.s = managerd.Scheduler(poll_fast=30, poll_slow=600)
        self.s.set_nodes(['a'])

    def test_intervals_double_and_cap_at_600(self):
        seen = []
        for _ in range(8):
            self.s.record('a', any_ok=False)
            seen.append(self.s.interval('a'))
        self.assertEqual([60, 120, 240, 480, 600, 600, 600, 600], seen)

    def test_any_success_resets_backoff_completely(self):
        for _ in range(5):
            self.s.record('a', any_ok=False)
        self.s.record('a', any_ok=True)
        self.assertEqual(30, self.s.interval('a'))
        self.assertEqual(0, self.s.consecutive_failures('a'))

    def test_backed_off_node_is_scheduled_at_the_longer_interval(self):
        self.s.due(1000.0)
        self.s.record('a', any_ok=False)          # 30 -> 60
        self.assertEqual([], self.s.due(1059.0))
        self.assertEqual([('a', 'fast')], self.s.due(1060.0))

    def test_backoff_never_slows_the_slow_lane(self):
        # The slow lane is already 600s; doubling it too would mean a recovered
        # node showing a stale disk list for an hour.
        self.s.due(1000.0)
        for _ in range(6):
            self.s.record('a', any_ok=False)
        self.assertIn(('a', 'slow'), self.s.due(1600.0))


class TestUnknownThreshold(unittest.TestCase):
    def setUp(self):
        self.s = managerd.Scheduler(poll_fast=30, poll_slow=600)
        self.s.set_nodes(['a'])

    def test_two_failures_is_not_yet_unknown(self):
        self.s.record('a', any_ok=False)
        self.s.record('a', any_ok=False)
        self.assertFalse(self.s.is_unknown('a'))

    def test_three_failures_is_unknown(self):
        for _ in range(3):
            self.s.record('a', any_ok=False)
        self.assertTrue(self.s.is_unknown('a'))

    def test_one_success_clears_unknown(self):
        for _ in range(5):
            self.s.record('a', any_ok=False)
        self.s.record('a', any_ok=True)
        self.assertFalse(self.s.is_unknown('a'))

    def test_a_never_polled_node_is_not_unknown(self):
        self.assertFalse(self.s.is_unknown('a'))


class TestPollNow(unittest.TestCase):
    def setUp(self):
        self.s = managerd.Scheduler(poll_fast=30, poll_slow=600)
        self.s.set_nodes(['a', 'b'])
        self.s.due(1000.0)

    def test_poll_now_for_one_node_makes_both_its_lanes_due(self):
        self.s.poll_now('a')
        self.assertEqual([('a', 'fast'), ('a', 'slow')], sorted(self.s.due(1001.0)))

    def test_poll_now_for_everything(self):
        self.s.poll_now()
        self.assertEqual(4, len(self.s.due(1001.0)))

    def test_poll_now_does_not_reset_backoff(self):
        # An operator forcing a poll is asking for data, not asserting health.
        self.s.record('a', any_ok=False)
        self.s.poll_now('a')
        self.assertEqual(60, self.s.interval('a'))

    def test_poll_now_for_an_unknown_node_id_is_ignored(self):
        self.s.poll_now('nosuch')
        self.assertEqual([], self.s.due(1001.0))


if __name__ == '__main__':
    unittest.main()
