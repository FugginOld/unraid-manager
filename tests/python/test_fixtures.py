import json
import os
import unittest

import context

SEED = os.path.join(context.FIXTURES, 'seed')

# Every domain the collector will parse must have a seed fixture, so no later
# task is blocked on a live box being reachable.
REQUIRED = [
    'info.json', 'array_populated.json', 'array_empty.json', 'shares.json',
    'notifications.json', 'metrics.json', 'parity.json', 'disks.json',
    'plugins.json', 'logfiles.json', 'error_resolver.json',
]


class TestSeedFixtures(unittest.TestCase):
    def test_every_domain_has_a_seed(self):
        for name in REQUIRED:
            self.assertTrue(os.path.isfile(os.path.join(SEED, name)), name)

    def test_seeds_are_graphql_envelopes(self):
        for name in REQUIRED:
            doc = context.fixture_json('seed/' + name)
            self.assertIn('data', doc, name)

    def test_resolver_error_fixture_has_null_data_and_errors(self):
        doc = context.fixture_json('seed/error_resolver.json')
        self.assertIsNone(doc['data'])
        self.assertTrue(doc['errors'])

    def test_non_json_fixtures_present(self):
        self.assertTrue(os.path.isfile(os.path.join(SEED, 'error_504.html')))
        self.assertTrue(os.path.isfile(os.path.join(SEED, 'error_malformed.txt')))

    def test_empty_array_fixture_is_all_zero_capacity(self):
        arr = context.fixture_json('seed/array_empty.json')['data']['array']
        kb = arr['capacity']['kilobytes']
        self.assertEqual(('0', '0', '0'), (kb['free'], kb['used'], kb['total']))
        self.assertEqual([], arr['disks'])

    def test_no_fixture_contains_anything_key_shaped(self):
        # A captured response must never carry a credential into the repo.
        import re
        keyish = re.compile(r'[A-Za-z0-9_\-]{40,}')
        for entry in os.listdir(SEED):
            path = os.path.join(SEED, entry)
            with open(path, 'r', encoding='utf-8') as fh:
                self.assertIsNone(keyish.search(fh.read()), entry)


class TestCaptureScript(unittest.TestCase):
    def test_capture_script_never_takes_a_key_argument(self):
        path = os.path.join(os.path.dirname(context.FIXTURES), '..', '..',
                            'scripts', 'capture_fixtures.py')
        with open(os.path.abspath(path), 'r', encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn("'--key'", src)
        self.assertNotIn('"--key"', src)
        self.assertIn('UNRAID_API_KEY', src)


if __name__ == '__main__':
    unittest.main()
