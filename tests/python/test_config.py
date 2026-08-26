import os
import tempfile
import unittest

import context  # noqa: F401
import config


class TestManagerCfg(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path

    def test_reads_values(self):
        p = self.write('manager.cfg', 'db_path=/mnt/user/appdata/unraid-manager\npoll_fast=15\npoll_slow=900\n')
        cfg = config.read_manager_cfg(p)
        self.assertEqual('/mnt/user/appdata/unraid-manager', cfg['db_path'])
        self.assertEqual(15, cfg['poll_fast'])
        self.assertEqual(900, cfg['poll_slow'])

    def test_missing_file_is_defaults(self):
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'))
        self.assertEqual({'db_path': '', 'poll_fast': 30, 'poll_slow': 600}, cfg)

    def test_junk_value_falls_back_to_default(self):
        p = self.write('manager.cfg', 'db_path=/mnt/x\npoll_fast=banana\n')
        self.assertEqual(30, config.read_manager_cfg(p)['poll_fast'])

    def test_quotes_and_whitespace_are_stripped(self):
        p = self.write('manager.cfg', '  db_path = "/mnt/user/x"  \n')
        self.assertEqual('/mnt/user/x', config.read_manager_cfg(p)['db_path'])

    def test_comments_ignored(self):
        p = self.write('manager.cfg', '# a comment\npoll_fast=45\n')
        self.assertEqual(45, config.read_manager_cfg(p)['poll_fast'])


class TestNodesCfg(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, text):
        path = os.path.join(self.dir, 'nodes.cfg')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path

    def test_reads_two_nodes_sorted_by_name(self):
        p = self.write(
            '[b2c3]\nname=Raven\naddress=192.168.2.19\nport=29220\ntier=0\nenabled=1\n'
            '\n[a1b2]\nname=Golem\naddress=192.168.2.248\nport=15137\ntier=0\nenabled=1\n')
        nodes = config.read_nodes_cfg(p)
        self.assertEqual(['Golem', 'Raven'], [n['name'] for n in nodes])
        self.assertEqual('a1b2', nodes[0]['id'])
        self.assertEqual(15137, nodes[0]['port'])
        self.assertEqual(0, nodes[0]['tier'])
        self.assertTrue(nodes[0]['enabled'])

    def test_missing_file_is_empty_registry(self):
        self.assertEqual([], config.read_nodes_cfg(os.path.join(self.dir, 'nope.cfg')))

    def test_enabled_zero_is_false(self):
        p = self.write('[x]\nname=X\naddress=1.2.3.4\nport=1\nenabled=0\n')
        self.assertFalse(config.read_nodes_cfg(p)[0]['enabled'])

    def test_section_missing_address_is_skipped_not_fatal(self):
        # A hand-edited flash file must not take the daemon down.
        p = self.write('[broken]\nname=Broken\n\n[ok]\nname=OK\naddress=1.2.3.4\nport=80\n')
        nodes = config.read_nodes_cfg(p)
        self.assertEqual(['OK'], [n['name'] for n in nodes])

    def test_bad_port_is_skipped(self):
        p = self.write('[x]\nname=X\naddress=1.2.3.4\nport=notanumber\n')
        self.assertEqual([], config.read_nodes_cfg(p))

    def test_name_defaults_to_address_when_absent(self):
        p = self.write('[x]\naddress=1.2.3.4\nport=80\n')
        self.assertEqual('1.2.3.4', config.read_nodes_cfg(p)[0]['name'])


class TestReadKey(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_reads_and_strips(self):
        with open(os.path.join(self.dir, 'a1b2.key'), 'w', encoding='utf-8') as fh:
            fh.write('  secret-value\n')
        self.assertEqual('secret-value', config.read_key(self.dir, 'a1b2'))

    def test_missing_key_is_none(self):
        self.assertIsNone(config.read_key(self.dir, 'nosuch'))

    def test_node_id_cannot_traverse(self):
        self.assertIsNone(config.read_key(self.dir, '../../etc/passwd'))


if __name__ == '__main__':
    unittest.main()
