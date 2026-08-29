import datetime
import tempfile
import unittest

import context
import collector
import gqlclient
import health
import managerd
import store

KEY = 'managerd-health-key-01234567890123456789012'
NODE = {'id': 'a1b2', 'name': 'Golem', 'address': '192.168.2.248', 'port': 15137,
        'tier': 0, 'enabled': True}

FAST_DATA = {
    'info': context.fixture_json('seed/info.json')['data'],
    'array': context.fixture_json('seed/array_populated.json')['data'],
    'shares': context.fixture_json('seed/shares.json')['data'],
    'notifications': context.fixture_json('seed/notifications.json')['data'],
    'metrics': context.fixture_json('seed/metrics.json')['data'],
    'parity': context.fixture_json('seed/parity.json')['data'],
}


def good_post(fail=()):
    by_query = {collector.DOMAINS[n].query: d for n, d in FAST_DATA.items()}
    failing = {collector.DOMAINS[n].query for n in fail}

    def post_fn(address, port, key, query, timeout):
        if query in failing:
            raise gqlclient.TransportError('connection refused')
        if query in by_query:
            return by_query[query]
        raise gqlclient.TransportError('connection refused')
    return post_fn


class HealthCycleCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.conn = store.connect(self.dir)
        self.cfg = {'db_path': self.dir, 'poll_fast': 30, 'poll_slow': 600,
                    'capacity_high_water': 90, 'temp_warn': 50, 'temp_crit': 60,
                    'error_window_min': 15}

    def manager(self, post_fn=None):
        m = managerd.Manager(self.conn, self.cfg, keys_dir=self.dir,
                             post_fn=post_fn or good_post())
        m._read_nodes = lambda: [NODE]
        m._read_key = lambda node_id: KEY
        m.reload()
        return m

    def health(self, indicator):
        return store.read_health(self.conn, 'a1b2').get(indicator)

    def tearDown(self):
        self.conn.close()


