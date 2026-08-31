"""The manager daemon: scheduling, dispatch, lifecycle.

This module must stay import-safe -- the scheduler is unit-tested by importing
it, so nothing here opens a socket or a database at import time. The entrypoint
lives under `if __name__ == '__main__'`.
"""

import datetime
import functools
import json
import logging
import logging.handlers
import os
import posixpath
import re
import signal
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import agentclient
import collector
import config
import ctl
import gqlclient
import health
import store

BACKOFF_CAP = 600          # seconds; the slow lane's interval is the ceiling
UNKNOWN_AFTER = 3          # consecutive all-domain failures before a node is unknown
MAX_WORKERS = 8
NCHAN_CHANNEL = 'unraid-manager'

# The presence of this directory is what "the box has a pool" means: /mnt/user
# exists exactly when the array is up and there is somewhere durable to write.
# A module-level name so a test can point it somewhere real.
POOL_MARKER = '/mnt/user'

log = logging.getLogger('managerd')


class _NodeState(object):
    __slots__ = ('last_fast', 'last_slow', 'failures')

    def __init__(self):
        # When each lane was last DISPATCHED, not when it is next due. Storing
        # a deadline instead would freeze the interval at dispatch time, so a
        # failure recorded afterwards could not lengthen the slot it is meant
        # to lengthen -- the node would keep polling at the old rate for one
        # more cycle every time it backed off.
        self.last_fast = None     # never dispatched => due immediately
        self.last_slow = None
        self.failures = 0


class Scheduler(object):
    """Decides which (node, lane) pairs are due. Owns no clock and no threads.

    `now` is passed in, which is what lets a test walk ten minutes of backoff in
    a millisecond instead of sleeping through it.
    """

    def __init__(self, poll_fast=30, poll_slow=600):
        self.poll_fast = int(poll_fast)
        self.poll_slow = int(poll_slow)
        self._nodes = {}

    def set_nodes(self, node_ids):
        """Make the schedule match the registry, keeping state for survivors."""
        wanted = list(node_ids)
        for node_id in wanted:
            if node_id not in self._nodes:
                self._nodes[node_id] = _NodeState()     # due immediately
        for node_id in list(self._nodes):
            if node_id not in wanted:
                del self._nodes[node_id]

    def interval(self, node_id):
        """Effective fast interval: doubles per consecutive failure, capped."""
        state = self._nodes.get(node_id)
        if state is None:
            return self.poll_fast
        return min(self.poll_fast * (2 ** state.failures), BACKOFF_CAP)

    def consecutive_failures(self, node_id):
        state = self._nodes.get(node_id)
        return state.failures if state else 0

    def is_unknown(self, node_id):
        return self.consecutive_failures(node_id) >= UNKNOWN_AFTER

    def due(self, now):
        """Return the (node_id, lane) pairs due at `now`, marking them dispatched.

        Marking here rather than at completion is deliberate: the slow lane's
        timeout is 90s and the tick is 1s, so a poll that is merely slow would
        otherwise be handed out ninety more times.
        """
        out = []
        for node_id, state in self._nodes.items():
            if state.last_fast is None or now >= state.last_fast + self.interval(node_id):
                out.append((node_id, collector.FAST))
                state.last_fast = now
            # Backoff deliberately does not apply to the slow lane: it is
            # already ten minutes, and stretching it further would leave a
            # recovered node showing an hour-old disk list.
            if state.last_slow is None or now >= state.last_slow + self.poll_slow:
                out.append((node_id, collector.SLOW))
                state.last_slow = now
        return out

    def poll_now(self, node_id=None):
        """Make a node (or every node) due on the next due() call.

        Does not touch backoff: an operator pressing Poll is asking for data,
        not asserting that the node is healthy.
        """
        targets = self._nodes.values() if node_id is None else (
            [self._nodes[node_id]] if node_id in self._nodes else [])
        for state in targets:
            state.last_fast = None
            state.last_slow = None

    def record(self, node_id, any_ok):
        """Record a completed fast cycle. `any_ok` = at least one domain read.

        Any success resets: a node answering at all is reachable, and holding a
        long interval against a box that just answered makes the UI feel dead.
        """
        state = self._nodes.get(node_id)
        if state is None:
            return
        state.failures = 0 if any_ok else state.failures + 1


