import json
import tempfile
import threading
import unittest

import context  # noqa: F401
import store

GOLEM = {'id': 'a1b2', 'name': 'Golem', 'address': '192.168.2.248', 'port': 15137,
         'tier': 0, 'enabled': True}
RAVEN = {'id': 'b2c3', 'name': 'Raven', 'address': '192.168.2.19', 'port': 29220,
         'tier': 0, 'enabled': True}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(tempfile.mkdtemp())

    def test_the_connection_is_usable_from_another_thread(self):
        # The daemon polls on a worker pool and answers the control socket on a
        # listener thread. sqlite3's default refuses any use off the creating
        # thread, which broke every poll and every status call on Raven.
        seen = []
        worker = threading.Thread(target=lambda: seen.append(
            self.conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]))
        worker.start()
        worker.join()
        # The row count is irrelevant (subclasses seed nodes); what is asserted
        # is that the query RAN off-thread instead of raising ProgrammingError.
        self.assertEqual(1, len(seen), 'the connection must cross threads')

    def tearDown(self):
        self.conn.close()

    def ids(self):
        return sorted(r['id'] for r in self.conn.execute('SELECT id FROM nodes'))


class TestSyncRegistry(StoreCase):
    def test_adds_new_nodes(self):
        result = store.sync_registry(self.conn, [GOLEM, RAVEN])
        self.assertEqual(['a1b2', 'b2c3'], sorted(result['added']))
        self.assertEqual(['a1b2', 'b2c3'], self.ids())

    def test_sync_is_idempotent(self):
        store.sync_registry(self.conn, [GOLEM])
        result = store.sync_registry(self.conn, [GOLEM])
        self.assertEqual([], result['added'])
        self.assertEqual([], result['removed'])
        self.assertEqual(['a1b2'], self.ids())

    def test_rename_and_readdress_are_applied(self):
        store.sync_registry(self.conn, [GOLEM])
        moved = dict(GOLEM, name='Golem-2', address='10.0.0.5', port=443, enabled=False)
        result = store.sync_registry(self.conn, [moved])
        self.assertEqual(['a1b2'], result['updated'])
        row = self.conn.execute('SELECT * FROM nodes WHERE id=?', ('a1b2',)).fetchone()
        self.assertEqual(('Golem-2', '10.0.0.5', 443, 0), (row['name'], row['address'], row['port'], row['enabled']))

    def test_added_at_survives_an_update(self):
        store.sync_registry(self.conn, [GOLEM])
        first = self.conn.execute('SELECT added_at FROM nodes WHERE id=?', ('a1b2',)).fetchone()[0]
        store.sync_registry(self.conn, [dict(GOLEM, name='Renamed')])
        self.assertEqual(first, self.conn.execute(
            'SELECT added_at FROM nodes WHERE id=?', ('a1b2',)).fetchone()[0])

    def test_node_dropped_from_cfg_is_deleted_with_its_data(self):
        # The flash cfg is the authoritative registry (spec §2).
        store.sync_registry(self.conn, [GOLEM, RAVEN])
        store.upsert_state(self.conn, 'b2c3', 'info', 'ok', payload={'hostname': 'Raven'})
        store.add_samples(self.conn, 'b2c3', [('cpu.percent', 4.0)])
        store.log_event(self.conn, 'poll_fail', 'timeout', node_id='b2c3')

        result = store.sync_registry(self.conn, [GOLEM])
        self.assertEqual(['b2c3'], result['removed'])
        self.assertEqual(['a1b2'], self.ids())
        for table in ('node_state', 'samples', 'events'):
            left = self.conn.execute(
                'SELECT COUNT(*) FROM %s WHERE node_id=?' % table, ('b2c3',)).fetchone()[0]
            self.assertEqual(0, left, table)

    def test_empty_cfg_clears_the_registry(self):
        store.sync_registry(self.conn, [GOLEM])
        self.assertEqual(['a1b2'], store.sync_registry(self.conn, [])['removed'])
        self.assertEqual([], self.ids())


