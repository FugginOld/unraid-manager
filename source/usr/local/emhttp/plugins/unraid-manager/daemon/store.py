"""SQLite on the pool: schema, guards, writes, retention.

Everything durable the manager knows lives here, and NOTHING here lives on
flash. The daemon is the only writer; the PHP layer opens this database
read-only (spec section 5). Keys are never stored in it.
"""
import datetime
import json
import os
import posixpath
import sqlite3

SCHEMA_VERSION = 2
DB_FILENAME = 'manager.db'


class FlashPathError(ValueError):
    """db_path resolves onto the USB flash device."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, port INTEGER NOT NULL,
  tier INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
  added_at TEXT NOT NULL, last_seen TEXT);
CREATE TABLE IF NOT EXISTS node_state(
  node_id TEXT NOT NULL, domain TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ok','error','unknown')),
  error TEXT, fetched_at TEXT, payload TEXT,
  PRIMARY KEY(node_id, domain));
CREATE TABLE IF NOT EXISTS samples(
  node_id TEXT NOT NULL, metric TEXT NOT NULL, ts TEXT NOT NULL, value REAL NOT NULL);
CREATE INDEX IF NOT EXISTS samples_by_series ON samples(node_id, metric, ts);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, node_id TEXT,
  kind TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS node_health(
  node_id TEXT NOT NULL, indicator TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('ok','watch','warn','unknown')),
  value REAL, basis TEXT,
  pending_state TEXT, pending_count INTEGER NOT NULL DEFAULT 0,
  since TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY(node_id, indicator));
"""


