"""Is `disks.stale` ever non-empty?  (docs/verification/p1-m1-check.md)

Never observed live: `node_state` survived every upgrade, so both nodes always
had a retained payload and neither branch that fills the list has run on a box.
There are two, and this drives both:

  1. no `node_state` row at all - enrolled but never polled
     -> 'no disks poll recorded yet'
  2. a poll that failed with no prior payload to retain
     -> the domain's own error, payload NULL

The lever is one throwaway node pointed at a CLOSED PORT ON THIS BOX. A refused
connection reaches the same branch as the 504 in api/disks.php's header without
needing a peer to be sick, and it cannot touch either real node.

The two branches live in um_fleet_disks(), so this calls THAT - api/disks.php
guards its endpoint body with `PHP_SAPI !== 'cli'`, which makes the file safe to
include from the CLI and its functions callable. Reimplementing the branches
here would only prove that this script agrees with itself.

nodes.cfg is restored byte-for-byte in a finally block and the key file removed,
whatever happens. Read-only on the database throughout; the probe's rows leave
with the node, since disks.php lists what is in `nodes`.
"""
import json
import os
import socket
import subprocess
import sqlite3
import sys
import time

PLUGIN = '/usr/local/emhttp/plugins/unraid-manager'
SOCK = '/var/run/unraid-manager/managerd.sock'
CFG_DIR = '/boot/config/plugins/unraid-manager'
NODES = CFG_DIR + '/nodes.cfg'
KEYS = CFG_DIR + '/keys'

sys.path.insert(0, PLUGIN + '/daemon')
from config import read_manager_cfg  # noqa: E402
DB = os.path.join(read_manager_cfg(CFG_DIR + '/manager.cfg')['db_path'], 'manager.db')

PROBE = 'um-stale-probe'
PROBE_NAME = 'zz-stale-probe'      # sorts last, so it never displaces a real node
DEADLINE = 120


def say(msg):
    print(msg, flush=True)


def daemon(cmd, **kw):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30)
    try:
        s.connect(SOCK)
        s.sendall((json.dumps(dict(cmd=cmd, **kw)) + chr(10)).encode())
        return s.recv(65536).decode().strip()
    finally:
        s.close()


PHP = ('require "%s/api/disks.php"; '
       'echo json_encode(um_fleet_disks(um_db())["stale"]);' % PLUGIN)


def stale():
    """api/disks.php's own stale list, straight out of the php CLI."""
    # Errors off: a notice on stdout would land in the middle of the JSON and
    # read as a parse failure rather than as the notice it is.
    p = subprocess.run(['php', '-d', 'error_reporting=0', '-d', 'display_errors=0',
                        '-r', PHP], capture_output=True, text=True)
    if p.returncode != 0:
        say('php failed: ' + (p.stderr.strip() or p.stdout.strip()))
        sys.exit(2)
    return json.loads(p.stdout.strip() or '[]')


def probe_entry(entries):
    for entry in entries:
        if entry.get('node') == PROBE_NAME:
            return entry
    return None


def show(label):
    entries = stale()
    mine = probe_entry(entries)
    say('  %-18s stale has %d entr%s' % (label, len(entries),
                                         'y' if len(entries) == 1 else 'ies'))
    for entry in entries:
        say('      %s' % json.dumps(entry, sort_keys=True))
    return mine


def disks_row():
    """The probe's node_state row for the disks domain, if the lane has run."""
    conn = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    try:
        return conn.execute(
            'SELECT status, error, payload IS NULL FROM node_state '
            'WHERE node_id = ? AND domain = ?', (PROBE, 'disks')).fetchone()
    finally:
        conn.close()


ORIGINAL = open(NODES, encoding='utf-8', newline='').read()
KEYFILE = os.path.join(KEYS, PROBE + '.key')
say('nodes.cfg saved (%d bytes); it is restored no matter how this exits.'
    % len(ORIGINAL))

if probe_entry(stale()) is not None:
    say('A %r is already enrolled - a previous run did not clean up. Remove its '
        'section from nodes.cfg first.' % PROBE_NAME)
    sys.exit(2)

failures = []
try:
    say('')
    say('-- 1. enrol a node pointed at 127.0.0.1:1, a closed port')
    block = [''] if not ORIGINAL.endswith(chr(10)) else []
    block += ['[' + PROBE + ']', 'name=' + PROBE_NAME, 'address=127.0.0.1',
              'port=1', 'tier=0', 'enabled=1', '']
    open(NODES, 'w', encoding='utf-8', newline='').write(
        ORIGINAL + chr(10).join(block))
    # A key on file matters: with none, the collector answers `unknown` without
    # ever opening a socket, and the refused connection - the branch under test
    # - never happens. The value is never sent anywhere but a closed port.
    open(KEYFILE, 'w', encoding='utf-8', newline='').write('0' * 64)
    say('  reload -> ' + daemon('reload')[:160])

    mine = show('never polled')
    if mine is None:
        failures.append('the probe is enrolled but absent from stale - an '
                        'uncollected node is invisible, which is the fail-open '
                        'this branch exists to prevent')
    elif 'no disks poll recorded yet' not in (mine.get('error') or ''):
        failures.append('expected the never-polled reason, got %r' % mine)
    else:
        say('  OK: an enrolled, never-polled node is listed as uncollected.')

    say('')
    say('-- 2. poll it; the refused connection must leave a NULL payload')
    end = time.time() + DEADLINE
    row = None
    while time.time() < end:
        daemon('poll_now', node_id=PROBE)
        time.sleep(3)
        row = disks_row()
        if row is not None:
            break
    if row is None:
        failures.append('no disks row appeared for the probe within %ds' % DEADLINE)
    else:
        say('  node_state: status=%s payload_is_null=%s' % (row[0], bool(row[2])))
        say('  error: %s' % row[1])
        if row[0] == 'ok':
            failures.append('the probe polled OK against a closed port')
        if not row[2]:
            failures.append('payload is not NULL on a first-ever failed poll')
        mine = show('failed poll')
        if mine is None:
            failures.append('the probe polled and FAILED, and is not in stale - '
                            'a node with no readable disks is silently absent')
        elif mine.get('status') == 'ok' or not (mine.get('error') or '').strip():
            failures.append('listed without a usable reason: %r' % mine)
        else:
            say('  OK: the failure is listed, with its reason and status.')
finally:
    open(NODES, 'w', encoding='utf-8', newline='').write(ORIGINAL)
    try:
        os.remove(KEYFILE)
    except OSError:
        pass
    say('')
    say('nodes.cfg restored, key removed.')
    try:
        say('  reload -> ' + daemon('reload')[:160])
        left = probe_entry(stale())
        if left is not None:
            failures.append('the probe is STILL listed after being removed: %r' % left)
        else:
            say('  the probe is gone from stale.')
    except Exception as exc:
        say('could not reload after restoring: %s' % exc)

say('')
if failures:
    say('FAILED:')
    for f in failures:
        say('  - ' + f)
    sys.exit(1)
say('PASS: both stale branches render, and the fleet is back to what it was.')
