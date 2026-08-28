import os
import re
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
        # dynamix_path is passed explicitly and points at nothing: without it
        # this reads the REAL /boot/config/plugins/dynamix/dynamix.cfg when the
        # suite runs on an Unraid box, and the expectation below would depend
        # on that operator's Disk Settings.
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'),
                                      dynamix_path=os.path.join(self.dir, 'no-dynamix.cfg'))
        self.assertEqual({'db_path': '', 'poll_fast': 30, 'poll_slow': 600,
                          'capacity_high_water': 90, 'capacity_watch': 80,
                          'temp_warn': 50, 'temp_crit': 60,
                          'error_window_min': 15}, cfg)

    def _dynamix(self, text):
        path = os.path.join(self.dir, 'dynamix.cfg')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path

    def test_unraid_disk_settings_become_the_defaults(self):
        # P1 exit finding F-8. The operator filled in Settings -> Disk
        # Settings; shipping unrelated constants gave one box two answers to
        # "how hot is too hot" - Raven's Unraid said 45/55 while this daemon
        # said 50/60, so a disk at 47 C was warm to one and fine to the other.
        dyn = self._dynamix('[display]\nhot="45"\nmax="55"\n'
                            'warning="70"\ncritical="90"\nhotssd="60"\n')
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'), dynamix_path=dyn)
        self.assertEqual(45, cfg['temp_warn'])
        self.assertEqual(55, cfg['temp_crit'])
        self.assertEqual(70, cfg['capacity_watch'])
        self.assertEqual(90, cfg['capacity_high_water'])

    def test_the_ssd_thresholds_are_not_borrowed_for_every_disk(self):
        # hotssd/maxssd are 60/70 on Raven. Applying an SSD limit to a spinner
        # needs a rotational flag the physical enumeration does not carry.
        dyn = self._dynamix('[display]\nhotssd="60"\nmaxssd="70"\n')
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'), dynamix_path=dyn)
        self.assertEqual(50, cfg['temp_warn'])
        self.assertEqual(60, cfg['temp_crit'])

    def test_our_own_setting_still_overrides_unraids(self):
        # The fleet override, for a manager whose own box is not representative.
        dyn = self._dynamix('[display]\nhot="45"\nmax="55"\n')
        path = os.path.join(self.dir, 'manager.cfg')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('temp_warn=52\ntemp_crit=62\n')
        cfg = config.read_manager_cfg(path, dynamix_path=dyn)
        self.assertEqual(52, cfg['temp_warn'])

    def test_a_blank_setting_means_follow_unraid(self):
        # The settings page writes every key on every save, so blank is the
        # only way to say "use Unraid's" after once typing a number.
        dyn = self._dynamix('[display]\nhot="45"\nmax="55"\n')
        path = os.path.join(self.dir, 'manager.cfg')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('temp_warn=\ntemp_crit=\n')
        cfg = config.read_manager_cfg(path, dynamix_path=dyn)
        self.assertEqual(45, cfg['temp_warn'])
        self.assertEqual(55, cfg['temp_crit'])

    def test_an_inverted_pair_from_unraid_is_refused_too(self):
        # The guard falls back to OUR constants, never to `defaults` - which
        # already carries Unraid's values, so falling back there would "refuse"
        # an inversion by restoring the very pair that was inverted.
        dyn = self._dynamix('[display]\nhot="55"\nmax="45"\n'
                            'warning="95"\ncritical="90"\n')
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'), dynamix_path=dyn)
        self.assertEqual(50, cfg['temp_warn'])
        self.assertEqual(60, cfg['temp_crit'])
        self.assertEqual(80, cfg['capacity_watch'])
        self.assertEqual(90, cfg['capacity_high_water'])

    def test_an_inverted_capacity_pair_from_our_own_cfg_is_refused(self):
        # The temperature pair had this test; the capacity pair had none, and
        # the guard could be deleted outright with the suite green.
        path = os.path.join(self.dir, 'manager.cfg')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('capacity_watch=95\ncapacity_high_water=90\n')
        cfg = config.read_manager_cfg(path,
                                      dynamix_path=os.path.join(self.dir, 'none.cfg'))
        self.assertEqual(80, cfg['capacity_watch'])
        self.assertEqual(90, cfg['capacity_high_water'])

    def test_a_fahrenheit_box_inherits_no_temperature_at_all(self):
        # We cannot tell whether Unraid stores hot/max in F on such a box or
        # always in C with `unit` governing display only, and no test here can
        # find out - it is a fact about dynamix, not about this code.
        # Converting a Celsius 45 would give 7 C and warn on every disk
        # forever; reading a Fahrenheit 95 as Celsius would give a threshold
        # that can never fire. Declining is the only choice safe under both.
        dyn = self._dynamix('[display]\nunit="F"\nhot="45"\nmax="55"\n'
                            'warning="70"\ncritical="90"\n')
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'), dynamix_path=dyn)
        self.assertEqual(50, cfg['temp_warn'], 'our own default, not 45 and not 7')
        self.assertEqual(60, cfg['temp_crit'])
        # Capacity is a percentage: unaffected by the display unit.
        self.assertEqual(70, cfg['capacity_watch'])
        self.assertEqual(90, cfg['capacity_high_water'])

    def test_only_the_display_section_is_read(self):
        # common.php reads ['display'] only; without the same filter here a key
        # of the same name in another section would diverge the two readers by
        # file order, on a file neither of them owns.
        dyn = self._dynamix('[display]\nhot="45"\n[somethingelse]\nhot="99"\n')
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'), dynamix_path=dyn)
        self.assertEqual(45, cfg['temp_warn'])

    def test_an_out_of_range_unraid_value_is_ignored(self):
        # Another plugin's file. A nonsense value must leave our own default in
        # place rather than produce a threshold that can never fire.
        dyn = self._dynamix('[display]\nhot="4"\nmax="500"\n')
        cfg = config.read_manager_cfg(os.path.join(self.dir, 'nope.cfg'), dynamix_path=dyn)
        self.assertEqual(50, cfg['temp_warn'])
        self.assertEqual(60, cfg['temp_crit'])

    def test_junk_value_falls_back_to_default(self):
        p = self.write('manager.cfg', 'db_path=/mnt/x\npoll_fast=banana\n')
        self.assertEqual(30, config.read_manager_cfg(p)['poll_fast'])

    def test_quotes_and_whitespace_are_stripped(self):
        p = self.write('manager.cfg', '  db_path = "/mnt/user/x"  \n')
        self.assertEqual('/mnt/user/x', config.read_manager_cfg(p)['db_path'])

    def test_comments_ignored(self):
        p = self.write('manager.cfg', '# a comment\npoll_fast=45\n')
        self.assertEqual(45, config.read_manager_cfg(p)['poll_fast'])


