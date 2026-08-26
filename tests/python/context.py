"""Put the daemon package dir on sys.path for every test in this directory.

unittest discovery inserts tests/python at sys.path[0], so `import context`
resolves from any test module here.
"""
import os
import sys

DAEMON = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..',
    'source', 'usr', 'local', 'emhttp', 'plugins', 'unraid-manager', 'daemon'))
FIXTURES = os.path.abspath(os.path.join(os.path.dirname(__file__), 'fixtures'))

if DAEMON not in sys.path:
    sys.path.insert(0, DAEMON)


def fixture(name):
    """Read a fixture file's text by path relative to tests/python/fixtures."""
    with open(os.path.join(FIXTURES, name), 'r', encoding='utf-8') as fh:
        return fh.read()


def fixture_json(name):
    import json
    return json.loads(fixture(name))