class TestUpsertState(StoreCase):
    def setUp(self):
        super().setUp()
        store.sync_registry(self.conn, [GOLEM])

    def row(self, domain='info'):
        return self.conn.execute('SELECT * FROM node_state WHERE node_id=? AND domain=?',
                                 ('a1b2', domain)).fetchone()

    def test_ok_writes_payload_and_clears_error(self):
        store.upsert_state(self.conn, 'a1b2', 'info', 'error', error='boom')
        store.upsert_state(self.conn, 'a1b2', 'info', 'ok', payload={'hostname': 'Golem'})
        row = self.row()
        self.assertEqual('ok', row['status'])
        self.assertIsNone(row['error'])
        self.assertEqual({'hostname': 'Golem'}, json.loads(row['payload']))

    def test_error_keeps_the_last_good_payload(self):
        store.upsert_state(self.conn, 'a1b2', 'info', 'ok', payload={'hostname': 'Golem'})
        store.upsert_state(self.conn, 'a1b2', 'info', 'error', error='504 Gateway Time-out')
        row = self.row()
        self.assertEqual('error', row['status'])
        self.assertEqual('504 Gateway Time-out', row['error'])
        self.assertEqual({'hostname': 'Golem'}, json.loads(row['payload']),
                         'last-good payload must survive a failed poll')

    def test_unknown_keeps_the_last_good_payload_too(self):
        store.upsert_state(self.conn, 'a1b2', 'array', 'ok', payload={'state': 'STARTED'})
        store.upsert_state(self.conn, 'a1b2', 'array', 'unknown', error='unreachable')
        self.assertEqual({'state': 'STARTED'}, json.loads(self.row('array')['payload']))

    def test_fetched_at_only_advances_on_ok(self):
        store.upsert_state(self.conn, 'a1b2', 'info', 'ok', payload={}, fetched_at='2026-08-25T10:00:00Z')
        store.upsert_state(self.conn, 'a1b2', 'info', 'error', error='x', fetched_at='2026-08-25T10:00:30Z')
        self.assertEqual('2026-08-25T10:00:00Z', self.row()['fetched_at'],
                         'fetched_at is when the data was last true, not when we last tried')

    def test_rejects_an_unknown_status_value(self):
        with self.assertRaises(ValueError):
            store.upsert_state(self.conn, 'a1b2', 'info', 'degraded')


class TestSamplesAndEvents(StoreCase):
    def setUp(self):
        super().setUp()
        store.sync_registry(self.conn, [GOLEM])

    def test_add_samples_writes_a_row_per_metric(self):
        n = store.add_samples(self.conn, 'a1b2',
                              [('cpu.percent', 12.4), ('mem.percent', 34.9)],
                              ts='2026-08-25T10:00:00Z')
        self.assertEqual(2, n)
        rows = self.conn.execute('SELECT metric, ts, value FROM samples ORDER BY metric').fetchall()
        self.assertEqual([('cpu.percent', '2026-08-25T10:00:00Z', 12.4),
                          ('mem.percent', '2026-08-25T10:00:00Z', 34.9)],
                         [tuple(r) for r in rows])

    def test_add_samples_skips_non_numeric_without_raising(self):
        n = store.add_samples(self.conn, 'a1b2', [('cpu.percent', None), ('mem.percent', 1.0)])
        self.assertEqual(1, n)

    def test_log_event_returns_an_increasing_id(self):
        first = store.log_event(self.conn, 'enroll', 'Golem enrolled', node_id='a1b2')
        second = store.log_event(self.conn, 'daemon', 'started')
        self.assertLess(first, second)
        row = self.conn.execute('SELECT * FROM events WHERE id=?', (second,)).fetchone()
        self.assertEqual(('daemon', 'started', None), (row['kind'], row['message'], row['node_id']))

    def test_touch_last_seen(self):
        store.touch_last_seen(self.conn, 'a1b2', ts='2026-08-25T10:00:00Z')
        self.assertEqual('2026-08-25T10:00:00Z', self.conn.execute(
            'SELECT last_seen FROM nodes WHERE id=?', ('a1b2',)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