class TestHealthIsPersisted(HealthCycleCase):
    def test_a_fast_cycle_writes_every_indicator(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        for indicator in ('array_state', 'capacity', 'thermal', 'disk_errors', 'overall'):
            self.assertIsNotNone(self.health(indicator), indicator)

    def test_the_basis_is_stored_for_the_ui(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertIn('%', self.health('capacity')['basis'])

    def test_a_slow_cycle_does_not_touch_health(self):
        # Health is computed from fast-lane payloads; the slow lane has none.
        m = self.manager()
        m.run_cycle('a1b2', collector.SLOW, 1000.0)
        self.assertIsNone(self.health('capacity'))


class TestHysteresisAcrossCycles(HealthCycleCase):
    def test_one_bad_cycle_does_not_flip_the_indicator(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)     # capacity warn proposed
        self.assertEqual('ok', self.health('capacity')['state'],
                         'the first sighting must only arm the counter')
        self.assertEqual('warn', self.health('capacity')['pending_state'])

    def test_two_agreeing_cycles_escalate(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual('warn', self.health('capacity')['state'])

    def test_counters_survive_a_daemon_restart(self):
        # The whole reason pending_count is a column and not an attribute.
        first = self.manager()
        first.run_cycle('a1b2', collector.FAST, 1000.0)
        second = self.manager()                     # a fresh Manager, same database
        second.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual('warn', self.health('capacity')['state'])


class TestErrorWindow(HealthCycleCase):
    def test_the_window_looks_BACKWARDS_not_forwards(self):
        # Flipping the timedelta's sign makes the cutoff a future timestamp, so
        # recent_samples always returns nothing and disk_errors is permanently
        # unknown - a dead indicator that looks fine. Nothing else pins the sign.
        #
        # TWO cycles, because first sighting only arms the counter: one call
        # leaves this at ok/pending=warn by design. Same shape as
        # test_two_agreeing_cycles_escalate.
        m = self.manager()
        store.add_samples(self.conn, 'a1b2', [('array.errors_total', 0)],
                          ts='2026-08-27T11:55:00Z')
        store.add_samples(self.conn, 'a1b2', [('array.errors_total', 7)],
                          ts='2026-08-27T11:59:00Z')
        m._update_health('a1b2', [], '2026-08-27T12:00:00Z')
        first = store.read_health(self.conn, 'a1b2')['disk_errors']
        self.assertEqual('warn', first['pending_state'], 'the rise was seen at once')

        m._update_health('a1b2', [], '2026-08-27T12:00:30Z')
        row = store.read_health(self.conn, 'a1b2')['disk_errors']
        self.assertEqual('warn', row['state'], 'samples inside the window must be seen')

    def test_a_sample_older_than_the_window_is_ignored(self):
        m = self.manager()
        store.add_samples(self.conn, 'a1b2', [('array.errors_total', 0)],
                          ts='2026-08-27T10:00:00Z')
        store.add_samples(self.conn, 'a1b2', [('array.errors_total', 7)],
                          ts='2026-08-27T10:05:00Z')
        m._update_health('a1b2', [], '2026-08-27T12:00:00Z')
        row = store.read_health(self.conn, 'a1b2')['disk_errors']
        self.assertEqual('unknown', row['state'], 'an hour-old rise is not news')


class TestOverall(HealthCycleCase):
    def test_a_healthy_node_with_a_warn_indicator_is_degraded(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual('degraded', self.health('overall')['state'])

    def test_one_failed_cycle_is_degraded_not_unknown(self):
        """P1 triage P2-3. `overall` used to re-derive `unknown` from a single
        cycle, while the scheduler's UNKNOWN_AFTER=3 still called the same node
        reachable - two definitions of one word reaching the operator from two
        clocks. A transient turned a card grey and back inside a minute, which
        is how an operator learns to stop believing a colour.

        One failed cycle is honest as `degraded`: we tried, and nothing
        answered. It is not yet `unknown`.
        """
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual('degraded', self.health('overall')['state'])

    def test_two_failed_cycles_are_still_not_unknown(self):
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        for t in (1000.0, 1030.0):
            m.run_cycle('a1b2', collector.FAST, t)
        self.assertEqual('degraded', self.health('overall')['state'])

    def test_an_unreachable_node_is_unknown_on_the_third_cycle(self):
        # The threshold the scheduler already promised, now the only one.
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        for t in (1000.0, 1030.0, 1060.0):
            m.run_cycle('a1b2', collector.FAST, t)
        self.assertEqual('unknown', self.health('overall')['state'])

    def test_the_chip_and_the_scheduler_never_disagree(self):
        # Stated as the property rather than as a count, so moving
        # UNKNOWN_AFTER moves both halves together or fails here.
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        for i in range(managerd.UNKNOWN_AFTER + 2):
            m.run_cycle('a1b2', collector.FAST, 1000.0 + 30 * i)
            self.assertEqual(m.scheduler.is_unknown('a1b2'),
                             self.health('overall')['state'] == 'unknown',
                             'cycle %d' % (i + 1))

    def test_one_success_puts_a_node_back_before_the_threshold(self):
        posts = [good_post(fail=FAST_DATA.keys())] * 3 + [good_post()]
        m = self.manager(post_fn=lambda *a, **k: posts[0](*a, **k))
        for i in range(3):
            m.run_cycle('a1b2', collector.FAST, 1000.0 + 30 * i)
        self.assertEqual('unknown', self.health('overall')['state'])
        posts.pop(0); posts.pop(0); posts.pop(0)
        m.run_cycle('a1b2', collector.FAST, 1090.0)
        self.assertNotEqual('unknown', self.health('overall')['state'])

    def test_one_blind_domain_is_degraded_not_unknown(self):
        m = self.manager(post_fn=good_post(fail=['metrics']))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual('degraded', self.health('overall')['state'])

    def _fake_clock(self):
        """Give every cycle its own second, deterministically.

        The previous version of the test below asserted that `overall`'s
        `since` held across three cycles - but with the Golem fixture, capacity
        escalates ok -> warn on the second cycle, so overall legitimately
        becomes degraded and `since` SHOULD move. It passed only because all
        three cycles ran inside one wall-clock second, which made the
        re-stamped value byte-identical to the original. On a loaded machine it
        failed about one run in six.

        A test that passes because the clock did not tick is not testing what
        it names, so the clock is controlled here and both directions are
        asserted below.
        """
        ticks = iter(['2026-08-28T10:%02d:00Z' % m for m in range(10, 40)])
        original = store.utcnow
        store.utcnow = lambda: next(ticks)
        self.addCleanup(setattr, store, 'utcnow', original)

    def test_since_holds_for_an_indicator_whose_state_does_not_change(self):
        self._fake_clock()
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        first = self.health('array_state')['since']
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        m.run_cycle('a1b2', collector.FAST, 1060.0)
        row = self.health('array_state')
        self.assertEqual('ok', row['state'])
        self.assertEqual(first, row['since'], 'since must not move while the state holds')
        self.assertNotEqual(first, row['updated_at'],
                            'updated_at must advance, or a stale row is invisible')

    def test_since_moves_when_the_state_actually_changes(self):
        # The other half, and the reason the old test could pass by accident:
        # capacity escalates on the second cycle, so overall goes ok ->
        # degraded and this timestamp is supposed to move.
        self._fake_clock()
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        first = self.health('overall')
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        m.run_cycle('a1b2', collector.FAST, 1060.0)
        second = self.health('overall')
        self.assertEqual('ok', first['state'])
        self.assertEqual('degraded', second['state'])
        self.assertNotEqual(first['since'], second['since'],
                            '"degraded for how long" must date from the change')


class TestThermalOnAnEmptyArray(HealthCycleCase):
    def _empty_array_manager(self):
        data = dict(FAST_DATA, array=context.fixture_json('seed/array_empty.json')['data'])
        by_query = {collector.DOMAINS[n].query: d for n, d in data.items()}
        return self.manager(post_fn=lambda a, p, k, q, t: by_query[q])

    def test_no_temperature_does_not_make_the_node_unknown(self):
        # Nothing stored from the slow lane yet: still unknown, still not a
        # reason to call the whole node unreachable.
        m = self._empty_array_manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual(health.UNKNOWN, self.health('thermal')['state'])
        self.assertEqual('ok', self.health('overall')['state'])

    def test_a_stale_inventory_is_not_used_to_judge_a_temperature(self):
        """upsert_state retains the last good payload across a failed poll.

        Golem's disks lane 504s persistently (P1 exit F-9), so without an age
        bound a reading from a disk that has since been pulled would hold
        thermal at WARN forever: the proposal never changes, so hysteresis can
        never clear it, and the failing slow-lane domain contributes nothing to
        the fast-lane rollup that would explain why.
        """
        store.upsert_state(self.conn, 'a1b2', 'disks', 'ok',
                           payload={'disks': [{'device': '/dev/sda', 'temp': 61}]},
                           fetched_at='2020-01-01T00:00:00Z')
        m = self._empty_array_manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        row = self.health('thermal')
        self.assertEqual(health.UNKNOWN, row['state'],
                         'a years-old inventory must not decide a temperature')
        self.assertNotIn('61', str(row['value']))

    def test_an_undateable_inventory_is_not_used_either(self):
        # A payload we cannot date is one we cannot vouch for. Treating "no
        # timestamp" as "current" is the same fail-open the age bound exists to
        # close - and it is reachable: upsert_state stores fetched_at only for
        # a poll that succeeded.
        store.upsert_state(self.conn, 'a1b2', 'disks', 'unknown',
                           payload={'disks': [{'device': '/dev/sda', 'temp': 61}]},
                           error='HTTP 504', fetched_at=None)
        m = self._empty_array_manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual(health.UNKNOWN, self.health('thermal')['state'])

    # ABSOLUTE seconds, not `INVENTORY_STALE_AFTER * poll_slow`. Deriving the
    # boundary from the constant under test moves the test with it: the first
    # version of these two did exactly that, and mutating the constant from 3
    # to 100 - 16.6 hours, long enough for the failure to return for most of a
    # day - left both green. With poll_slow at 600 the bound is 1800 seconds,
    # and that number is written here so that changing it must be deliberate.
    BOUND_SECONDS = 1800

    def _inventory_aged(self, seconds, temp):
        when = (datetime.datetime(2026, 8, 28, 12, 0, 0)
                - datetime.timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
        store.upsert_state(self.conn, 'a1b2', 'disks', 'ok',
                           payload={'disks': [{'device': '/dev/sda', 'temp': temp}]},
                           fetched_at=when)
        m = self._empty_array_manager()
        m._update_health('a1b2', [], '2026-08-28T12:00:00Z')

    def test_the_constant_is_the_bound_the_tests_below_assume(self):
        self.assertEqual(3, managerd.Manager.INVENTORY_STALE_AFTER)
        self.assertEqual(self.BOUND_SECONDS,
                         managerd.Manager.INVENTORY_STALE_AFTER * self.cfg['poll_slow'])

    def test_just_past_the_bound_is_refused(self):
        self._inventory_aged(self.BOUND_SECONDS + 60, 61)
        self.assertEqual(health.UNKNOWN, self.health('thermal')['state'])

    def test_just_inside_the_bound_is_still_used(self):
        self._inventory_aged(self.BOUND_SECONDS - 60, 41)
        self.assertEqual(41, self.health('thermal')['value'])

    def test_a_recent_inventory_is_still_used(self):
        # The other side of the bound: one missed slow poll is ordinary, and
        # dropping to array-only on the first miss would flap an empty-array
        # box between a real temperature and "unknown" every ten minutes.
        store.upsert_state(self.conn, 'a1b2', 'disks', 'ok',
                           payload={'disks': [{'device': '/dev/sda', 'temp': 41}]},
                           fetched_at=store.utcnow())
        m = self._empty_array_manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual(41, self.health('thermal')['value'])

    def test_the_stored_disk_inventory_is_used_when_the_array_has_no_disks(self):
        """P1 exit F-4, at the level that actually shipped broken.

        health.evaluate_thermal takes the inventory, but the daemon evaluates
        on the FAST lane and the inventory is a SLOW-lane payload - so a fix
        that stops at the pure function reaches production not at all. This is
        Raven: an empty array, eleven disks the array does not know about, and
        a card that read "no disk temperature reported" while the Disks tab
        showed 33-40 C for the same box.
        """
        store.upsert_state(self.conn, 'a1b2', 'disks', 'ok', payload={
            'disks': [{'device': '/dev/sda', 'temp': 37},
                      {'device': '/dev/sdb', 'temp': 41}]},
            fetched_at=store.utcnow())
        m = self._empty_array_manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        row = self.health('thermal')
        self.assertEqual(health.OK, row['state'])
        self.assertEqual(41, row['value'])
        self.assertIn('inventory', row['basis'])




class TestThresholdsReachTheEvaluator(HealthCycleCase):
    """Every threshold config resolves must actually be handed to health.

    HEALTH_THRESHOLDS is that hand-off, and a key missing from it is dropped
    silently while the evaluator falls back to its own constant. capacity_watch
    shipped dead exactly that way for a whole branch: config.py resolved it
    from Unraid's Disk Settings, the pure function honoured it, and nothing in
    between passed it along - with the pure-function test green throughout.
    """

    def test_every_key_config_resolves_is_forwarded(self):
        import config
        missing = set(config.THRESHOLD_BOUNDS) - set(managerd.Manager.HEALTH_THRESHOLDS)
        self.assertEqual(set(), missing,
                         'these thresholds are configurable but never reach health.evaluate')

    def test_the_capacity_watch_level_changes_the_verdict(self):
        # The Golem capture is 93.3% full: above a 70 watch, below a 99 warn.
        # Two cycles because the first sighting only arms the counter.
        m = self.manager()
        m.cfg['capacity_watch'] = 70
        m.cfg['capacity_high_water'] = 99
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual('watch', self.health('capacity')['state'])

    def test_a_high_watch_level_leaves_the_same_fleet_ok(self):
        # The other direction, so the test above cannot pass on a constant.
        m = self.manager()
        m.cfg['capacity_watch'] = 95
        m.cfg['capacity_high_water'] = 99
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual('ok', self.health('capacity')['state'])


if __name__ == '__main__':
    unittest.main()
