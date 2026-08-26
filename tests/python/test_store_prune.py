import tempfile
import unittest

import context  # noqa: F401
import store

NODE = {'id': 'a1b2', 'name': 'Golem', 'address': '192.168.2.248', 'port': 15137,
        'tier': 0, 'enabled': True}


class TestPrune(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(tempfile.mkdtemp())
        store.sync_registry(self.conn, [NODE])

    def tearDown(self):
        self.conn.close()

    def count(self, table):
        return self.conn.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]

    def test_samples_older_than_seven_days_go(self):
        store.add_samples(self.conn, 'a1b2', [('cpu.percent', 1.0)], ts='2026-08-10T00:00:00Z')
        store.add_samples(self.conn, 'a1b2', [('cpu.percent', 2.0)], ts='2026-08-24T00:00:00Z')
        result = store.prune(self.conn, now='2026-08-25T00:00:00Z')
        self.assertEqual(1, result['samples'])
        self.assertEqual(1, self.count('samples'))

    def test_a_sample_exactly_at_the_boundary_is_kept(self):
        store.add_samples(self.conn, 'a1b2', [('cpu.percent', 1.0)], ts='2026-08-18T00:00:00Z')
        self.assertEqual(0, store.prune(self.conn, now='2026-08-25T00:00:00Z')['samples'])

    def test_events_are_capped_keeping_the_newest(self):
        for i in range(25):
            store.log_event(self.conn, 'poll_fail', 'failure %d' % i, node_id='a1b2')
        result = store.prune(self.conn, event_cap=10)
        self.assertEqual(15, result['events'])
        self.assertEqual(10, self.count('events'))
        kept = [r[0] for r in self.conn.execute('SELECT message FROM events ORDER BY id')]
        self.assertEqual('failure 15', kept[0])
        self.assertEqual('failure 24', kept[-1])

    def test_under_the_cap_deletes_nothing(self):
        store.log_event(self.conn, 'daemon', 'started')
        self.assertEqual(0, store.prune(self.conn, event_cap=10)['events'])

    def test_prune_on_an_empty_database_is_a_no_op(self):
        self.assertEqual({'samples': 0, 'events': 0, 'vacuumed': False}, store.prune(self.conn))

    def test_vacuum_runs_when_asked(self):
        self.assertTrue(store.prune(self.conn, vacuum=True)['vacuumed'])

    def test_malformed_timestamp_is_left_alone_not_deleted(self):
        # Never let a bad row become a reason to delete good data.
        self.conn.execute("INSERT INTO samples(node_id,metric,ts,value) VALUES('a1b2','x','garbage',1.0)")
        self.conn.commit()
        store.prune(self.conn, now='2026-08-25T00:00:00Z')
        self.assertEqual(1, self.count('samples'))


if __name__ == '__main__':
    unittest.main()
