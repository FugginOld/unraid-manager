import tempfile
import unittest

import context  # noqa: F401
import store

NODE = {'id': 'a1b2', 'name': 'Golem', 'address': '192.168.2.248', 'port': 15137,
        'tier': 0, 'enabled': True}


class HealthCase(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(tempfile.mkdtemp())
        store.sync_registry(self.conn, [NODE])

    def tearDown(self):
        self.conn.close()


class TestSchema(HealthCase):
    def test_the_table_exists(self):
        self.assertEqual(0, self.conn.execute(
            'SELECT COUNT(*) FROM node_health').fetchone()[0])

    def test_the_schema_version_moved_to_two(self):
        self.assertEqual(2, self.conn.execute('PRAGMA user_version').fetchone()[0])

    def test_an_invalid_state_is_refused_by_the_database(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO node_health(node_id,indicator,state,updated_at) "
                              "VALUES('a1b2','capacity','purple','x')")


class TestUpsert(HealthCase):
    def row(self, indicator='capacity'):
        return store.read_health(self.conn, 'a1b2')[indicator]

    def test_a_first_write_records_since(self):
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'warn', value=93.0,
                            basis='93% used', now='2026-08-27T00:00:00Z')
        self.assertEqual('2026-08-27T00:00:00Z', self.row()['since'])
        self.assertEqual('warn', self.row()['state'])
        self.assertEqual(93.0, self.row()['value'])
        self.assertEqual('93% used', self.row()['basis'])

    def test_an_unchanged_state_keeps_the_original_since(self):
        # "degraded for 4 hours" is the whole point of the column.
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'warn', now='2026-08-27T00:00:00Z')
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'warn', now='2026-08-27T04:00:00Z')
        self.assertEqual('2026-08-27T00:00:00Z', self.row()['since'])

    def test_a_changed_state_resets_since(self):
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'warn', now='2026-08-27T00:00:00Z')
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'ok', now='2026-08-27T04:00:00Z')
        self.assertEqual('2026-08-27T04:00:00Z', self.row()['since'])

    def test_updated_at_always_advances(self):
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'warn', now='2026-08-27T00:00:00Z')
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'warn', now='2026-08-27T04:00:00Z')
        self.assertEqual('2026-08-27T04:00:00Z', self.row()['updated_at'])

    def test_pending_counters_round_trip(self):
        store.upsert_health(self.conn, 'a1b2', 'thermal', 'ok',
                            pending_state='warn', pending_count=1)
        row = self.row('thermal')
        self.assertEqual('warn', row['pending_state'])
        self.assertEqual(1, row['pending_count'])

    def test_read_health_for_an_unknown_node_is_empty(self):
        self.assertEqual({}, store.read_health(self.conn, 'nosuch'))


class TestRegistrySync(HealthCase):
    def test_health_rows_go_with_a_removed_node(self):
        # A re-enrolled address must not inherit a stranger's verdict.
        store.upsert_health(self.conn, 'a1b2', 'capacity', 'warn')
        store.sync_registry(self.conn, [])
        self.assertEqual(0, self.conn.execute(
            'SELECT COUNT(*) FROM node_health').fetchone()[0])


class TestRecentSamples(HealthCase):
    def test_it_returns_only_samples_at_or_after_the_cutoff(self):
        for ts, value in (('2026-08-27T00:00:00Z', 1), ('2026-08-27T00:10:00Z', 2),
                          ('2026-08-27T00:20:00Z', 5)):
            store.add_samples(self.conn, 'a1b2', [('array.errors_total', value)], ts=ts)
        rows = store.recent_samples(self.conn, 'a1b2', 'array.errors_total',
                                    '2026-08-27T00:10:00Z')
        self.assertEqual([('2026-08-27T00:10:00Z', 2.0), ('2026-08-27T00:20:00Z', 5.0)], rows)

    def test_it_is_ascending(self):
        for ts, value in (('2026-08-27T00:20:00Z', 5), ('2026-08-27T00:00:00Z', 1)):
            store.add_samples(self.conn, 'a1b2', [('array.errors_total', value)], ts=ts)
        rows = store.recent_samples(self.conn, 'a1b2', 'array.errors_total', '2026-01-01T00:00:00Z')
        self.assertEqual([ts for ts, _ in rows], sorted(ts for ts, _ in rows))

    def test_another_metric_is_not_included(self):
        store.add_samples(self.conn, 'a1b2', [('cpu.percent', 9)], ts='2026-08-27T00:00:00Z')
        self.assertEqual([], store.recent_samples(self.conn, 'a1b2', 'array.errors_total',
                                                  '2026-01-01T00:00:00Z'))


if __name__ == '__main__':
    unittest.main()
