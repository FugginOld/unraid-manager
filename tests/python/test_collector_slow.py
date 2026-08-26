import unittest

import context
import collector


class TestSlowParsers(unittest.TestCase):
    def test_disks_splits_installed_from_spares(self):
        out = collector.parse_disks(context.fixture_json('seed/disks.json')['data'])
        self.assertEqual(1, out['count'])
        self.assertEqual(1, out['spare_count'])
        self.assertEqual('sdc', out['disks'][0]['name'])
        self.assertEqual('sdz', out['spares'][0]['name'])

    def test_smart_status_is_carried_verbatim(self):
        # Tier 0 gives OK|UNKNOWN only. Do not translate UNKNOWN into a verdict:
        # the disk verdict chain is Tier 1 (module map, M4).
        out = collector.parse_disks(context.fixture_json('seed/disks.json')['data'])
        self.assertEqual('OK', out['disks'][0]['smart_status'])

    def test_serial_is_dropped_from_the_payload(self):
        # Invariant from plan section 12: no raw serial in any API-bound payload.
        out = collector.parse_disks(context.fixture_json('seed/disks.json')['data'])
        self.assertNotIn('serialNum', out['disks'][0])
        self.assertNotIn('serial', out['disks'][0])

    def test_plugins_are_names_only(self):
        # M10 trap: installedUnraidPlugins gives no versions at Tier 0.
        out = collector.parse_plugins(context.fixture_json('seed/plugins.json')['data'])
        self.assertEqual(3, out['count'])
        self.assertIn('ca.backup2.plg', out['plugins'])

    def test_logfiles_are_names_and_sizes_only(self):
        out = collector.parse_logfiles(context.fixture_json('seed/logfiles.json')['data'])
        self.assertEqual(2, out['count'])
        self.assertEqual({'name', 'path', 'size', 'modified_at'}, set(out['files'][0]))

    def test_slow_domains_are_registered_with_the_90s_timeout(self):
        for name in ('disks', 'plugins', 'logfiles'):
            self.assertEqual(collector.SLOW, collector.DOMAINS[name].lane, name)
            self.assertEqual(90, collector.DOMAINS[name].timeout, name)


if __name__ == '__main__':
    unittest.main()
