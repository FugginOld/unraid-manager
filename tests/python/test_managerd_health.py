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

    def test_an_unreachable_node_is_unknown(self):
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual('unknown', self.health('overall')['state'])

    def test_one_blind_domain_is_degraded_not_unknown(self):
        m = self.manager(post_fn=good_post(fail=['metrics']))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual('degraded', self.health('overall')['state'])

    def test_since_holds_while_the_state_does_not_change(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        first = self.health('overall')['since']
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        m.run_cycle('a1b2', collector.FAST, 1060.0)
        self.assertEqual(first, self.health('overall')['since'])


class TestThermalOnAnEmptyArray(HealthCycleCase):
    def test_no_temperature_does_not_make_the_node_unknown(self):
        data = dict(FAST_DATA, array=context.fixture_json('seed/array_empty.json')['data'])
        by_query = {collector.DOMAINS[n].query: d for n, d in data.items()}
        m = self.manager(post_fn=lambda a, p, k, q, t: by_query[q])
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual(health.UNKNOWN, self.health('thermal')['state'])
        self.assertEqual('ok', self.health('overall')['state'])


if __name__ == '__main__':
    unittest.main()