class TestUnraidKeyMirror(unittest.TestCase):
    """UNRAID_THRESHOLD_KEYS <-> UM_UNRAID_THRESHOLD_KEYS.

    The comments on both call them deliberate mirrors, and the older mirror
    (THRESHOLD_BOUNDS <-> UM_THRESHOLDS) has had a test since Task 4. This one
    had none, and it is the map that decides which of another plugin's settings
    this daemon obeys - a silent divergence would have the page reporting one
    inherited value while the daemon used another.
    """

    def test_both_halves_map_the_same_keys(self):
        php = os.path.join(os.path.dirname(__file__), '..', '..', 'source', 'usr',
                           'local', 'emhttp', 'plugins', 'unraid-manager',
                           'include', 'common.php')
        with open(php, encoding='utf-8') as fh:
            text = fh.read()
        block = re.search(r'UM_UNRAID_THRESHOLD_KEYS\s*=\s*\[(.*?)\];', text, re.S)
        self.assertIsNotNone(block, 'the PHP mirror is gone or was renamed')
        pairs = dict(re.findall(r"'(\w+)'\s*=>\s*'(\w+)'", block.group(1)))
        self.assertEqual(len(config.UNRAID_THRESHOLD_KEYS), len(pairs),
                         'parsed %d php rows, expected %d'
                         % (len(pairs), len(config.UNRAID_THRESHOLD_KEYS)))
        self.assertEqual(config.UNRAID_THRESHOLD_KEYS, pairs)

    def test_every_inherited_key_has_a_bound(self):
        # Adding a fifth entry to both key maps without adding it to
        # THRESHOLD_BOUNDS/UM_THRESHOLDS leaves both mirrors green, and the two
        # halves then diverge on that key from silent-accept to KeyError.
        self.assertLessEqual(set(config.UNRAID_THRESHOLD_KEYS.values()),
                             set(config.THRESHOLD_BOUNDS))


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


