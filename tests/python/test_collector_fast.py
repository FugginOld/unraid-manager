import unittest

import context
import collector
import gqlclient


class TestDomainTable(unittest.TestCase):
    def test_fast_lane_membership(self):
        self.assertEqual(['info', 'array', 'shares', 'notifications', 'metrics', 'parity'],
                         [d.name for d in collector.domains_for_lane(collector.FAST)])

    def test_slow_lane_membership(self):
        self.assertEqual(['disks', 'plugins', 'logfiles'],
                         [d.name for d in collector.domains_for_lane(collector.SLOW)])

    def test_disks_is_never_in_the_fast_lane(self):
        # Constraint 2: 15.4s on Golem, a reproducible 504 on Raven.
        self.assertEqual(collector.SLOW, collector.DOMAINS['disks'].lane)

    def test_lane_timeouts(self):
        self.assertEqual(10, collector.DOMAINS['info'].timeout)
        self.assertEqual(90, collector.DOMAINS['disks'].timeout)

    def test_every_domain_is_its_own_query(self):
        # Constraint 1: one failing resolver nulls a batched response.
        queries = [d.query for d in collector.DOMAINS.values()]
        self.assertEqual(len(queries), len(set(queries)))

    def test_no_mutation_anywhere_in_the_domain_table(self):
        # P0 is strictly read-only against peers (spec, decisions).
        for domain in collector.DOMAINS.values():
            self.assertNotIn('mutation', domain.query.lower(), domain.name)

    def test_no_introspection_query(self):
        # Constraint 4: introspection is off in production.
        for domain in collector.DOMAINS.values():
            self.assertNotIn('__schema', domain.query, domain.name)
            self.assertNotIn('__type', domain.query, domain.name)


class TestParseInfo(unittest.TestCase):
    def test_headline_fields(self):
        out = collector.parse_info(context.fixture_json('seed/info.json')['data'])
        self.assertEqual('Golem', out['hostname'])
        self.assertEqual('7.3.2', out['unraid'])
        self.assertEqual('4.37.3+d5058009', out['api'])
        self.assertEqual('6.18.38-Unraid', out['kernel'])

    def test_uptime_is_kept_as_the_boot_timestamp_it_is(self):
        # Finding 5: InfoOs.uptime is an ISO8601 boot time, not a duration.
        out = collector.parse_info(context.fixture_json('seed/info.json')['data'])
        self.assertEqual('2026-08-11T04:12:07.000Z', out['booted_at'])
        self.assertNotIn('uptime', out)

    def test_missing_versions_block_does_not_raise(self):
        out = collector.parse_info({'info': {'os': {'hostname': 'X'}}})
        self.assertEqual('X', out['hostname'])
        self.assertIsNone(out['unraid'])


class TestParseArray(unittest.TestCase):
    def populated(self):
        return collector.parse_array(context.fixture_json('seed/array_populated.json')['data'])

    def empty(self):
        return collector.parse_array(context.fixture_json('seed/array_empty.json')['data'])

    def test_state_and_counts(self):
        out = self.populated()
        self.assertEqual('STARTED', out['state'])
        self.assertEqual(22, out['disk_count'])
        self.assertEqual(1, out['parity_count'])

    def test_capacity_is_numeric_bytes_not_kilobyte_strings(self):
        out = self.populated()
        self.assertEqual(267592186044 * 1024, out['capacity']['total'])
        self.assertEqual(250000000000 * 1024, out['capacity']['used'])

    def test_empty_array_is_flagged_and_is_not_an_error(self):
        # Constraint 3: Raven's real state. Zero capacity + zero used slots.
        out = self.empty()
        self.assertTrue(out['empty'])
        self.assertEqual('STARTED', out['state'])
        self.assertEqual(0, out['capacity']['total'])

    def test_populated_array_is_not_flagged_empty(self):
        self.assertFalse(self.populated()['empty'])

    def test_multi_device_pool_siblings_are_deduped(self):
        # Finding 6: cache_movies2 / medianucbackup2 carry null fsType.
        pools = self.populated()['pools']
        self.assertEqual(['cache_movies', 'medianucbackup', 'cache_tv'],
                         [p['name'] for p in pools])
        self.assertEqual(2, pools[0]['members'])
        self.assertEqual('btrfs', pools[0]['fs_type'])
        self.assertEqual(4, pools[2]['members'])

    def test_pool_capacity_comes_from_the_primary_member_only(self):
        pools = self.populated()['pools']
        self.assertEqual(1953514584 * 1024, pools[0]['size'])

    def test_disk_error_total_and_hottest_disk(self):
        out = self.populated()
        self.assertEqual(0, out['errors_total'])
        self.assertEqual(40, out['temp_max'])

    def test_empty_array_has_no_temperature_rather_than_zero(self):
        self.assertIsNone(self.empty()['temp_max'])

    def test_parity_check_status_passes_through(self):
        # It is an OBJECT, not a leaf (correction notice, item 1), and the whole
        # object is what passes through: parse_parity deliberately returns
        # running/paused as None and defers to this field for live state.
        pcs = self.populated()['parity_check_status']
        self.assertEqual('COMPLETED', pcs['status'])
        self.assertIsNone(pcs['running'])

    def test_a_disk_missing_temp_does_not_break_the_max(self):
        out = collector.parse_array({'array': {
            'state': 'STARTED',
            'capacity': {'kilobytes': {'free': '1', 'used': '1', 'total': '2'},
                         'disks': {'free': '1', 'used': '1', 'total': '2'}},
            'parities': [], 'caches': [],
            'disks': [{'idx': 1, 'name': 'disk1', 'temp': None, 'numErrors': 0},
                      {'idx': 2, 'name': 'disk2', 'temp': 31, 'numErrors': 2}]}})
        self.assertEqual(31, out['temp_max'])
        self.assertEqual(2, out['errors_total'])


