#!/usr/bin/env python3
"""Capture Tier 0 GraphQL responses from a live Unraid box into test fixtures.

The operator runs this by hand. It is NOT part of the test suite and no test
imports it — tests read the files it writes.

The API key comes from the UNRAID_API_KEY environment variable, or from a
prompt. It is deliberately NOT a command-line argument: arguments land in shell
history and in the process table. Nothing this script writes contains the key.

  set UNRAID_API_KEY=<paste>
  python scripts/capture_fixtures.py --host 192.168.2.19 --port 29220 --label raven
"""
import argparse
import getpass
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HERE, '..', 'tests', 'python', 'fixtures')

# Same query text the collector uses. Kept literal here rather than imported so
# a capture proves the text works against a real box before collector.py trusts
# it — that is the whole point of running this.
QUERIES = {
    'info': '{ info { os { hostname release kernel uptime } versions { core { unraid api kernel } } } }',
    'array': ('{ array { state capacity { kilobytes { free used total } disks { free used total } } '
              'parityCheckStatus { status progress errors correcting paused running } '
              'parities { idx name device size status temp numErrors } '
              'disks { idx name device size status temp numErrors fsType fsSize fsFree fsUsed } '
              'caches { name fsType fsSize fsFree fsUsed size } } }'),
    'shares': '{ shares { name free used size floor } }',
    'notifications': '{ notifications { overview { unread { info warning alert total } } } }',
    'metrics': '{ metrics { cpu { percentTotal } memory { total used free percentTotal } } }',
    'parity': ('{ parityHistory { date duration speed status errors progress '
               'correcting paused running } }'),
    'disks': ('{ disks { device name vendor size temperature smartStatus interfaceType serialNum } '
              'assignableDisks { device name vendor size temperature smartStatus interfaceType serialNum } }'),
    'plugins': '{ installedUnraidPlugins }',
    'logfiles': '{ logFiles { name path size modifiedAt } }',
}

TIMEOUTS = {'disks': 90}
DEFAULT_TIMEOUT = 20

SERIAL_KEYS = ('serialNum', 'serial')


def mask(value):
    """Length-preserving mask so a fixture keeps its shape without its identity."""
    return re.sub(r'[A-Za-z0-9]', 'X', value)


def scrub_serials(node):
    if isinstance(node, dict):
        return {k: (mask(v) if k in SERIAL_KEYS and isinstance(v, str) else scrub_serials(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [scrub_serials(v) for v in node]
    return node


def scrub_serials_text(text):
    """Best-effort scrub for a body that didn't parse as JSON (HTML error page,
    truncated response) — a serial can still appear in a partial payload."""
    pattern = r'"(%s)"\s*:\s*"([^"]*)"' % '|'.join(SERIAL_KEYS)
    return re.sub(pattern, lambda m: '"%s":"%s"' % (m.group(1), mask(m.group(2))), text)


def fetch(host, port, key, query, timeout):
    url = 'https://%s:%d/graphql' % (host, port)
    body = json.dumps({'query': query}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('x-api-key', key)
    # Unraid serves a self-signed certificate on its LAN address. This script
    # talks to boxes the operator names on their own network; verification is
    # off for the same reason the collector's is.
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', required=True)
    ap.add_argument('--port', type=int, required=True)
    ap.add_argument('--label', required=True, help='fixture subdirectory, e.g. raven')
    args = ap.parse_args()

    # A label is joined straight into a filesystem path below. Reject anything
    # that could escape tests/python/fixtures/ or collide with the hand-written
    # seed/ baseline this script must never overwrite.
    if os.sep in args.label or (os.altsep and os.altsep in args.label) or '/' in args.label \
            or args.label in ('.', '..', 'seed'):
        print("--label must be a plain directory name, not a path, and not 'seed'", file=sys.stderr)
        return 2

    key = os.environ.get('UNRAID_API_KEY') or getpass.getpass('API key for %s: ' % args.host)
    if not key.strip():
        print('no API key supplied', file=sys.stderr)
        return 2
    key = key.strip()

    out = os.path.abspath(os.path.join(OUT_ROOT, args.label))
    os.makedirs(out, exist_ok=True)

    failures = 0
    for domain, query in QUERIES.items():
        timeout = TIMEOUTS.get(domain, DEFAULT_TIMEOUT)
        try:
            status, raw = fetch(args.host, args.port, key, query, timeout)
        except Exception as exc:                       # noqa: BLE001 - report and continue
            # Never let a key reach the terminal through an exception string.
            print('%-14s TRANSPORT %s' % (domain, str(exc).replace(key, '<redacted>')))
            failures += 1
            continue

        path = os.path.join(out, domain + ('.json' if status == 200 else '.error'))
        try:
            doc = json.loads(raw.decode('utf-8'))
            text = json.dumps(scrub_serials(doc), indent=None)
        except (ValueError, UnicodeDecodeError):
            text = scrub_serials_text(raw.decode('utf-8', 'replace'))
            path = os.path.join(out, domain + '.error')

        # A plain assert is a credential backstop; python -O strips asserts, so
        # this check must not be one.
        if key in text:
            raise SystemExit('refusing to write a fixture containing the key (%s)' % domain)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text + '\n')

        note = 'ok' if status == 200 and '"errors"' not in text else 'HTTP %s / errors' % status
        try:
            shown = os.path.relpath(path)
        except ValueError:
            # relpath raises when path and cwd are on different drives (Windows).
            shown = path
        print('%-14s %-6s %s  -> %s' % (domain, status, note, shown))
        if note != 'ok':
            failures += 1

    print('\ncaptured into %s (%d domain(s) not clean)' % (out, failures))
    print('Review every file for anything identifying before committing.')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
