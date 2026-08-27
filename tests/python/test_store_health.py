import os
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

    def test_the_schema_version_moved_to_three(self):
        # 2 -> 3 for the migration hook (widened CHECK); see TestMigration below.
        self.assertEqual(3, self.conn.execute('PRAGMA user_version').fetchone()[0])

    def test_an_invalid_state_is_refused_by_the_database(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO node_health(node_id,indicator,state,updated_at) "
                              "VALUES('a1b2','capacity','purple','x')")


class TestMigration(unittest.TestCase):
    """The one thing CREATE TABLE IF NOT EXISTS cannot do."""

    def test_a_narrow_check_from_an_older_version_is_widened(self):
        import sqlite3
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, store.DB_FILENAME)
        old = sqlite3.connect(path)
        old.executescript(
            "CREATE TABLE node_health(node_id TEXT NOT NULL, indicator TEXT NOT NULL,"
            " state TEXT NOT NULL CHECK(state IN ('ok','watch','warn','unknown')),"
            " value REAL, basis TEXT, pending_state TEXT,"
            " pending_count INTEGER NOT NULL DEFAULT 0, since TEXT,"
            " updated_at TEXT NOT NULL, PRIMARY KEY(node_id, indicator));"
            "PRAGMA user_version=2;")
        old.commit()
        old.close()

        conn = store.connect(directory)
        store.sync_registry(conn, [NODE])
        # Would raise IntegrityError against the old constraint.
        store.upsert_health(conn, 'a1b2', 'overall', 'degraded', basis='capacity')
        self.assertEqual('degraded', store.read_health(conn, 'a1b2')['overall']['state'])
        self.assertEqual(store.SCHEMA_VERSION,
                         conn.execute('PRAGMA user_version').fetchone()[0])
        conn.close()

    def test_history_is_never_dropped_by_a_migration(self):
        # node_state, samples and events are not derived. Losing them to an
        # upgrade would throw away everything the fleet has ever recorded.
        directory = tempfile.mkdtemp()
        conn = store.connect(directory)
        store.sync_registry(conn, [NODE])
        store.add_samples(conn, 'a1b2', [('cpu.percent', 4.0)], ts='2026-08-27T00:00:00Z')
        store.log_event(conn, 'daemon', 'started')
        conn.execute('PRAGMA user_version=2')
        conn.commit()
        conn.close()

        conn = store.connect(directory)
        self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM samples').fetchone()[0])
        self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM events').fetchone()[0])
        self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0])
        conn.close()


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

    def test_an_invalid_state_is_refused_before_sql(self):
        # The database CHECK is the backstop; this is the guard that gives a
        # caller a legible error instead of an IntegrityError. upsert_state has
        # the same test - without it, deleting the raise leaves the suite green.
        with self.assertRaises(ValueError):
            store.upsert_health(self.conn, 'a1b2', 'capacity', 'purple')


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
        # Inserted newest-first on purpose. Asserting the exact list rather than
        # comparing the result to sorted(itself), which passes on an empty
        # result and survives deleting the ORDER BY.
        for ts, value in (('2026-08-27T00:20:00Z', 5), ('2026-08-27T00:00:00Z', 1)):
            store.add_samples(self.conn, 'a1b2', [('array.errors_total', value)], ts=ts)
        rows = store.recent_samples(self.conn, 'a1b2', 'array.errors_total', '2026-01-01T00:00:00Z')
        self.assertEqual([('2026-08-27T00:00:00Z', 1.0), ('2026-08-27T00:20:00Z', 5.0)], rows)

    def test_another_metric_is_not_included(self):
        store.add_samples(self.conn, 'a1b2', [('cpu.percent', 9)], ts='2026-08-27T00:00:00Z')
        self.assertEqual([], store.recent_samples(self.conn, 'a1b2', 'array.errors_total',
                                                  '2026-01-01T00:00:00Z'))


if __name__ == '__main__':
    unittest.main()
