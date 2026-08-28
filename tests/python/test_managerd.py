import logging
import os
import socket
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
        # Mirrors read_manager_cfg, which always returns every key with a
        # default filled in - a partial cfg would let a test pass against a
        # shape production never produces.
        self.cfg = dict(config.MANAGER_DEFAULTS,
                        db_path=self.dir, poll_fast=30, poll_slow=600)

    def manager(self, post_fn=None, nodes=(NODE,)):
        # dict(...) on purpose: Manager keeps the mapping it is handed, so
        # sharing one with the test would let a mutation reach m.cfg without
        # ever passing through reload().
        m = managerd.Manager(self.conn, dict(self.cfg), keys_dir=self.dir,
                             post_fn=post_fn or good_post(),
                             publish_fn=self.published.append)
        m._read_nodes = lambda: list(nodes)          # inject the registry
        # Stubbed for the same reason as _read_nodes: reload() re-reads
        # manager.cfg, and without this the suite would read the operator's real
        # /boot file when run on a box - green here only because /boot does not
        # exist on the dev machine.
        m._read_manager_cfg = lambda: dict(self.cfg)
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

    def test_every_successful_cycle_publishes_so_the_pane_can_refresh(self):
        """This test used to assert the opposite, and that is why nchan was dead.

        `changed` holds STATUS transitions (ok -> error -> unknown). On a
        healthy fleet nothing ever transitions, so publishing only `if changed`
        meant the daemon never nudged at all - proven on Raven during the P1
        exit trial, where nginx's `total published messages` did not move
        across a forced poll. The 30s fallback timer in the browser had been
        the entire live-update mechanism since P0.

        A poll that succeeded has fresh data behind it - a new fetched_at at
        the very least, which is exactly what the card's "last seen" shows -
        so it is worth a nudge.
        """
        m = self.manager()
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual(2, len(self.published))

    def test_a_repeated_failure_publishes_nothing(self):
        # The other direction still holds: a node that was already failing and
        # is still failing has produced nothing new to look at. Only the
        # transition into failure nudges.
        # self.manager(), NOT a bare Manager(): the helper injects the node
        # registry and calls reload(). Without it run_cycle early-returns at
        # `if node is None`, stores nothing, publishes nothing, and the
        # assertion below holds 0 == 0 no matter what the code does. The first
        # draft of this test did exactly that and could not see `if True:`.
        m = self.manager(post_fn=good_post(fail=tuple(FAST_DATA)))
        m.run_cycle('a1b2', collector.FAST, 1000.0)
        first = len(self.published)
        self.assertEqual(1, first, 'the transition into failure must nudge once')
        m.run_cycle('a1b2', collector.FAST, 1030.0)
        self.assertEqual(first, len(self.published))

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


class TestShutdown(ManagerCase):
    class FakePool(object):
        def __init__(self):
            self.kwargs = None

        def shutdown(self, **kwargs):
            self.kwargs = kwargs

    def test_shutdown_does_not_wait_on_in_flight_polls(self):
        # A slow-lane disks request can run the full 90s timeout. Waiting for it
        # made `rc stop` report failure after 10s on Raven and would stall array
        # stop; the write lock, not the pool, is what must be respected.
        m = self.manager()
        m.pool = self.FakePool()
        codes = []
        managerd.shutdown(m, self.conn, exit_fn=codes.append)
        self.assertEqual({'wait': False, 'cancel_futures': True}, m.pool.kwargs)
        self.assertEqual([0], codes)

    def test_shutdown_closes_the_database(self):
        m = self.manager()
        m.pool = self.FakePool()
        managerd.shutdown(m, self.conn, exit_fn=lambda code: None)
        with self.assertRaises(Exception):
            self.conn.execute('SELECT 1')

    def tearDown(self):
        pass          # shutdown() already closed the connection


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

    RAVEN_CONF = """
    server {
        listen unix:/var/run/nginx.socket default_server;
        location ~ /pub/(.*)$ {
            nchan_publisher;
        }
    }
    """

    def test_finds_it_with_trailing_directives_on_the_listen_line(self):
        # Verbatim shape from Raven's /etc/nginx/conf.d/servers.conf. The path
        # ends at a space here, not at the semicolon, which is what broke
        # discovery on the live box while nchan was sitting right there.
        self.assertEqual('/var/run/nginx.socket', managerd.nchan_endpoint(self.RAVEN_CONF))

    def test_absent_publisher_returns_none(self):
        self.assertIsNone(managerd.nchan_endpoint('server { listen 80; }'))

    def test_empty_input_returns_none(self):
        self.assertIsNone(managerd.nchan_endpoint(''))


