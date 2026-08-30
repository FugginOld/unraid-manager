"""Does pending_count survive a daemon restart?  (docs/verification/p1-m1-check.md)

Never exercised live: the counters were at zero when a restart was available,
and non-zero only after the reload fix had made restarting unnecessary.

The lever is a threshold change, not a node outage. Stopping a node's API makes
every indicator propose UNKNOWN, and apply_hysteresis returns immediately on
UNKNOWN carrying the count unchanged - so an outage produces pending_count=0
and proves nothing. A threshold that moves under the disks makes thermal
propose a real state, which is what actually drives the ladder.

  1. temp_warn below the hottest disk  -> thermal proposes WATCH
     two polls (ESCALATE_AFTER=2)      -> state=watch, count back to 0
  2. temp_warn restored                -> thermal proposes OK
     polls until pending_count == 2    -> mid-clear, 3 short of CLEAR_AFTER=5
  3. rc stop, read the DB with nothing running, rc start
  4. the count must be intact across the stop, and must CONTINUE across the
     start rather than restarting at 1.

Restores manager.cfg byte-for-byte in a finally block, whatever happens.
Read-only on the database throughout.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time

PLUGIN = '/usr/local/emhttp/plugins/unraid-manager'
SOCK = '/var/run/unraid-manager/managerd.sock'
CFG = '/boot/config/plugins/unraid-manager/manager.cfg'
RC = PLUGIN + '/scripts/rc.unraid-manager'

# db_path is an operator setting, not a constant - ask the daemon's own reader
# where the database is rather than guessing a pool name.
sys.path.insert(0, PLUGIN + '/daemon')
from config import read_manager_cfg  # noqa: E402
DB = os.path.join(read_manager_cfg(CFG)['db_path'], 'manager.db')
NODE = os.environ.get('UM_NODE', 'Raven')
INDICATOR = 'thermal'
COLD = '25'          # below any plausible disk temperature -> WATCH
DEADLINE = 180       # seconds to wait for any single expected transition


def say(msg):
    print(msg, flush=True)


def daemon(cmd, **kw):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30)
    try:
        s.connect(SOCK)
        s.sendall((json.dumps(dict(cmd=cmd, **kw)) + '\n').encode())
        return s.recv(65536).decode().strip()
    finally:
        s.close()


def row():
    """(state, pending_state, pending_count, updated_at) for the tracked row."""
    conn = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    try:
        got = conn.execute(
            'SELECT h.state, coalesce(h.pending_state, "-"), h.pending_count, h.updated_at '
            'FROM node_health h JOIN nodes n ON n.id = h.node_id '
            'WHERE n.name = ? AND h.indicator = ?', (NODE, INDICATOR)).fetchone()
    finally:
        conn.close()
    return got


def show(label):
    got = row()
    say('  %-22s state=%-8s pending=%-8s count=%s  (%s)'
        % (label, got[0], got[1], got[2], got[3]))
    return got


def wait_for(predicate, what, overshot=None):
    """Poll the node until the row satisfies predicate. Returns the row.

    `overshot` catches the window closing before we sampled it - the clear is
    five polls and we want to stop at two, so an extra scheduled poll landing
    between ours could carry it past. That is a retryable setup failure, not a
    result, and it must not be reported as a timeout.
    """
    end = time.time() + DEADLINE
    while time.time() < end:
        daemon('poll_now')
        time.sleep(2.5)
        got = row()
        if predicate(got):
            return got
        if overshot is not None and overshot(got):
            say('OVERSHOT while waiting for %s - the row moved past the window '
                'before it could be sampled: %s' % (what, got))
            say('Nothing is broken; this is a timing miss. Re-run to try again.')
            sys.exit(2)
    say('TIMEOUT waiting for %s - last row: %s' % (what, row()))
    sys.exit(2)


def set_temp_warn(value):
    """Rewrite temp_warn in manager.cfg and make the daemon re-read it."""
    with open(CFG, encoding='utf-8', newline='') as fh:
        lines = fh.read().split('\n')
    out = []
    for line in lines:
        if line.startswith('temp_warn='):
            out.append('temp_warn=' + value)
        else:
            out.append(line)
    with open(CFG, 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(out))
    say(daemon('reload')[:120])


def rc(action):
    p = subprocess.run([RC, action], capture_output=True, text=True)
    say('  rc %-7s %s' % (action, p.stdout.strip() or p.stderr.strip()))


def socket_up():
    try:
        daemon('status')
        return True
    except Exception:
        return False


with open(CFG, encoding='utf-8', newline='') as fh:
    ORIGINAL = fh.read()
say('manager.cfg saved (%d bytes); it is restored no matter how this exits.' % len(ORIGINAL))

start = row()
if start is None:
    say('No %s row for node %r. Set UM_NODE to a node that has one.' % (INDICATOR, NODE))
    sys.exit(2)
if start[0] == 'unknown':
    say('%s is unknown on %s - no disk temperature is reaching the daemon, so '
        'this indicator cannot be driven. Nothing changed.' % (INDICATOR, NODE))
    sys.exit(2)

failures = []
try:
    say('')
    say('-- baseline')
    show('start')

    say('')
    say('-- 1. temp_warn=%s: thermal should propose WATCH and escalate' % COLD)
    set_temp_warn(COLD)
    wait_for(lambda r: r[0] == 'watch', 'thermal to reach watch')
    show('escalated')

    say('')
    say('-- 2. temp_warn restored: the 5-sample clear starts counting')
    set_temp_warn('')
    banked = wait_for(lambda r: r[2] >= 2 and r[1] == 'ok',
                      'pending_count to reach 2',
                      overshot=lambda r: r[0] == 'ok')
    show('mid-clear')
    count_before, state_before = banked[2], banked[0]

    say('')
    say('-- 3. stop the daemon and read the database with nothing running')
    rc('stop')
    time.sleep(1)
    stopped = show('after stop')
    if stopped[2] != count_before or stopped[0] != state_before:
        failures.append('the count did not survive the STOP: %s/%s before, %s/%s after'
                        % (state_before, count_before, stopped[0], stopped[2]))
    else:
        say('  OK: pending_count=%s survived the process exiting.' % count_before)

    say('')
    say('-- 4. start it again; the first poll must CONTINUE the count, not restart it')
    rc('start')
    end = time.time() + 60
    while time.time() < end and not socket_up():
        time.sleep(1)
    if not socket_up():
        failures.append('the daemon did not come back up within 60s')
    else:
        after = wait_for(lambda r: r[3] != stopped[3], 'the first poll after the restart')
        show('after restart')
        if after[0] == 'ok':
            failures.append('it jumped straight to ok on the first poll after the restart '
                            '- the remaining samples were skipped')
        elif after[2] <= count_before and after[2] <= 1:
            failures.append('the count RESTARTED at %s after banking %s'
                            % (after[2], count_before))
        elif after[2] < count_before:
            failures.append('the count went BACKWARDS: %s banked, %s after the restart'
                            % (count_before, after[2]))
        else:
            say('  OK: %s banked before the restart, %s after - it continued.'
                % (count_before, after[2]))
finally:
    with open(CFG, 'w', encoding='utf-8', newline='') as fh:
        fh.write(ORIGINAL)
    say('')
    say('manager.cfg restored.')
    try:
        if socket_up():
            say(daemon('reload')[:120])
        else:
            rc('start')
    except Exception as exc:
        say('could not reload after restoring: %s' % exc)

say('')
if failures:
    say('FAILED:')
    for f in failures:
        say('  - ' + f)
    sys.exit(1)
say('PASS: pending_count survived the restart and continued from where it was.')
