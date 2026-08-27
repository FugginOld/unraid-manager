import logging
import os
import tempfile
import threading
import unittest

import context
import collector
import config          # noqa: F401
import gqlclient
import managerd
import store

KEY = 'managerd-test-key-012345678901234567890123456'
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


class ManagerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.conn = store.connect(self.dir)
        self.published = []
        self.cfg = {'db_path': self.dir, 'poll_fast': 30, 'poll_slow': 600}

    def manager(self, post_fn=None, nodes=(NODE,)):
        m = managerd.Manager(self.conn, self.cfg, keys_dir=self.dir,
                             post_fn=post_fn or good_post(),
                             publish_fn=self.published.append)
        m._read_nodes = lambda: list(nodes)          # inject the registry
        m._read_key = lambda node_id: KEY
        m.reload()
        return m

    def state(self, domain, node_id='a1b2'):
        return self.conn.execute('SELECT * FROM node_state WHERE node_id=? AND domain=?',
                                 (node_id, domain)).fetchone()

    def tearDown(self):
        self.conn.close()


class TestReload(ManagerCase):
    def test_reload_syncs_the_registry_into_sqlite(self):
        self.manager()
        self.assertEqual(1, self.conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0])

    def test_reload_logs_an_event(self):
        self.manager()
        kinds = [r[0] for r in self.conn.execute('SELECT kind FROM events')]
        self.assertIn('enroll', kinds)

    def test_a_removed_node_is_gone_after_reload(self):
        m = self.manager()
        m._read_nodes = lambda: []
        m.reload()
        self.assertEqual(0, self.conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0])

    def test_a_disabled_node_is_not_scheduled(self):
        m = self.manager(nodes=[dict(NODE, enabled=False)])
        self.assertEqual([], m.scheduler.due(1000.0))


class TestTmpDbPathGuard(ManagerCase):
    """Spec section 4.4: /tmp is refused on a box that has a pool to use."""

    def test_a_tmp_db_path_is_refused_when_a_pool_is_mounted(self):
        managerd.POOL_MARKER = self.dir            # stand in for /mnt/user
        try:
            with self.assertRaises(ValueError) as ctx:
                managerd.Manager(self.conn, dict(self.cfg, db_path='/tmp/unraid-manager'),
                                 keys_dir=self.dir, post_fn=good_post())
            self.assertIn('/tmp', str(ctx.exception))
        finally:
            managerd.POOL_MARKER = '/mnt/user'

    def test_a_tmp_db_path_is_tolerated_with_no_pool_to_point_at(self):
        managerd.POOL_MARKER = os.path.join(self.dir, 'nosuch')
        try:
            managerd.Manager(self.conn, dict(self.cfg, db_path='/tmp/unraid-manager'),
                             keys_dir=self.dir, post_fn=good_post())
        finally:
            managerd.POOL_MARKER = '/mnt/user'


