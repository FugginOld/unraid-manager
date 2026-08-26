import json
import unittest

import context
import collector
import gqlclient

KEY = 'probe-key-0123456789012345678901234567890123'

FAST_NAMES = ('info', 'array', 'shares', 'notifications', 'metrics', 'parity')


def responder(mapping, fail=None):
    """post_fn that answers per-domain from `mapping`, raising from `fail`."""
    by_query = {collector.DOMAINS[name].query: data for name, data in mapping.items()}
    raisers = {collector.DOMAINS[name].query: exc for name, exc in (fail or {}).items()}

    def post_fn(address, port, key, query, timeout):
        if query in raisers:
            raise raisers[query]
        if query in by_query:
            return by_query[query]
        raise gqlclient.DomainError('unexpected query')
    return post_fn


def all_good():
    return {
        'info': context.fixture_json('seed/info.json')['data'],
        'array': context.fixture_json('seed/array_populated.json')['data'],
        'shares': context.fixture_json('seed/shares.json')['data'],
        'notifications': context.fixture_json('seed/notifications.json')['data'],
        'metrics': context.fixture_json('seed/metrics.json')['data'],
        'parity': context.fixture_json('seed/parity.json')['data'],
    }


class TestProbe(unittest.TestCase):
    def test_all_domains_ok_is_verdict_ok_with_headlines(self):
        report = collector.probe(responder(all_good()), '192.168.2.248', 15137, KEY)
        self.assertEqual('ok', report['verdict'])
        self.assertEqual('Golem', report['headline']['hostname'])
        self.assertEqual('7.3.2', report['headline']['unraid'])
        self.assertEqual('4.37.3+d5058009', report['headline']['api'])
        self.assertEqual('STARTED', report['headline']['array_state'])
        self.assertEqual(39, report['headline']['shares'])

    def test_probe_only_runs_the_fast_lane(self):
        report = collector.probe(responder(all_good()), 'h', 1, KEY)
        self.assertEqual(list(FAST_NAMES), list(report['domains']))
        self.assertNotIn('disks', report['domains'])

    def test_one_failing_domain_is_partial_not_a_refusal(self):
        report = collector.probe(
            responder(all_good(), fail={'metrics': gqlclient.DomainError('resolver blew up')}),
            'h', 1, KEY)
        self.assertEqual('partial', report['verdict'])
        self.assertEqual('error', report['domains']['metrics']['status'])
        self.assertEqual('ok', report['domains']['info']['status'])

    def test_every_domain_unreachable_is_unreachable(self):
        fail = {n: gqlclient.TransportError('connection refused') for n in FAST_NAMES}
        report = collector.probe(responder({}, fail=fail), 'h', 1, KEY)
        self.assertEqual('unreachable', report['verdict'])

    def test_a_rejected_key_is_bad_key_not_unreachable(self):
        fail = {n: gqlclient.AuthError('API key rejected (HTTP 401): Unauthorized')
                for n in FAST_NAMES}
        report = collector.probe(responder({}, fail=fail), 'h', 1, KEY)
        self.assertEqual('bad_key', report['verdict'])

    def test_a_key_short_of_scope_on_some_domains_is_partial(self):
        report = collector.probe(
            responder(all_good(), fail={'notifications': gqlclient.AuthError('Access denied')}),
            'h', 1, KEY)
        self.assertEqual('partial', report['verdict'])

    def test_empty_array_probes_ok(self):
        mapping = all_good()
        mapping['array'] = context.fixture_json('seed/array_empty.json')['data']
        report = collector.probe(responder(mapping), 'h', 1, KEY)
        self.assertEqual('ok', report['verdict'])
        self.assertEqual('STARTED', report['headline']['array_state'])
        self.assertTrue(report['headline']['array_empty'])

    def test_the_key_appears_nowhere_in_the_report(self):
        fail = {'info': gqlclient.TransportError('failed with key %s' % KEY)}
        report = collector.probe(responder(all_good(), fail=fail), 'h', 1, KEY)
        self.assertNotIn(KEY, json.dumps(report))


if __name__ == '__main__':
    unittest.main()