def setup_logging(path=ctl.LOG_PATH):
    """Size-capped file logging: 1 MB x 2. rootfs is tmpfs -- an unbounded log
    here is RAM, not disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1024 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('managerd')
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    return logger


def check_db_path_is_durable(db_path):
    """Refuse /tmp when the box has a pool to use instead (spec section 4.4).

    store.validate_db_path refuses flash, which is a property of the path alone.
    This one needs a runtime fact - is there a pool at all - so it lives here,
    where the daemon starts, rather than in the pure validator. A box with no
    array up has nowhere better, so /tmp stays tolerated there.
    """
    posix = posixpath.normpath(str(db_path).replace('\\', '/'))
    if posix != '/tmp' and not posix.startswith('/tmp/'):
        return
    if not os.path.isdir(POOL_MARKER):
        return
    raise ValueError(
        'db_path %r is under /tmp and is lost on reboot. This box has a pool - '
        'point db_path at it, e.g. /mnt/user/appdata/unraid-manager' % db_path)


def nchan_endpoint(servers_conf_text):
    """The local socket nginx's nchan publisher listens on, or None.

    Discovered rather than hardcoded: the path is Unraid's, not ours, and a
    version that moves it should cost us live updates, not a crash. The UI's
    30-second fallback poll covers the None case.
    """
    if not servers_conf_text:
        return None
    if 'nchan_publisher' not in servers_conf_text:
        return None
    # The path ends at whitespace OR the semicolon: Unraid's real line is
    # `listen unix:/var/run/nginx.socket default_server;`, and a pattern
    # anchored on `;` matches nothing there. Cost a live box its live
    # updates on Raven while nchan was sitting right in servers.conf.
    match = re.search(r'listen\s+unix:([^\s;]+)', servers_conf_text)
    return match.group(1) if match else None


# nginx reads the channel's buffer length out of the QUERY STRING:
# Unraid's publisher location is `nchan_message_buffer_length $arg_buffer_length`,
# so a POST without one is refused outright with
# `403 missing nchan_message_buffer_length value`. Verified on Raven during the
# P1 exit trial, where every nudge the daemon had ever sent - across two whole
# phases - had been rejected, while the log said "nchan publisher at
# /var/run/nginx.socket" at every startup and the browser fell back to its 30s
# timer. 1: a nudge carries no data, so only the newest one is ever worth
# keeping.
PUBLISH_BUFFER_LENGTH = 1


def _publish_request(message):
    """The exact bytes we POST for one nudge."""
    body = json.dumps(message, separators=(',', ':')).encode('utf-8')
    path = ('/pub/%s?buffer_length=%d' % (NCHAN_CHANNEL, PUBLISH_BUFFER_LENGTH)).encode()
    return (b'POST ' + path + b' HTTP/1.0\r\n'
            b'Content-Type: application/json\r\n'
            b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)


def _publish_check(response):
    """Raise unless nginx said it accepted the message.

    The old code read the reply into a buffer and discarded it, so a refusal
    was indistinguishable from an acceptance and nothing upstream could report
    a dead channel. An unparseable or empty reply is a failure too: not seeing
    an acceptance is not the same as getting one.
    """
    line = response.split(b'\r\n', 1)[0].decode('latin-1', 'replace')
    parts = line.split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise OSError('nchan publish: unreadable reply %r' % line[:80])
    status = int(parts[1])
    if not 200 <= status < 300:
        body = response.partition(b'\r\n\r\n')[2].decode('latin-1', 'replace').strip()
        raise OSError('nchan publish refused: %s (%s)' % (line, body[:80]))


def _publish_over(sock_path):
    """Return a publish_fn that POSTs a delta to the nchan publisher socket."""
    def publish(message):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect(sock_path)
            sock.sendall(_publish_request(message))
            _publish_check(sock.recv(512))
        finally:
            sock.close()
    return publish


class Manager(object):
    """Owns the database connection, the schedule and the worker pool."""

    def __init__(self, conn, cfg, keys_dir=config.KEYS_DIR, post_fn=None, publish_fn=None,
                 exec_fn=None):
        check_db_path_is_durable(cfg.get('db_path'))
        self.conn = conn
        self.cfg = cfg
        self.keys_dir = keys_dir
        self.post_fn = post_fn or gqlclient.post
        # Bound by keyword, not position: exec_agent takes SIX parameters
        # (node, verb, args, timeout, keys_dir, run_fn) but collect_agent calls
        # exec_fn with only the first four. Binding keys_dir by name means a
        # reordering of exec_agent's parameter list cannot silently line up
        # the wrong value with this slot the way a positional partial would.
        self.exec_fn = exec_fn or functools.partial(agentclient.exec_agent, keys_dir=self.keys_dir)
        self.publish_fn = publish_fn
        self.publishing = publish_fn is not None
        self.scheduler = Scheduler(cfg['poll_fast'], cfg['poll_slow'])
        # main() supplies the real pool. None here so that constructing a
        # Manager is complete on its own: tick() is the only user, and a test
        # that calls it without a pool should get a clear AttributeError on
        # None rather than one about a missing attribute.
        self.pool = None
        self.started_at = time.time()
        self._lock = threading.Lock()          # sqlite3 connections are not thread-safe
        self._last_status = {}                 # (node, domain) -> status, for change detection
        self._down = set()                     # nodes already journalled as down

    # Seams the tests replace; production reads flash.
    def _read_manager_cfg(self):
        return config.read_manager_cfg()

    def _read_nodes(self):
        return config.read_nodes_cfg(config.NODES_CFG)

    def _read_key(self, node_id):
        return config.read_key(self.keys_dir, node_id)

    def _node(self, node_id):
        # Called from a worker thread. check_same_thread is off on this
        # connection, so the lock is the only thing keeping a read off a
        # concurrent write - it is not optional.
        with self._lock:
            row = self.conn.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
        return dict(row) if row else None

    # Every threshold health.evaluate() reads. A key missing here is silently
    # dropped on the way in and the evaluator falls back to its own constant -
    # which is how capacity_watch shipped dead for a whole branch: config.py
    # resolved it from Unraid's Disk Settings, the pure function honoured it,
    # and nothing in between passed it along.
    HEALTH_THRESHOLDS = ('capacity_high_water', 'capacity_watch', 'temp_warn',
                         'temp_crit', 'error_window_min')

    # Derived, not restated. capacity_watch was in HEALTH_THRESHOLDS and not in
    # this tuple, so saving it on the settings page stayed inert until a daemon
    # restart - the same silent shape as the bug reload() itself exists to fix,
    # and a second hand-maintained copy of one list is how it happened. There is
    # no threshold the evaluator reads that an operator may not change.
    #
    # db_path is deliberately absent: repointing the database under a running
    # daemon would mean reopening the connection every worker already holds.
    # A db_path change still needs a restart, and the settings page says so.
    RELOADABLE = ('poll_fast', 'poll_slow') + HEALTH_THRESHOLDS

    def reload(self):
        """Re-read both flash files: the registry, and the tunables.

        manager.cfg used to be read once at start, so every value the settings
        page writes - the poll intervals and all four health thresholds - was
        inert until the daemon restarted. Found on Raven 2026-08-27: saving
        temp_warn=38 left a 46 C node reading `ok`, because the evaluator was
        still holding the 50 it booted with. The page reports success, the file
        on flash is correct, and nothing happens; there is no worse shape for a
        settings bug.
        """
        try:
            fresh = self._read_manager_cfg()
        except Exception as exc:                    # a broken flash file
            # Never let an unreadable manager.cfg take the registry reload down
            # with it - the two halves are independent, and a node the operator
            # just enrolled matters more than a tunable they did not change.
            log.warning('reload: manager.cfg unreadable, keeping current settings: %s', exc)
        else:
            changed = {k: fresh[k] for k in self.RELOADABLE
                       if k in fresh and fresh[k] != self.cfg.get(k)}
            self.cfg.update(changed)
            self.scheduler.poll_fast = int(self.cfg['poll_fast'])
            self.scheduler.poll_slow = int(self.cfg['poll_slow'])
            if changed:
                log.info('reload: settings changed: %s',
                         ', '.join('%s=%s' % kv for kv in sorted(changed.items())))
        nodes = self._read_nodes()
        with self._lock:
            result = store.sync_registry(self.conn, nodes)
            for node_id in result['added']:
                store.log_event(self.conn, 'enroll', 'node enrolled', node_id=node_id)
            for node_id in result['removed']:
                store.log_event(self.conn, 'remove', 'node removed from the registry',
                                node_id=node_id)
        self.scheduler.set_nodes([n['id'] for n in nodes if n.get('enabled', True)])
        log.info('reload: +%d ~%d -%d', len(result['added']), len(result['updated']),
                 len(result['removed']))
        return result

    def run_cycle(self, node_id, lane, now):
        """Poll one node's lane. Never raises; that is the contract with the pool."""
        node = self._node(node_id)
        if node is None:
            return {}
        key = self._read_key(node_id)
        target = dict(node, key=key)

        results = []
        for domain in collector.domains_for_lane(lane, node.get('tier', 0)):
            if domain.transport == collector.AGENT:
                results.append(collector.collect_agent(self.exec_fn, target, domain))
                continue
            if key is None:
                results.append(collector.Result(
                    domain.name, 'unknown', None,
                    'no API key on file for this node - re-enter it on the settings page', []))
                continue
            results.append(collector.collect(self.post_fn, target, domain))

        stamp = store.utcnow()
        changed = {}
        with self._lock:
            for result in results:
                store.upsert_state(self.conn, node_id, result.domain, result.status,
                                   payload=result.payload, error=result.error,
                                   fetched_at=stamp if result.status == 'ok' else None)
                if result.samples:
                    store.add_samples(self.conn, node_id, result.samples, ts=stamp)
                if self._last_status.get((node_id, result.domain)) != result.status:
                    changed[result.domain] = result.status
                self._last_status[(node_id, result.domain)] = result.status

            any_ok = any(r.status == 'ok' for r in results)
            if any_ok:
                store.touch_last_seen(self.conn, node_id, ts=stamp)

        if lane == collector.FAST:
            self.scheduler.record(node_id, any_ok=any_ok)
            # One journal row per transition, not one per failed cycle: a node
            # down overnight would otherwise write a thousand identical rows and
            # push everything else past the retention cap.
            if not any_ok and node_id not in self._down:
                self._down.add(node_id)
                with self._lock:
                    store.log_event(self.conn, 'poll_fail',
                                    'node unreadable: %s'
                                    % (results[0].error if results else 'unknown'),
                                    node_id=node_id)
            elif any_ok and node_id in self._down:
                self._down.discard(node_id)
                with self._lock:
                    store.log_event(self.conn, 'poll_ok', 'node readable again', node_id=node_id)
            # Order is load-bearing. _safe_cycle swallows and logs any exception
            # from a cycle, so anything raised by health evaluation would skip
            # whatever follows it. Put it first and one bad evaluation silently
            # disables backoff, the unknown threshold and the event journal for
            # that node - the daemon would keep hammering an unreachable box
            # every 30s with nothing in the log to say why. Health is the least
            # critical of the three; it goes last.
            self._update_health(node_id, results, stamp)

        # `changed` is STATUS transitions only, and on a healthy fleet nothing
        # ever transitions - so publishing on it alone meant the daemon nudged
        # essentially never, and the browser's 30s fallback timer was the whole
        # live-update mechanism from P0 until the P1 exit trial caught it
        # (nginx's `total published messages` did not move across a forced
        # poll on Raven). A cycle that stored anything has fresh data behind
        # it, including a new fetched_at, which is what the card's "last seen"
        # renders. A node that was already failing and still is has produced
        # nothing new, so it still stays quiet.
        if changed or any(result.status == 'ok' for result in results):
            self._publish({'node_id': node_id, 'domains': changed, 'ts': stamp})
        return changed

    # How many slow-lane intervals a retained disk inventory may be behind
    # before thermal stops believing it. Three rather than one: a single missed
    # slow poll is ordinary (Query.disks 504s under load, by design), and
    # dropping to array-only on the first miss would flap an empty-array box
    # between a real temperature and "unknown" every ten minutes.
    INVENTORY_STALE_AFTER = 3

    def _inventory_is_current(self, collected_at, stamp):
        """Is a retained payload recent enough to judge a temperature on?

        No timestamp at all is NOT current: a payload we cannot date is one we
        cannot vouch for, and the safe direction here is to say we do not know
        rather than to warn on a reading of unknown age.
        """
        if not collected_at:
            return False
        try:
            then = datetime.datetime.strptime(collected_at, '%Y-%m-%dT%H:%M:%SZ')
            now = datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')
        except (TypeError, ValueError):
            return False
        limit = self.INVENTORY_STALE_AFTER * int(self.cfg.get('poll_slow') or 600)
        return 0 <= (now - then).total_seconds() <= limit

    def _update_health(self, node_id, results, stamp):
        """Evaluate this cycle's payloads, debounce, and persist. Returns the
        node's overall state.

        Fast lane only: every indicator comes from a fast-lane payload, and
        running this after a slow cycle would evaluate an absent array payload
        and report unknown for a node that is perfectly readable.
        """
        thresholds = {k: self.cfg[k] for k in self.HEALTH_THRESHOLDS if k in self.cfg}
        payloads = {r.domain: r.payload for r in results if r.status == 'ok'}

        window = int(thresholds.get('error_window_min',
                                    health.DEFAULT_THRESHOLDS['error_window_min']))
        cutoff = (datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')
                  - datetime.timedelta(minutes=window)).strftime('%Y-%m-%dT%H:%M:%SZ')

        # ONE lock around the whole read-modify-write. Scheduler.due marks a
        # lane dispatched when it hands it out, not when it finishes, so a fast
        # cycle that outruns its own interval - six domains at a 10s timeout
        # against a 30s interval, on exactly the sick node whose health matters
        # most - can be re-dispatched while the first is still in flight. Two
        # runs would then read the same `previous`, both write pending_count=1,
        # and whichever finished last would stamp an older `updated_at` over the
        # newer row. The evaluation in the middle is pure and takes
        # microseconds, so holding the lock across it costs nothing.
        with self._lock:
            errors_history = store.recent_samples(self.conn, node_id,
                                                  'array.errors_total', cutoff)
            previous = store.read_health(self.conn, node_id)

            # The disk inventory is a SLOW-lane payload, so it is never in this
            # cycle's results - health runs on the fast lane. Without reaching
            # back for the stored one, thermal sees only array-assigned disks,
            # and a box whose disks are all unassigned has no thermal
            # monitoring at all (P1 exit F-4: Raven, eleven disks at 33-40 C,
            # card reading "no disk temperature reported").
            #
            # ...but only while it is still current. upsert_state retains the
            # last good payload across a failed poll, and Golem's disks lane
            # 504s persistently (F-9), so an unbounded reach-back would let a
            # reading from a disk that has since been pulled hold thermal at
            # WARN forever: the proposal never changes, so hysteresis can never
            # clear it, and the failing slow domain contributes nothing to the
            # fast-lane rollup that would otherwise explain why.
            inventory, collected_at = store.read_state(self.conn, node_id, 'disks')
            if inventory is not None and self._inventory_is_current(collected_at, stamp):
                payloads.setdefault('disks', inventory)

            indicators = health.evaluate(payloads, thresholds, errors_history)
            settled = {}
            rows = []
            for name, proposed in indicators.items():
                prior = previous.get(name) or {}
                # A node with no row yet starts from OK, NOT from whatever this
                # poll proposes. Seeding with the proposal makes current ==
                # proposed on the first sighting, which returns immediately and
                # means hysteresis never engages for a newly enrolled node.
                state, pending_state, pending_count = health.apply_hysteresis(
                    prior.get('state') or health.OK, proposed.state,
                    prior.get('pending_state'), prior.get('pending_count') or 0)
                settled[name] = health.Indicator(state, proposed.value, proposed.basis)
                rows.append((name, state, proposed.value, proposed.basis,
                             pending_state, pending_count))

            overall = health.node_overall([r.status for r in results], settled)

            # ONE clock owns `unknown` (P1 triage P2-3). node_overall is pure -
            # it sees a single cycle, so all-domains-failed looks identical on
            # the first failure and the tenth. The scheduler has counted them
            # since P0 and already promised UNKNOWN_AFTER; deriving a second
            # answer here meant a transient greyed a card while the daemon
            # still called the node reachable, and two definitions of one word
            # reaching an operator is how a colour stops being believed.
            #
            # Under the threshold the honest word is `degraded`: we tried, and
            # nothing answered. `record` runs earlier in this same cycle, so
            # the count already includes the failure being judged.
            #
            # The trigger is NOTHING ANSWERED, not the spelling of the failure.
            # node_overall reaches `unknown` only when every status is literally
            # `unknown`, which happens when the connection is REFUSED - and a
            # real Unraid node whose API is stopped does not refuse anything.
            # Something still replies over HTTP with a GraphQL InternalError,
            # gqlclient raises DomainError, and all nine domains land on
            # `error` instead. The rollup then took its any() branch to
            # `degraded` and the node could never go grey however long it
            # stayed dead: ten minutes of it on Raven, still amber, while the
            # scheduler beside it read failures=3, unknown=True (2026-08-29).
            #
            # So the second clock was inside node_overall all along. `error`
            # and `unknown` both mean we got nothing; both count here.
            #
            # is_unknown alone is the whole condition. `record` ran earlier in
            # this cycle from these same results, so failures>=3 already
            # implies this cycle read nothing - an extra `nothing answered`
            # conjunct here cannot ever be the deciding term, and a branch no
            # test can reach is a branch no test can protect. The ordering it
            # leans on is documented as load-bearing where record is called.
            if self.scheduler.is_unknown(node_id):
                overall = 'unknown'
            elif overall == 'unknown':
                overall = 'degraded'

            # Name what dragged the node down, so the UI can say "Degraded -
            # capacity, thermal" instead of just "Degraded". `basis` means WHY
            # everywhere else in this table; the overall row is no exception.
            culprits = sorted(name for name, i in settled.items()
                              if i.state in (health.WATCH, health.WARN))
            blind = sorted(r.domain for r in results if r.status != 'ok')
            why = ', '.join(culprits or blind) or 'all clear'

            for name, state, value, basis, pending_state, pending_count in rows:
                store.upsert_health(self.conn, node_id, name, state, value=value,
                                    basis=basis, pending_state=pending_state,
                                    pending_count=int(pending_count or 0), now=stamp)
            store.upsert_health(self.conn, node_id, 'overall', overall,
                                basis=why, now=stamp)
        return overall

    def _publish(self, message):
        """A ping saying something changed -- never the data itself.

        Payloads go over the authenticated PHP API; nchan carries only the
        nudge that makes the browser ask for them.
        """
        if not self.publishing or self.publish_fn is None:
            return
        try:
            self.publish_fn(message)
        except Exception as exc:                  # noqa: BLE001
            # Logged once, then off until reload. The 30s fallback poll in the
            # browser is the safety net, so a silent nchan degrades refresh
            # rate and nothing else.
            log.warning('nchan publish failed, disabling until reload: %s', exc)
            self.publishing = False

    def tick(self, now):
        """Dispatch everything due. Returns how many jobs were submitted."""
        jobs = self.scheduler.due(now)
        for node_id, lane in jobs:
            self.pool.submit(self._safe_cycle, node_id, lane, now)
        return len(jobs)

    def _safe_cycle(self, node_id, lane, now):
        try:
            self.run_cycle(node_id, lane, now)
        except Exception:                          # noqa: BLE001
            log.exception('cycle failed for %s/%s', node_id, lane)

    def status(self):
        # Answered on the control socket's listener thread.
        with self._lock:
            rows = self.conn.execute(
                'SELECT id,name,last_seen FROM nodes ORDER BY name').fetchall()
        return {
            'uptime': int(time.time() - self.started_at),
            'publishing': self.publishing,
            'nodes': [{'id': r['id'], 'name': r['name'], 'last_seen': r['last_seen'],
                       'failures': self.scheduler.consecutive_failures(r['id']),
                       'interval': self.scheduler.interval(r['id']),
                       'unknown': self.scheduler.is_unknown(r['id'])} for r in rows],
        }

    def _test_node(self, args):
        """Probe a candidate, or re-probe a node that is already enrolled.

        Two forms. {address, port, key} is enrollment: the key was just typed
        into a form, is used for this one probe, and is dropped -- nothing is
        written. {node_id} is the Test button beside an enrolled node: the key
        is read from flash HERE, by the daemon, so the PHP layer never handles
        key material for a node it has already enrolled and a browser never has
        to send one back to get a node re-checked.
        """
        node_id = args.get('node_id')
        if node_id:
            node = self._node(node_id)
            if node is None:
                raise ValueError('no such node: %s' % node_id)
            key = self._read_key(node_id)
            if key is None:
                raise ValueError('no API key on file for this node')
            address, port = node['address'], node['port']
        else:
            address, port, key = args['address'], args['port'], args.get('key')
        return collector.probe(self.post_fn, address, int(port), key)

    def _agent_hello(self, args):
        """Test connection, for enrollment. Runs OUTSIDE the lock: this is an
        ssh round trip that can legitimately take up to 30s, and holding the
        lock across it would stall every other ctl request and worker cycle
        behind one slow or dead peer -- the same reason _test_node does not
        take it either.

        Nothing is persisted on a failed test: there must be no state meaning
        'probably Tier 1' (spec section 4). Only a real reply writes tier=1
        and logs the enrollment.
        """
        node_id = args.get('node_id')
        node = self._node(node_id)
        if node is None:
            raise ValueError('no such node: %s' % node_id)
        try:
            data = self.exec_fn(node, 'agent.hello', {}, 30)
        except Exception as exc:                       # noqa: BLE001
            return {'ok': False, 'error': str(exc)}
        with self._lock:
            store.set_tier(self.conn, node_id, 1)
            store.log_event(self.conn, 'enroll', 'agent verified, node is now Tier 1',
                             node_id=node_id)
        return {'ok': True, 'version': data.get('version'), 'verbs': data.get('verbs')}

    def _prune(self, args):
        # Also the listener thread. A VACUUM can hold this for minutes and will
        # stall the poll loop behind it; that is the right trade for a single
        # writer, and it runs from cron at 04:17 on a Sunday.
        with self._lock:
            return store.prune(self.conn, vacuum=bool(args.get('vacuum')))

    def handlers(self):
        return {
            'status': lambda args: self.status(),
            'reload': lambda args: self.reload(),
            'poll_now': lambda args: {
                'scheduled': self.scheduler.poll_now(args.get('node_id')) or True},
            'prune': self._prune,
            'test_node': self._test_node,
            'agent_hello': self._agent_hello,
        }


