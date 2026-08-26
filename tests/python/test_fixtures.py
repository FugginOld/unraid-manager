import json
import os
import re
import unittest
from datetime import datetime

import context

SEED = os.path.join(context.FIXTURES, 'seed')
SCHEMA_PATH = os.path.join(os.path.dirname(context.FIXTURES), '..', '..',
                            'docs', 'verification', 'graphql-schema-raven.json')

# Every domain the collector will parse must have a seed fixture, so no later
# task is blocked on a live box being reachable.
REQUIRED = [
    'info.json', 'array_populated.json', 'array_empty.json', 'shares.json',
    'notifications.json', 'metrics.json', 'parity.json', 'disks.json',
    'plugins.json', 'logfiles.json', 'error_resolver.json',
]


# --- schema walker ----------------------------------------------------------
# docs/verification/graphql-schema-raven.json is a committed introspection
# dump. Nothing validated fixtures against it, which is how the brief's
# invented `parityCheckStatus` leaf-string and `parityHistory.status: "OK"`
# shipped unnoticed — both rejected by the live schema, and both now rejected
# here too: unknown-field, object/scalar, enum-membership and scalar-type
# mismatches all fail the fixture, not just a live capture.

def _load_schema():
    with open(os.path.abspath(SCHEMA_PATH), 'r', encoding='utf-8') as fh:
        doc = json.load(fh)
    return {t['name']: t for t in doc['__schema']['types'] if t.get('name')}


TYPES = _load_schema()


def _field_shape(field):
    """Unwrap NON_NULL/LIST wrappers -> (is_list, kind, type_name, is_non_null).

    is_non_null reflects only the field's outermost wrapper: the field itself
    cannot be null. It does not track a NON_NULL wrapper on list *items*
    (e.g. the `!` in `[ArrayDisk!]!`) — no current fixture exercises a null
    list item, and every field this walker rejects null on today (array.state,
    array.parityCheckStatus, Query.array itself, Disk.smartStatus/size) is
    caught by the outer check alone.
    """
    is_non_null = field['type']['kind'] == 'NON_NULL'
    is_list = False
    t = field['type']
    while t['kind'] in ('NON_NULL', 'LIST'):
        if t['kind'] == 'LIST':
            is_list = True
        t = t['ofType']
    return is_list, t['kind'], t['name'], is_non_null


QUERY_FIELDS = {f['name']: _field_shape(f) for f in TYPES['Query']['fields']}

# Declared GraphQL scalar name -> the Python type a JSON-decoded value must
# have. bool is deliberately excluded from the numeric checks: isinstance(True,
# int) is True in Python, and a stray boolean must not pass as a BigInt.
SCALAR_TYPES = {
    'String': str, 'ID': str, 'PrefixedID': str, 'DateTime': str,
    'Boolean': bool,
    'Int': int,
    'Float': (int, float), 'BigInt': (int, float),
}


def _scalar_ok(type_name, value):
    py_type = SCALAR_TYPES.get(type_name)
    if py_type is None:
        return True  # unmapped scalar (JSON/Port/URL/...) - not exercised here
    if py_type is bool:
        return isinstance(value, bool)
    if py_type is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, py_type) and not isinstance(value, bool)