class TestRunCycle(ManagerCase):
    def test_a_good_fast_cycle_writes_every_domain_ok(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        for name in ('info', 'array', 'shares', 'notifications', 'metrics', 'parity'):
            self.assertEqual('ok', self.state(name)['status'], name)

    def test_a_good_cycle_advances_last_seen(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertIsNotNone(self.conn.execute(
            'SELECT last_seen FROM nodes WHERE id=?', ('a1b2',)).fetchone()[0])

    def test_a_good_cycle_writes_samples(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        metrics = {r[0] for r in self.conn.execute('SELECT DISTINCT metric FROM samples')}
        self.assertIn('cpu.percent', metrics)
        self.assertIn('array.bytes_used', metrics)

    def test_an_unreachable_node_marks_every_domain_unknown(self):
        # Constraint 5 end to end: unreachable is unknown, never ok, never error.
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        for name in FAST_DATA:
            self.assertEqual('unknown', self.state(name)['status'], name)

    def test_an_unreachable_node_does_not_advance_last_seen(self):
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertIsNone(self.conn.execute(
            'SELECT last_seen FROM nodes WHERE id=?', ('a1b2',)).fetchone()[0])

    def test_one_failing_domain_costs_only_that_domain(self):
        # Constraint 1, the whole reason for per-domain queries.
        m = self.manager(post_fn=good_post(fail=['metrics']))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual('unknown', self.state('metrics')['status'])
        self.assertEqual('ok', self.state('info')['status'])
        self.assertEqual('ok', self.state('array')['status'])

    def test_a_partial_cycle_still_counts_as_reachable(self):
        m = self.manager(post_fn=good_post(fail=['metrics']))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual(0, m.scheduler.consecutive_failures('a1b2'))

    def test_repeated_total_failure_backs_off_and_goes_unknown(self):
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        for _ in range(3):
            m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertTrue(m.scheduler.is_unknown('a1b2'))
        self.assertEqual(240, m.scheduler.interval('a1b2'))

    def test_a_missing_key_is_unknown_not_a_crash(self):
        m = self.manager()
        m._read_key = lambda node_id: None
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual('unknown', self.state('info')['status'])
        self.assertIn('key', self.state('info')['error'].lower())

    def test_poll_failure_is_journalled_once_per_transition(self):
        m = self.manager(post_fn=good_post(fail=FAST_DATA.keys()))
        for _ in range(4):
            m.run_cycle('a1b2', collector.FAST, 1000.0)
        fails = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind='poll_fail'").fetchone()[0]
        self.assertEqual(1, fails, 'a node down for an hour must not write 120 journal rows')


class TestThreading(ManagerCase):
    def test_a_cycle_and_a_status_call_can_run_on_different_threads(self):
        # The pool polls, the control socket answers, and both go through one
        # sqlite connection. On Raven the first status call raised
        # ProgrammingError and every poll would have done the same.
        m = self.manager()
        errors = []

        def poll():
            try:
                m.run_cycle('a1b2', collector.FAST, 1000.0)
            except Exception as exc:            # noqa: BLE001 - the point of the test
                errors.append(exc)

        worker = threading.Thread(target=poll)
        worker.start()
        worker.join()
        self.assertEqual([], errors)
        self.assertEqual(1, len(m.status()['nodes']))
        self.assertEqual('ok', self.state('info')['status'])


class TestPublish(ManagerCase):
    def test_a_changed_cycle_publishes_a_compact_delta(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertEqual(1, len(self.published))
        msg = self.published[0]
        self.assertEqual('a1b2', msg['node_id'])
        self.assertEqual('ok', msg['domains']['info'])
        self.assertIn('ts', msg)

    def test_an_unchanged_cycle_publishes_nothing(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual(1, len(self.published))

    def test_the_delta_carries_no_payload_and_no_key(self):
        import json as _json
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        text = _json.dumps(self.published[0])
        self.assertNotIn(KEY, text)
        self.assertNotIn('Golem', text, 'the ping says something changed, not what')

    def test_a_publish_failure_disables_publishing_and_does_not_raise(self):
        def boom(msg):
            raise OSError('nchan is not listening')
        m = managerd.Manager(self.conn, self.cfg, keys_dir=self.dir,
                             post_fn=good_post(), publish_fn=boom)
        m._read_nodes = lambda: [NODE]
        m._read_key = lambda node_id: KEY
        m.reload()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        self.assertFalse(m.publishing)
        m.run_cycle('a1b2', collector.SLOW, 1001.0)     # must not raise


class TestNchanEndpoint(unittest.TestCase):
    CONF = '''
    server {
        listen unix:/var/run/nginx-pub.sock;
        location ~ /pub/(.*)$ {
            nchan_publisher;
            nchan_channel_id "$1";
        }
    }
    '''

    def test_finds_the_publisher_socket(self):
        self.assertEqual('/var/run/nginx-pub.sock', managerd.nchan_endpoint(self.CONF))

    def test_absent_publisher_returns_none(self):
        self.assertIsNone(managerd.nchan_endpoint('server { listen 80; }'))

    def test_empty_input_returns_none(self):
        self.assertIsNone(managerd.nchan_endpoint(''))


class TestStatusAndHandlers(ManagerCase):
    def test_status_reports_each_node(self):
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        st = m.status()
        self.assertEqual(1, len(st['nodes']))
        self.assertEqual('a1b2', st['nodes'][0]['id'])
        self.assertIn('uptime', st)

    def test_handlers_cover_the_documented_commands(self):
        self.assertEqual({'status', 'poll_now', 'test_node', 'reload', 'prune'},
                         set(self.manager().handlers()))

    def test_test_node_handler_returns_a_probe_report_and_no_key(self):
        import json as _json
        m = self.manager()
        report = m.handlers()['test_node'](
            {'address': '192.168.2.248', 'port': 15137, 'key': KEY})
        self.assertEqual('ok', report['verdict'])
        self.assertNotIn(KEY, _json.dumps(report))

    def test_test_node_persists_nothing(self):
        m = self.manager()
        before = self.conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]
        m.handlers()['test_node']({'address': 'h', 'port': 1, 'key': KEY})
        self.assertEqual(before, self.conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0])

    def test_test_node_by_id_reads_the_key_from_flash(self):
        # The Test button beside an enrolled node sends no key: the daemon has
        # it, and the browser must never need it back.
        m = self.manager()
        self.assertEqual('ok', m.handlers()['test_node']({'node_id': 'a1b2'})['verdict'])

    def test_test_node_by_id_for_an_unknown_node_is_refused(self):
        m = self.manager()
        with self.assertRaises(ValueError):
            m.handlers()['test_node']({'node_id': 'nosuch'})

    def test_test_node_by_id_with_no_key_on_file_is_refused(self):
        m = self.manager()
        m._read_key = lambda node_id: None
        with self.assertRaises(ValueError):
            m.handlers()['test_node']({'node_id': 'a1b2'})


class TestLogging(unittest.TestCase):
    def test_setup_logging_rotates_at_one_megabyte_twice(self):
        path = os.path.join(tempfile.mkdtemp(), 'managerd.log')
        logger = managerd.setup_logging(path)
        handler = logger.handlers[0]
        self.assertEqual(1024 * 1024, handler.maxBytes)
        self.assertEqual(2, handler.backupCount)
        logger.info('hello')
        handler.close()
        logger.handlers = []
        with open(path, 'r', encoding='utf-8') as fh:
            self.assertIn('hello', fh.read())

    def tearDown(self):
        logging.getLogger('managerd').handlers = []


if __name__ == '__main__':
    unittest.main()