class TestThresholds(unittest.TestCase):
    def write(self, text):
        path = os.path.join(tempfile.mkdtemp(), 'manager.cfg')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path

    def test_defaults_when_absent(self):
        cfg = config.read_manager_cfg(self.write('db_path=/mnt/user/x\n'))
        self.assertEqual(90, cfg['capacity_high_water'])
        self.assertEqual(50, cfg['temp_warn'])
        self.assertEqual(60, cfg['temp_crit'])
        self.assertEqual(15, cfg['error_window_min'])

    def test_values_are_read(self):
        cfg = config.read_manager_cfg(self.write(
            'db_path=/mnt/user/x\ncapacity_high_water=85\ntemp_warn=45\n'
            'temp_crit=55\nerror_window_min=30\n'))
        self.assertEqual(85, cfg['capacity_high_water'])
        self.assertEqual(45, cfg['temp_warn'])
        self.assertEqual(55, cfg['temp_crit'])
        self.assertEqual(30, cfg['error_window_min'])

    def test_nonsense_falls_back_rather_than_raising(self):
        cfg = config.read_manager_cfg(self.write('db_path=/x\ncapacity_high_water=soon\n'))
        self.assertEqual(90, cfg['capacity_high_water'])

    def test_an_out_of_range_percentage_falls_back(self):
        cfg = config.read_manager_cfg(self.write('db_path=/x\ncapacity_high_water=900\n'))
        self.assertEqual(90, cfg['capacity_high_water'])

    def test_crit_below_warn_falls_back_to_both_defaults(self):
        # An inverted pair would make thermal unreachable; refuse the pair, not
        # just the second value.
        cfg = config.read_manager_cfg(self.write('db_path=/x\ntemp_warn=70\ntemp_crit=40\n'))
        self.assertEqual(50, cfg['temp_warn'])
        self.assertEqual(60, cfg['temp_crit'])

    def test_every_bound_is_enforced_not_just_the_first(self):
        # Deleting the bounds loop must fail more than one assertion, or three
        # of the four keys are unprotected.
        cfg = config.read_manager_cfg(self.write(
            'db_path=/x\ntemp_warn=1\ntemp_crit=500\nerror_window_min=99999\n'))
        self.assertEqual(50, cfg['temp_warn'])
        self.assertEqual(60, cfg['temp_crit'])
        self.assertEqual(15, cfg['error_window_min'])

    def test_the_defaults_match_the_health_engine(self):
        # Duplicated deliberately - config must not import health - so assert
        # they agree rather than trusting they do.
        import health
        for key, value in health.DEFAULT_THRESHOLDS.items():
            self.assertEqual(value, config.MANAGER_DEFAULTS[key], key)

class TestPhpBoundsMirror(unittest.TestCase):
    """The settings form validates against its own copy of the bounds.

    PHP's UM_THRESHOLDS and this module's THRESHOLD_BOUNDS/MANAGER_DEFAULTS are
    the same contract written twice, and each side's suite pins only its own
    numbers - so editing one table and its own tests leaves BOTH suites green
    while the form accepts a value the daemon then silently discards to the
    default. This is the only test in the repo that crosses the language
    boundary, and it exists because nothing else can see that divergence.
    """

    PHP = os.path.join(os.path.dirname(__file__), '..', '..', 'source', 'usr',
                       'local', 'emhttp', 'plugins', 'unraid-manager', 'api',
                       'settings.php')

    def php_thresholds(self):
        import re
        with open(self.PHP, encoding='utf-8') as fh:
            src = fh.read()
        block = re.search(r'const\s+UM_THRESHOLDS\s*=\s*\[(.*?)\];', src, re.S)
        self.assertIsNotNone(block, 'UM_THRESHOLDS not found in settings.php')
        rows = re.findall(r"'(\w+)'\s*=>\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]",
                          block.group(1))
        # A parse that silently finds nothing would make every assertion below
        # vacuous, which is the failure mode this whole test exists to prevent.
        self.assertEqual(len(rows), len(config.THRESHOLD_BOUNDS),
                         'parsed %d threshold rows, expected %d'
                         % (len(rows), len(config.THRESHOLD_BOUNDS)))
        return {k: (int(lo), int(hi), int(default)) for k, lo, hi, default in rows}

    def test_php_bounds_match_python(self):
        php = self.php_thresholds()
        self.assertEqual(set(php), set(config.THRESHOLD_BOUNDS))
        for key, (lo, hi, default) in php.items():
            self.assertEqual((lo, hi), config.THRESHOLD_BOUNDS[key],
                             '%s bounds differ between settings.php and config.py' % key)
            self.assertEqual(default, config.MANAGER_DEFAULTS[key],
                             '%s default differs between settings.php and config.py' % key)


if __name__ == '__main__':
    unittest.main()
