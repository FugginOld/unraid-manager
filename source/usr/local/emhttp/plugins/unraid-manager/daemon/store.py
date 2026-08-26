"""SQLite on the pool: schema, guards, writes, retention.

Everything durable the manager knows lives here, and NOTHING here lives on
flash. The daemon is the only writer; the PHP layer opens this database
read-only (spec section 5). Keys are never stored in it.
"""
import datetime
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
