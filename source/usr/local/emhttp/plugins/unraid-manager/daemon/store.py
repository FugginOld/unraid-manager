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

SCHEMA_VERSION = 1
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
    conn = sqlite3.connect(os.path.join(db_dir, DB_FILENAME), timeout=30.0)
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
        for table in ('node_state', 'samples', 'events'):
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