class TestOtherFastParsers(unittest.TestCase):
    def test_shares(self):
        out = collector.parse_shares(context.fixture_json('seed/shares.json')['data'])
        self.assertEqual(39, out['count'])
        self.assertEqual(['appdata', 'media'], [s['name'] for s in out['shares'][:2]])

    def test_notifications_counts(self):
        out = collector.parse_notifications(context.fixture_json('seed/notifications.json')['data'])
        self.assertEqual({'info': 3, 'warning': 1, 'alert': 0, 'total': 4}, out['unread'])

    def test_notifications_missing_overview_is_zeros_not_a_crash(self):
        out = collector.parse_notifications({'notifications': {}})
        self.assertEqual({'info': 0, 'warning': 0, 'alert': 0, 'total': 0}, out['unread'])

    def test_metrics(self):
        out = collector.parse_metrics(context.fixture_json('seed/metrics.json')['data'])
        self.assertEqual(12.4, out['cpu_percent'])
        self.assertEqual(34.9, out['mem_percent'])

    def test_parity_history_latest_first(self):
        out = collector.parse_parity(context.fixture_json('seed/parity.json')['data'])
        self.assertEqual('COMPLETED', out['last']['status'])
        self.assertEqual(0, out['last']['errors'])
        self.assertFalse(out['running'])

    def test_parity_never_run_has_no_last(self):
        out = collector.parse_parity({'parityHistory': []})
        self.assertIsNone(out['last'])


class TestCollect(unittest.TestCase):
    NODE = {'id': 'a1b2', 'address': '192.168.2.248', 'port': 15137, 'key': 'k' * 44}

    def test_ok_result_carries_payload_and_samples(self):
        data = context.fixture_json('seed/metrics.json')['data']
        result = collector.collect(lambda *a, **k: data, self.NODE, collector.DOMAINS['metrics'])
        self.assertEqual('ok', result.status)
        self.assertEqual(12.4, result.payload['cpu_percent'])
        self.assertIn(('cpu.percent', 12.4), result.samples)

    def test_array_ok_emits_capacity_samples(self):
        data = context.fixture_json('seed/array_populated.json')['data']
        result = collector.collect(lambda *a, **k: data, self.NODE, collector.DOMAINS['array'])
        metrics = dict(result.samples)
        self.assertIn('array.bytes_used', metrics)
        self.assertIn('array.bytes_total', metrics)

    def test_empty_array_collects_ok(self):
        data = context.fixture_json('seed/array_empty.json')['data']
        result = collector.collect(lambda *a, **k: data, self.NODE, collector.DOMAINS['array'])
        self.assertEqual('ok', result.status)

    def test_transport_failure_is_unknown_not_error(self):
        # Constraint 5, fail closed: we could not read it, so we do not know.
        def boom(*a, **k):
            raise gqlclient.TransportError('connection refused')
        result = collector.collect(boom, self.NODE, collector.DOMAINS['info'])
        self.assertEqual('unknown', result.status)
        self.assertIn('connection refused', result.error)
        self.assertIsNone(result.payload)

    def test_resolver_failure_is_error(self):
        def boom(*a, **k):
            raise gqlclient.DomainError('Cannot read properties of undefined')
        result = collector.collect(boom, self.NODE, collector.DOMAINS['info'])
        self.assertEqual('error', result.status)

    def test_auth_failure_is_unknown_and_says_key(self):
        def boom(*a, **k):
            raise gqlclient.AuthError('API key rejected (HTTP 401): Unauthorized')
        result = collector.collect(boom, self.NODE, collector.DOMAINS['info'])
        self.assertEqual('unknown', result.status)
        self.assertIn('key', result.error.lower())

    def test_a_parser_crash_is_an_error_not_a_daemon_crash(self):
        result = collector.collect(lambda *a, **k: {'info': 'not a dict'},
                                   self.NODE, collector.DOMAINS['info'])
        self.assertEqual('error', result.status)
        self.assertTrue(result.error)

    def test_the_key_never_appears_in_a_result(self):
        key = self.NODE['key']

        def boom(*a, **k):
            raise gqlclient.TransportError('failed talking to %s' % key)
        result = collector.collect(boom, self.NODE, collector.DOMAINS['info'])
        self.assertNotIn(key, result.error)
        self.assertIn('<redacted>', result.error)


if __name__ == '__main__':
    unittest.main()