def shutdown(manager, conn, exit_fn=os._exit):
    """Stop now, without waiting on a peer that may be 90 seconds from replying.

    A slow-lane `disks` request can legitimately run for the full 90s timeout,
    and shutdown(wait=True) blocks on it - so `rc stop` gave up after 10s and
    reported a failure while the process was still draining, and array stop
    would have waited on the same thing. Observed on Raven.

    What must not be cut short is a write. Taking the lock guarantees no worker
    is mid-transaction; an in-flight HTTP request holds nothing and losing it
    costs one poll. After that the process leaves immediately rather than
    letting ThreadPoolExecutor's non-daemon threads join at interpreter exit.
    """
    log.info('shutting down')
    manager.pool.shutdown(wait=False, cancel_futures=True)
    with manager._lock:
        try:
            store.log_event(conn, 'daemon', 'managerd stopped')
        finally:
            conn.close()
    logging.shutdown()
    try:
        os.unlink(ctl.PID_PATH)
    except OSError:
        pass
    exit_fn(0)


def main(argv=None):
    setup_logging()
    cfg = config.read_manager_cfg()
    try:
        conn = store.connect(cfg['db_path'])
    except ValueError as exc:
        log.error('refusing to start: %s', exc)
        print('unraid-manager: %s' % exc)
        return 2

    try:
        manager = Manager(conn, cfg)
    except ValueError as exc:
        # The /tmp guard, which needs a live filesystem and so cannot live in
        # store.validate_db_path with the flash check.
        log.error('refusing to start: %s', exc)
        print('unraid-manager: %s' % exc)
        conn.close()
        return 2

    endpoint = None
    try:
        with open('/etc/nginx/conf.d/servers.conf', 'r', encoding='utf-8') as fh:
            endpoint = nchan_endpoint(fh.read())
    except OSError:
        pass
    if endpoint:
        manager.publish_fn = _publish_over(endpoint)
        manager.publishing = True
        log.info('nchan publisher at %s', endpoint)
    else:
        log.info('no nchan publisher found; the UI will fall back to polling')

    manager.pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    manager.reload()
    store.log_event(conn, 'daemon', 'managerd started')

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())

    listener = threading.Thread(
        target=ctl.serve, args=(ctl.SOCKET_PATH, manager.handlers(), stop), daemon=True)
    listener.start()

    os.makedirs(ctl.RUN_DIR, exist_ok=True)
    with open(ctl.PID_PATH, 'w', encoding='utf-8') as fh:
        fh.write(str(os.getpid()))
    # Unraid runs with umask 000, so this lands world-writable by default -
    # observed on Raven. The rc script feeds this number to `kill -TERM` as
    # root, so anyone who can rewrite it picks the victim.
    os.chmod(ctl.PID_PATH, 0o644)

    try:
        while not stop.is_set():
            manager.tick(time.time())
            stop.wait(1.0)
    finally:
        shutdown(manager, conn)
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