def utcnow():
    """The one timestamp format: ISO-8601 UTC, second resolution, 'Z' suffix."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def validate_db_path(raw):
    """Return the normalized telemetry directory, or refuse.

    Order matters: the flash check runs FIRST, before the parent-exists check,
    so an operator who typed a /boot path is told the real reason rather than a
    filesystem detail. Flash is a USB stick with finite write endurance and
    telemetry writes must never land on it (plan section 3, spec constraint 6).
    """
    if raw is None or not str(raw).strip():
        raise ValueError('db_path is not set. Set it on the Unraid-Manager settings page '
                         'to a directory on a pool, e.g. /mnt/user/appdata/unraid-manager')

    path = str(raw).strip()
    # Compare in POSIX form regardless of the host that is running the check,
    # so this refusal is identical in a Windows unit test and on the box.
    posix = posixpath.normpath(path.replace('\\', '/'))
    if posix == '/boot' or posix.startswith('/boot/'):
        raise FlashPathError(
            'db_path %r is on the USB flash device. Telemetry is written continuously '
            'and flash has finite write endurance - point db_path at a pool, '
            'e.g. /mnt/user/appdata/unraid-manager' % path)

    normalized = os.path.normpath(path)
    parent = os.path.dirname(normalized.rstrip(os.sep)) or os.sep
    if not os.path.isdir(normalized) and not os.path.isdir(parent):
        raise ValueError('db_path %r does not exist and neither does its parent. '
                         'Is the pool mounted?' % path)
    return normalized


def connect(db_dir):
    """Open (creating if needed) the manager database under db_dir."""
    db_dir = validate_db_path(db_dir)
    os.makedirs(db_dir, exist_ok=True)
    # check_same_thread=False because the daemon uses one connection from three
    # places: the main tick loop, a worker pool of eight, and the control
    # socket's listener thread. sqlite3 otherwise refuses any use off the
    # creating thread outright - observed on Raven, where the first status
    # call raised ProgrammingError and every poll would have done the same.
    # Serialisation is Manager._lock's job, and that is not optional: this
    # flag removes the interpreter's guard, it does not make the connection
    # safe to share unsynchronised.
    conn = sqlite3.connect(os.path.join(db_dir, DB_FILENAME), timeout=30.0,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(SCHEMA)
    conn.execute('PRAGMA user_version=%d' % SCHEMA_VERSION)
    conn.commit()
    return conn


VALID_STATUS = ('ok', 'error', 'unknown')

_NODE_FIELDS = ('name', 'address', 'port', 'tier', 'enabled')


def sync_registry(conn, nodes):
    """Make the nodes table match the flash registry exactly.

    The flash cfg is authoritative: it survives loss of the pool. A node that is
    no longer in it is gone, and its telemetry goes with it - otherwise a
    re-enrolled address inherits a stranger's history.
    """
    existing = {r['id']: r for r in conn.execute('SELECT * FROM nodes')}
    seen = set()
    added, updated = [], []

    for node in nodes:
        node_id = node['id']
        seen.add(node_id)
        values = (node['name'], node['address'], int(node['port']),
                  int(node.get('tier', 0)), 1 if node.get('enabled', True) else 0)
        if node_id not in existing:
            conn.execute(
                'INSERT INTO nodes(id,name,address,port,tier,enabled,added_at) '
                'VALUES(?,?,?,?,?,?,?)', (node_id,) + values + (utcnow(),))
            added.append(node_id)
            continue
        row = existing[node_id]
        if tuple(row[f] for f in _NODE_FIELDS) != values:
            conn.execute(
                'UPDATE nodes SET name=?,address=?,port=?,tier=?,enabled=? WHERE id=?',
                values + (node_id,))
            updated.append(node_id)

    removed = [i for i in existing if i not in seen]
    for node_id in removed:
        for table in ('node_state', 'samples', 'events', 'node_health'):
            conn.execute('DELETE FROM %s WHERE node_id=?' % table, (node_id,))
        conn.execute('DELETE FROM nodes WHERE id=?', (node_id,))

    conn.commit()
    return {'added': added, 'updated': updated, 'removed': removed}


def upsert_state(conn, node_id, domain, status, payload=None, error=None, fetched_at=None):
    """Record one domain's outcome.

    A failed or unreadable poll keeps the last-good payload and the fetched_at
    that goes with it: the UI shows what was last true and says when, which is
    a different and more useful thing than showing nothing. Constraint 5 lives
    in the status column, not in the payload.
    """
    if status not in VALID_STATUS:
        raise ValueError('status must be one of %r, got %r' % (VALID_STATUS, status))

    if status == 'ok':
        conn.execute(
            'INSERT INTO node_state(node_id,domain,status,error,fetched_at,payload) '
            'VALUES(?,?,?,NULL,?,?) '
            'ON CONFLICT(node_id,domain) DO UPDATE SET '
            'status=excluded.status, error=NULL, fetched_at=excluded.fetched_at, '
            'payload=excluded.payload',
            (node_id, domain, status, fetched_at or utcnow(),
             json.dumps(payload if payload is not None else {}, separators=(',', ':'))))
    else:
        conn.execute(
            'INSERT INTO node_state(node_id,domain,status,error,fetched_at,payload) '
            'VALUES(?,?,?,?,NULL,NULL) '
            'ON CONFLICT(node_id,domain) DO UPDATE SET '
            'status=excluded.status, error=excluded.error',
            (node_id, domain, status, error))
    conn.commit()


def add_samples(conn, node_id, rows, ts=None):
    """Append numeric series points. Non-numeric values are dropped, not raised:
    a metric a box does not report must not cost us the ones it does."""
    stamp = ts or utcnow()
    payload = []
    for metric, value in rows:
        try:
            payload.append((node_id, metric, stamp, float(value)))
        except (TypeError, ValueError):
            continue
    if payload:
        conn.executemany('INSERT INTO samples(node_id,metric,ts,value) VALUES(?,?,?,?)', payload)
        conn.commit()
    return len(payload)


def log_event(conn, kind, message, node_id=None):
    cur = conn.execute('INSERT INTO events(ts,node_id,kind,message) VALUES(?,?,?,?)',
                       (utcnow(), node_id, kind, message))
    conn.commit()
    return cur.lastrowid


def touch_last_seen(conn, node_id, ts=None):
    conn.execute('UPDATE nodes SET last_seen=? WHERE id=?', (ts or utcnow(), node_id))
    conn.commit()


def prune(conn, now=None, sample_days=7, event_cap=10000, vacuum=False):
    """Retention, per spec section 4. Runs daily from cron; VACUUM weekly.

    Timestamps are lexically comparable ISO-8601 UTC, so the cutoff is a string
    comparison -- which also means a row with a malformed ts sorts outside the
    window and is left alone rather than deleted. That is the safe direction.
    """
    stamp = now or utcnow()
    cutoff = (datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')
              - datetime.timedelta(days=sample_days)).strftime('%Y-%m-%dT%H:%M:%SZ')

    samples = conn.execute(
        "DELETE FROM samples WHERE ts < ? AND ts LIKE '____-__-__T__:__:__Z'",
        (cutoff,)).rowcount
    events = conn.execute(
        'DELETE FROM events WHERE id NOT IN '
        '(SELECT id FROM events ORDER BY id DESC LIMIT ?)', (event_cap,)).rowcount
    conn.commit()

    if vacuum:
        conn.execute('VACUUM')

    return {'samples': max(samples, 0), 'events': max(events, 0), 'vacuumed': bool(vacuum)}


VALID_HEALTH = ('ok', 'watch', 'warn', 'unknown')


def upsert_health(conn, node_id, indicator, state, value=None, basis=None,
                  pending_state=None, pending_count=0, now=None):
    """Record one indicator's verdict.

    `since` is preserved while the state is unchanged and reset when it changes,
    which is what lets the UI say "degraded for 4 hours" instead of just
    "degraded". `updated_at` always advances, so a stale row is visible as one.
    """
    if state not in VALID_HEALTH:
        raise ValueError('invalid health state: %r' % state)
    stamp = now or utcnow()
    previous = conn.execute(
        'SELECT state, since FROM node_health WHERE node_id=? AND indicator=?',
        (node_id, indicator)).fetchone()
    since = previous['since'] if previous and previous['state'] == state else stamp
    conn.execute(
        'INSERT INTO node_health(node_id,indicator,state,value,basis,'
        'pending_state,pending_count,since,updated_at) VALUES(?,?,?,?,?,?,?,?,?) '
        'ON CONFLICT(node_id,indicator) DO UPDATE SET '
        'state=excluded.state, value=excluded.value, basis=excluded.basis, '
        'pending_state=excluded.pending_state, pending_count=excluded.pending_count, '
        'since=excluded.since, updated_at=excluded.updated_at',
        (node_id, indicator, state, value, basis, pending_state,
         int(pending_count), since, stamp))
    conn.commit()


def read_health(conn, node_id):
    """Every indicator for one node, keyed by indicator name."""
    rows = conn.execute('SELECT * FROM node_health WHERE node_id=?', (node_id,)).fetchall()
    return {r['indicator']: dict(r) for r in rows}


def recent_samples(conn, node_id, metric, since_ts):
    """(ts, value) pairs at or after since_ts, ascending.

    Timestamps are lexically comparable ISO-8601 UTC, so the window is a string
    comparison - the same property retention relies on.
    """
    rows = conn.execute(
        'SELECT ts, value FROM samples WHERE node_id=? AND metric=? AND ts >= ? '
        'ORDER BY ts', (node_id, metric, since_ts)).fetchall()
    return [(r['ts'], r['value']) for r in rows]