def _check_node(value, kind, type_name, path):
    if value is None:
        return
    if kind in ('OBJECT', 'INTERFACE'):
        if not isinstance(value, dict):
            raise AssertionError('expected %s object at %s, got %r' % (type_name, path, value))
        fields_list = TYPES[type_name].get('fields')
        if not fields_list:
            # UNION/INTERFACE-without-fields edge case: nothing generic to
            # validate against without the response's __typename. Assert
            # cleanly rather than let the dict comprehension below TypeError
            # on `for f in None`.
            raise AssertionError('%s: type %s (kind=%s) has no field list to validate against'
                                  % (path, type_name, kind))
        fields = {f['name']: f for f in fields_list}
        for key, sub in value.items():
            if key not in fields:
                raise AssertionError('unknown field %s.%s at %s' % (type_name, key, path))
            is_list, fkind, fname, fnn = _field_shape(fields[key])
            _check_field(sub, is_list, fkind, fname, path + '.' + key, fnn)
    elif kind == 'ENUM':
        members = {e['name'] for e in TYPES[type_name]['enumValues']}
        if value not in members:
            raise AssertionError('%s: %r is not a member of enum %s %s'
                                  % (path, value, type_name, sorted(members)))
    elif kind == 'UNION':
        if not isinstance(value, dict):
            raise AssertionError('expected union object at %s, got %r' % (path, value))
    else:
        # SCALAR leaf. JSON is checked first: it's the one scalar the schema
        # legitimately types as an arbitrary object/array (DockerContainer.
        # labels/mounts, InfoCpu.cache — 36 fields total), so the dict/list
        # rejection below must not run for it.
        if type_name == 'JSON':
            return
        if isinstance(value, (dict, list)):
            raise AssertionError('expected scalar at %s, got %r' % (path, value))
        if not _scalar_ok(type_name, value):
            raise AssertionError('%s: expected %s, got %r' % (path, type_name, value))


def _check_field(value, is_list, kind, type_name, path, is_non_null=False):
    if value is None:
        if is_non_null:
            raise AssertionError('%s: NON_NULL field is null' % path)
        return
    if is_list:
        if not isinstance(value, list):
            raise AssertionError('expected list at %s, got %r' % (path, value))
        for i, item in enumerate(value):
            _check_node(item, kind, type_name, '%s[%d]' % (path, i))
    else:
        _check_node(value, kind, type_name, path)


def assert_fixture_matches_schema(name, doc):
    """Walk doc['data'] against the live Query schema. Raises on mismatch."""
    data = doc['data']
    if data is None:
        return
    for key, value in data.items():
        if key not in QUERY_FIELDS:
            raise AssertionError('%s: unknown Query field %r' % (name, key))
        is_list, kind, type_name, is_non_null = QUERY_FIELDS[key]
        _check_field(value, is_list, kind, type_name, '%s:%s' % (name, key), is_non_null)