class TestPublishOver(unittest.TestCase):
    """The publish REQUEST, not just the discovery of where to send it.

    Nothing exercised _publish_over until the P1 exit trial, and on Raven every
    nudge it had ever sent came back `403 missing nchan_message_buffer_length
    value` - Unraid's publisher location reads that length from a QUERY
    ARGUMENT (`nchan_message_buffer_length $arg_buffer_length`), and we sent
    none. Live updates had never once worked, in two phases, because the reply
    was read into a buffer and dropped on the floor.

    Split into two pure functions rather than tested through a real socket:
    ctl.py made the same call for the same reason, and AF_UNIX does not exist
    on every machine this suite runs on.
    """

    def test_the_publish_path_carries_a_buffer_length(self):
        line = managerd._publish_request({'probe': 1}).split(b'\r\n')[0]
        self.assertIn(b'buffer_length=', line)
        self.assertIn(b'/pub/' + managerd.NCHAN_CHANNEL.encode(), line)

    def test_the_buffer_length_is_a_number_nginx_will_accept(self):
        # `nchan_message_buffer_length $arg_buffer_length` - an empty or
        # non-numeric value is the 403 this whole class exists for.
        line = managerd._publish_request({'probe': 1}).split(b'\r\n')[0].decode()
        value = line.split('buffer_length=')[1].split()[0].split('&')[0]
        self.assertTrue(value.isdigit() and int(value) >= 1, line)

    def test_the_message_is_the_body(self):
        request = managerd._publish_request({'node_id': 'a1b2', 'domains': ['array']})
        head, _, body = request.partition(b'\r\n\r\n')
        self.assertIn(b'"node_id":"a1b2"', body)
        self.assertIn(b'Content-Length: ' + str(len(body)).encode(), head)

    def test_a_refused_publish_raises_so_the_caller_can_report_it(self):
        # Verbatim from Raven, 2026-08-28. Swallowing this is what let a dead
        # feature look healthy in the daemon log for two whole phases.
        with self.assertRaises(OSError):
            managerd._publish_check(
                b'HTTP/1.1 403 Forbidden\r\nContent-Length: 41\r\n\r\n'
                b'missing nchan_message_buffer_length value')

    def test_the_refusal_reason_survives_into_the_error(self):
        with self.assertRaises(OSError) as caught:
            managerd._publish_check(b'HTTP/1.1 403 Forbidden\r\n\r\nnope')
        self.assertIn('403', str(caught.exception))

    def test_201_and_202_are_both_accepted(self):
        managerd._publish_check(b'HTTP/1.1 201 Created\r\n\r\nqueued messages: 1')
        managerd._publish_check(b'HTTP/1.1 202 Accepted\r\n\r\nok')

    def test_a_silent_socket_is_not_a_success(self):
        # recv() returning nothing means we never saw an acceptance. Reading it
        # as one is how the old code turned every failure into a success.
        with self.assertRaises(OSError):
            managerd._publish_check(b'')

    def test_an_unparseable_reply_is_not_a_success(self):
        with self.assertRaises(OSError):
            managerd._publish_check(b'garbage\r\n\r\n')

    def test_a_refused_publish_turns_publishing_off_without_escaping_the_cycle(self):
        # The Manager wrapper keeps its existing contract: one bad publish
        # disables publishing and never propagates out of a poll cycle.
        def boom(_message):
            raise OSError('403 Forbidden')
        m = managerd.Manager.__new__(managerd.Manager)
        m.publishing = True
        m.publish_fn = boom
        m._publish({'probe': 1})
        self.assertFalse(m.publishing)


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




class TestReloadRereadsSettings(ManagerCase):
    """manager.cfg was read once at start, so the settings page was inert.

    Found on Raven 2026-08-27: saving temp_warn=38 left a 46 C node reading
    `ok` because the evaluator still held the 50 the daemon booted with. The
    page reported success and the flash file was correct.
    """

    def test_a_changed_threshold_reaches_the_evaluator(self):
        m = self.manager()
        self.assertEqual(50, m.cfg['temp_warn'])
        self.cfg['temp_warn'] = 38
        m.reload()
        self.assertEqual(38, m.cfg['temp_warn'])

    def test_a_changed_poll_interval_reaches_the_scheduler(self):
        m = self.manager()
        self.assertEqual(30, m.scheduler.poll_fast)
        self.cfg['poll_fast'] = 15
        self.cfg['poll_slow'] = 900
        m.reload()
        self.assertEqual(15, m.scheduler.poll_fast)
        self.assertEqual(900, m.scheduler.poll_slow)

    def test_db_path_is_not_reloadable(self):
        # Repointing the database under a running daemon would mean reopening a
        # connection every worker already holds.
        m = self.manager()
        self.cfg['db_path'] = '/somewhere/else'
        m.reload()
        self.assertEqual(self.dir, m.cfg['db_path'])

    def test_an_unreadable_manager_cfg_does_not_stop_the_registry_reload(self):
        m = self.manager(nodes=())
        def boom():
            raise IOError('flash went away')
        m._read_manager_cfg = boom
        m._read_nodes = lambda: [NODE]
        m.reload()
        self.assertEqual([NODE['id']], [r['id'] for r in
                         self.conn.execute('SELECT id FROM nodes')])
        self.assertEqual(50, m.cfg['temp_warn'])


if __name__ == '__main__':
    unittest.main()
