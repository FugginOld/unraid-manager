import os
import sqlite3
import tempfile
import unittest

import context  # noqa: F401
import store


class TestValidateDbPath(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_accepts_a_pool_path(self):
        self.assertEqual(self.dir, store.validate_db_path(self.dir))

    def test_empty_is_rejected(self):
        with self.assertRaises(ValueError):
            store.validate_db_path('')
        with self.assertRaises(ValueError):
            store.validate_db_path('   ')

    def test_boot_is_rejected(self):
        for bad in ('/boot', '/boot/', '/boot/config/plugins/unraid-manager',
                    '/boot//config/x', '/mnt/../boot/config'):
            with self.assertRaises(store.FlashPathError, msg=bad):
                store.validate_db_path(bad)

    def test_flash_check_runs_before_the_parent_exists_check(self):
        # /boot does not exist on this dev machine. The refusal must still be
        # the flash refusal, not "parent missing" — the message the operator
        # sees has to name the real reason.
        with self.assertRaises(store.FlashPathError):
            store.validate_db_path('/boot/config/plugins/unraid-manager')

    def test_a_path_merely_containing_boot_is_fine(self):
        ok = os.path.join(self.dir, 'bootstrap')
        os.makedirs(ok)
        self.assertEqual(ok, store.validate_db_path(ok))

    def test_missing_parent_is_rejected(self):
        with self.assertRaises(ValueError):
            store.validate_db_path(os.path.join(self.dir, 'no', 'such', 'tree'))


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.conn = store.connect(self.dir)

    def tearDown(self):
        self.conn.close()

    def test_db_file_created_with_expected_name(self):
        self.assertTrue(os.path.isfile(os.path.join(self.dir, 'manager.db')))

    def test_user_version_is_the_schema_version(self):
        self.assertEqual(store.SCHEMA_VERSION,
                         self.conn.execute('PRAGMA user_version').fetchone()[0])

    def test_all_four_tables_exist(self):
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'nodes', 'node_state', 'samples', 'events'} <= names)

    def test_samples_index_exists(self):
        idx = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn('samples_by_series', idx)

    def test_status_check_constraint_rejects_a_fourth_value(self):
        self.conn.execute("INSERT INTO nodes(id,name,address,port,added_at) "
                          "VALUES('n','N','1.2.3.4',80,?)", (store.utcnow(),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO node_state(node_id,domain,status) "
                              "VALUES('n','info','maybe')")

    def test_bootstrap_is_idempotent(self):
        second = store.connect(self.dir)
        self.assertEqual(store.SCHEMA_VERSION,
                         second.execute('PRAGMA user_version').fetchone()[0])
        second.close()

    def test_journal_mode_is_wal(self):
        mode = self.conn.execute('PRAGMA journal_mode').fetchone()[0]
        self.assertEqual('wal', str(mode).lower())

    def test_rows_are_addressable_by_column_name(self):
        self.conn.execute("INSERT INTO nodes(id,name,address,port,added_at) "
                          "VALUES('n','N','1.2.3.4',80,?)", (store.utcnow(),))
        row = self.conn.execute("SELECT * FROM nodes").fetchone()
        self.assertEqual('N', row['name'])


class TestUtcNow(unittest.TestCase):
    def test_shape(self):
        ts = store.utcnow()
        self.assertRegex(ts, r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')


if __name__ == '__main__':
    unittest.main()