class TestSeedFixtures(unittest.TestCase):
    def test_seeds_are_graphql_envelopes(self):
        for name in REQUIRED:
            doc = context.fixture_json('seed/' + name)
            self.assertIn('data', doc, name)
            if name == 'error_resolver.json':
                self.assertIsNone(doc['data'], name)
            else:
                # A bare scalar or empty dict must not pass as an envelope.
                self.assertIsInstance(doc['data'], dict, name)
                self.assertTrue(doc['data'], name)

    def test_all_fixtures_match_the_live_schema(self):
        # The key guard was widened to the whole fixtures tree because a real
        # capture lands outside seed/ (fixtures/<label>/); a bad shape there
        # must fail the suite the same way a bad seed does, so this walks the
        # whole tree too, not just seed/.
        for root, _dirs, files in os.walk(context.FIXTURES):
            for entry in files:
                if not entry.endswith('.json'):
                    continue
                path = os.path.join(root, entry)
                rel = os.path.relpath(path, context.FIXTURES)
                with open(path, 'r', encoding='utf-8') as fh:
                    try:
                        doc = json.load(fh)
                    except ValueError:
                        continue  # not a GraphQL envelope, e.g. a malformed-response fixture
                if not isinstance(doc, dict) or 'data' not in doc:
                    continue
                assert_fixture_matches_schema(rel, doc)

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

    def test_non_running_parity_check_nulls_its_subfields(self):
        # tier0-coverage.md item 8: Golem, a POPULATED array with 300+ parity
        # history entries, still returns null errors/correcting/paused/running
        # whenever status isn't RUNNING. This is not an empty-array artifact —
        # both fixtures must agree, or a collector coercing null to False/0
        # only breaks against the one box nobody tested against.
        for name in ('array_populated.json', 'array_empty.json'):
            pc = context.fixture_json('seed/' + name)['data']['array']['parityCheckStatus']
            if pc['status'] != 'RUNNING':
                for field in ('errors', 'correcting', 'paused', 'running'):
                    self.assertIsNone(pc[field], '%s.parityCheckStatus.%s' % (name, field))

    def test_parity_history_and_check_status_null_asymmetrically(self):
        # tier0-coverage.md item 11: parityHistory rows and array.parityCheck
        # Status share a GraphQL type (ParityCheck) but null OPPOSITE fields —
        # a history row has progress null / errors non-null, an idle check
        # status has progress non-null / errors null. A parser shared across
        # both contexts is wrong in one of them; pin the asymmetry explicitly.
        for row in context.fixture_json('seed/parity.json')['data']['parityHistory']:
            self.assertIsNone(row['progress'], row)
            self.assertIsNotNone(row['errors'], row)
        for name in ('array_populated.json', 'array_empty.json'):
            pc = context.fixture_json('seed/' + name)['data']['array']['parityCheckStatus']
            if pc['status'] != 'RUNNING':
                self.assertIsNotNone(pc['progress'], name)
                self.assertIsNone(pc['errors'], name)

    def test_empty_array_has_no_parities_and_thirty_free_slots(self):
        # Raven's real healthy-but-empty state: this is the trap the schema
        # walker alone wouldn't catch (shape is valid, values were wrong).
        arr = context.fixture_json('seed/array_empty.json')['data']['array']
        self.assertEqual([], arr['parities'])
        self.assertEqual('30', arr['capacity']['disks']['free'])

    def test_info_versions_live_under_core(self):
        info = context.fixture_json('seed/info.json')['data']['info']
        core = info['versions']['core']
        self.assertEqual('7.3.2', core['unraid'])
        self.assertIn('api', core)
        self.assertIn('kernel', core)
        self.assertNotIn('unraid', info['versions'])

    def test_uptime_is_a_boot_timestamp_not_a_duration(self):
        uptime = context.fixture_json('seed/info.json')['data']['info']['os']['uptime']
        self.assertIsInstance(uptime, str)
        self.assertIn('T', uptime)  # ISO8601 date/time separator; a duration wouldn't have one
        datetime.fromisoformat(uptime.replace('Z', '+00:00'))

    def test_smart_status_is_only_ok_or_unknown(self):
        doc = context.fixture_json('seed/disks.json')['data']
        for entry in doc['disks'] + doc['assignableDisks']:
            self.assertIn(entry['smartStatus'], ('OK', 'UNKNOWN'), entry)

    def test_non_primary_pool_members_have_null_fs_fields(self):
        for name in ('array_populated.json', 'array_empty.json'):
            caches = context.fixture_json('seed/' + name)['data']['array']['caches']
            primaries = [c for c in caches if c['fsType'] is not None]
            secondaries = [c for c in caches if c['fsType'] is None]
            self.assertTrue(primaries, name)
            self.assertTrue(secondaries, name)
            for c in secondaries:
                self.assertIsNone(c['fsSize'], name)
                self.assertIsNone(c['fsFree'], name)

    def test_no_fixture_contains_anything_key_shaped(self):
        # A captured response must never carry a credential into the repo.
        # Real API keys are 64 lowercase-hex characters; this project's node
        # ids are UUIDs, which the hyphen-free character class below never
        # matches as one run (a UUID's longest hyphen-delimited segment is 12).
        # Seeds are hand-written, but a real capture lands in fixtures/<label>/
        # (outside seed/), so the whole fixtures tree is walked, not just seed/.
        keyish = re.compile(r'[A-Za-z0-9_]{28,}')
        for root, _dirs, files in os.walk(context.FIXTURES):
            for entry in files:
                path = os.path.join(root, entry)
                # Binary-safe decode: a non-utf8 file must not error the test
                # out from under the files after it — it should just be
                # scanned as best-effort text, same as the capture script does.
                with open(path, 'rb') as fh:
                    text = fh.read().decode('utf-8', 'replace')
                match = keyish.search(text)
                self.assertIsNone(match, os.path.relpath(path, context.FIXTURES))


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
