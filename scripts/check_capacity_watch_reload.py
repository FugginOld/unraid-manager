"""Does a saved `capacity_watch` reach the evaluator without a restart?

capacity_watch was in HEALTH_THRESHOLDS and not in RELOADABLE, so the settings
page reported success, the flash file was correct, and the evaluator went on
using the value it booted with. Fixed by deriving one tuple from the other;
this is the live check that the fix is on the box and working.

The state is the discriminator, not the basis: evaluate_capacity's WATCH basis
names the high-water mark, never the watch level, so the string looks identical
either way. Only the verdict moves.

  1. capacity_high_water=99, capacity_watch just ABOVE this node's usage
     -> proposes OK, wait for the clear to settle there
  2. capacity_watch just BELOW it, high_water untouched, RELOAD ONLY
     -> must reach WATCH. One key changed, no restart. The old build cannot
        do this: it never picked the key up, so it stays OK forever.

Thresholds are computed from the node's own reported percentage, so this works
on any box with a populated array. Restores manager.cfg byte-for-byte in a
finally block and is read-only on the database.
"""
import json
import os
import socket
import sqlite3
import sys
import time

PLUGIN = '/usr/local/emhttp/plugins/unraid-manager'
SOCK = '/var/run/unraid-manager/managerd.sock'
CFG = '/boot/config/plugins/unraid-manager/manager.cfg'

sys.path.insert(0, PLUGIN + '/daemon')
from config import read_manager_cfg  # noqa: E402
DB = os.path.join(read_manager_cfg(CFG)['db_path'], 'manager.db')

NODE = os.environ.get('UM_NODE', 'Golem')
INDICATOR = 'capacity'
HIGH = '99'          # above any real usage, so high-water never decides
DEADLINE = 240


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


def row():
    conn = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    try:
        return conn.execute(
            'SELECT h.state, h.value, h.basis, coalesce(h.pending_state, "-"), '
            'h.pending_count FROM node_health h JOIN nodes n ON n.id = h.node_id '
            'WHERE n.name = ? AND h.indicator = ?', (NODE, INDICATOR)).fetchone()
    finally:
        conn.close()


def show(label):
    got = row()
    say('  %-18s state=%-6s value=%-6s pending=%-6s count=%s'
        % (label, got[0], got[1], got[3], got[4]))
    say('  %-18s %s' % ('', got[2]))
    return got


def wait_for(state, what):
    end = time.time() + DEADLINE
    while time.time() < end:
        daemon('poll_now')
        time.sleep(2.5)
        if row()[0] == state:
            return row()
    say('TIMEOUT waiting for %s - last row: %s' % (what, row()))
    return None


def set_thresholds(**want):
    """Rewrite the named keys in manager.cfg, appending any it does not carry."""
    want = {k: str(v) for k, v in want.items()}
    with open(CFG, encoding='utf-8', newline='') as fh:
        lines = fh.read().split(chr(10))
    out, seen = [], set()
    for line in lines:
        key = line.split('=', 1)[0]
        if key in want:
            out.append('%s=%s' % (key, want[key]))
            seen.add(key)
        else:
            out.append(line)
    for key in want:
        if key not in seen:
            out.insert(len(out) - 1 if out and out[-1] == '' else len(out),
                       '%s=%s' % (key, want[key]))
    with open(CFG, 'w', encoding='utf-8', newline='') as fh:
        fh.write(chr(10).join(out))
    say('  reload -> ' + daemon('reload')[:100])


with open(CFG, encoding='utf-8', newline='') as fh:
    ORIGINAL = fh.read()
say('manager.cfg saved (%d bytes); it is restored no matter how this exits.'
    % len(ORIGINAL))

start = row()
if start is None:
    say('No %s row for node %r. Set UM_NODE to a node that has one.' % (INDICATOR, NODE))
    sys.exit(2)
if start[1] is None:
    say('%s reports no percentage on %s (%r) - an empty array has nothing to '
        'drive. Use a node with a populated array.' % (INDICATOR, NODE, start[2]))
    sys.exit(2)

pct = float(start[1])
if not 12 <= pct <= 96:
    say('%s is at %g%% on %s, too close to a threshold bound (10-98) to leave '
        'room either side. Nothing changed.' % (INDICATOR, pct, NODE))
    sys.exit(2)
# int() truncates, so HIT <= pct <= MISS with a real gap either side.
HIT, MISS = str(int(pct)), str(min(98, int(pct) + 2))

failures = []
try:
    say('')
    say('-- baseline')
    show('start')
    say('  %s is at %g%% used; watch at %s should fire, watch at %s should not.'
        % (NODE, pct, HIT, MISS))

    say('')
    say('-- 1. high_water=%s, capacity_watch=%s: settle on OK' % (HIGH, MISS))
    set_thresholds(capacity_high_water=HIGH, capacity_watch=MISS)
    if wait_for('ok', 'capacity to settle on ok') is None:
        failures.append('could not reach a clean OK to start from')
        raise SystemExit
    show('settled')

    say('')
    say('-- 2. capacity_watch=%s ONLY, reload ONLY: must reach WATCH' % HIT)
    set_thresholds(capacity_high_water=HIGH, capacity_watch=HIT)
    if wait_for('watch', 'capacity to reach watch') is None:
        failures.append('capacity_watch=%s did not reach the evaluator on a '
                        'reload - this is the bug, still present on this box '
                        '(is the fixed managerd.py deployed?)' % HIT)
    else:
        show('after reload')
        say('  OK: one key, no restart, and the verdict moved.')
except SystemExit:
    pass
finally:
    with open(CFG, 'w', encoding='utf-8', newline='') as fh:
        fh.write(ORIGINAL)
    say('')
    say('manager.cfg restored.')
    try:
        say('  reload -> ' + daemon('reload')[:100])
    except Exception as exc:
        say('could not reload after restoring: %s' % exc)

say('')
if failures:
    say('FAILED:')
    for f in failures:
        say('  - ' + f)
    sys.exit(1)
say('PASS: capacity_watch reaches the evaluator on a reload, no restart.')
