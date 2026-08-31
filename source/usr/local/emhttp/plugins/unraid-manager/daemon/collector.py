"""What we ask each peer, and what we keep from the answer.

One domain is one GraphQL request, always. A batched query that includes a
resolver the box cannot satisfy returns data:null for EVERYTHING -- observed
live, with upsDevices on a box that has no UPS. So a domain failing costs that
domain and nothing else (constraint 1).

Query text here is identical to scripts/capture_fixtures.py, and every field
name is checked against docs/verification/graphql-schema-raven.json.
"""
import json
from collections import namedtuple

import agentclient
import gqlclient

FAST = 'fast'
SLOW = 'slow'
FAST_TIMEOUT = 10
SLOW_TIMEOUT = 90

KB = 1024

GRAPHQL = 'graphql'
AGENT = 'agent'

Domain = namedtuple('Domain', 'name lane query timeout parse transport min_tier')
Result = namedtuple('Result', 'domain status payload error samples')


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# -- parsers -----------------------------------------------------------------

def parse_info(data):
    info = data.get('info') or {}
    os_ = info.get('os') or {}
    core = ((info.get('versions') or {}).get('core')) or {}
    pkgs = ((info.get('versions') or {}).get('packages')) or {}
    return {
        'hostname': os_.get('hostname'),
        'release': os_.get('release'),
        # InfoOs.uptime is a boot TIMESTAMP, not a duration (verification
        # finding 5). Naming it uptime is how a UI ends up printing "2026" in
        # a Uptime column.
        'booted_at': os_.get('uptime'),
        'unraid': core.get('unraid') or os_.get('release'),
        'api': core.get('api'),
        'kernel': core.get('kernel') or os_.get('kernel'),
        # InfoVersions.packages, field names read off Raven's own schema on
        # 2026-08-27 rather than guessed - guessing one is what made the API
        # answer an entire query with INTERNAL_SERVER_ERROR in P0. The type
        # also carries openssl/node/npm/pm2/git/nginx; only the two the design
        # spec asks for are requested. pm2 came back as an empty string on a
        # real box, so an empty version is normalised to None here and reads as
        # "not reported" rather than as a value every node agrees on.
        'php': pkgs.get('php') or None,
        'docker': pkgs.get('docker') or None,
    }


def parse_array(data):
    # P1 triage P2-1: `data.get('array') or {}` turned an ABSENT array into an
    # EMPTY one - capacity {0,0,0}, empty=True - so a box that told us nothing
    # rendered "array is empty" and a healthy capacity indicator. Raven really
    # is empty, which is what made the two indistinguishable and the bug
    # invisible: the same absent-vs-empty family as the stale banner (F-1) and
    # the thermal blind spot (F-4), both of which were live on real hardware.
    #
    # Nulls, not zeros, for everything the array would have told us. Every
    # consumer already handles them: evaluate_capacity returns UNKNOWN for a
    # null capacity, evaluate_array_state for a null state, NodeCard has a
    # "capacity unknown" branch, and _samples_for skips a null rather than
    # recording a zero that would read as a counter reset.
    if data.get('array') is None:
        return {'state': None, 'empty': None, 'capacity': None,
                'slots': {'free': None, 'used': None, 'total': None},
                'disk_count': None, 'disks': [], 'parity_count': None,
                'errors_total': None, 'temp_max': None, 'pools': [],
                'parity_check_status': None}

    array = data.get('array') or {}
    kb = ((array.get('capacity') or {}).get('kilobytes')) or {}
    slots = ((array.get('capacity') or {}).get('disks')) or {}
    disks = array.get('disks') or []
    parities = array.get('parities') or []

    capacity = {'free': _int(kb.get('free')) * KB,
                'used': _int(kb.get('used')) * KB,
                'total': _int(kb.get('total')) * KB}

    # Constraint 3: all-zero capacity with no used slots is an EMPTY array, and
    # an empty array is a healthy array. Raven is in exactly this state.
    empty = (capacity['total'] == 0 and capacity['used'] == 0
             and _int(slots.get('used')) == 0 and not disks)

    temps = [d.get('temp') for d in list(disks) + list(parities)
             if isinstance(d.get('temp'), (int, float))]

    # Finding 6: a multi-device pool appears as sibling entries (cache,
    # cache2), and only the primary carries fsType/fsSize. Counting the
    # siblings as pools doubles the pool count and adds nulls to capacity.
    pools = []
    for cache in array.get('caches') or []:
        if cache.get('fsType'):
            pools.append({'name': cache.get('name'),
                          'fs_type': cache.get('fsType'),
                          'size': _int(cache.get('fsSize')) * KB,
                          'free': _int(cache.get('fsFree')) * KB,
                          'used': _int(cache.get('fsUsed')) * KB,
                          'members': 1})
        elif pools:
            pools[-1]['members'] += 1

    # Slim per-disk rows. array.disks is the ONLY source of slot and error
    # counters - Query.disks enumerates hardware and knows neither - and the
    # fleet disk table joins the two on device name. Kept to six fields
    # because this payload is stored as JSON on every poll.
    # Parities are included: errors_total below sums disks AND parities, so
    # leaving parity drives out would drive an indicator from a drive that has
    # no row in the table. `size` is multiplied like every other kilobyte value
    # in this function - the physical disks payload reports bytes, and two
    # merged payloads carrying a key named `size` in different units is a trap.
    disk_rows = [{'slot': d.get('name'), 'device': d.get('device'),
                  'temp': d.get('temp'), 'numErrors': _int(d.get('numErrors')),
                  'status': d.get('status'), 'size': _int(d.get('size')) * KB}
                 for d in list(disks) + list(parities)]

    return {
        'state': array.get('state'),
        'empty': empty,
        'capacity': capacity,
        'slots': {'free': _int(slots.get('free')), 'used': _int(slots.get('used')),
                  'total': _int(slots.get('total'))},
        'disk_count': len(disks),
        'disks': disk_rows,
        'parity_count': len(parities),
        'errors_total': sum(_int(d.get('numErrors')) for d in list(disks) + list(parities)),
        'temp_max': max(temps) if temps else None,
        'pools': pools,
        'parity_check_status': array.get('parityCheckStatus'),
    }


def parse_shares(data):
    shares = data.get('shares') or []
    return {'count': len(shares),
            'shares': [{'name': s.get('name'),
                        'size': _int(s.get('size')) * KB,
                        'used': _int(s.get('used')) * KB,
                        'free': _int(s.get('free')) * KB} for s in shares]}


def parse_notifications(data):
    # P1 triage P2-4. `or {}` turned "the API reported no unread block" into
    # "zero of everything", so the daemon erased the unheard-vs-zero
    # distinction UPSTREAM of a UI that carefully honours it: NodeCard has a
    # branch for unread === null, tests/js/node_card.mjs pins that null and
    # {0,0,0} render differently, and health.php passes null through - all of
    # which could only ever have fired for a node with no notifications row at
    # all, never for one whose query came back empty.
    #
    # Same shape as the absent-vs-empty array one function up: the honest
    # answer to "we were told nothing" is null, and every consumer of this
    # payload already knew what to do with one.
    unread = ((data.get('notifications') or {}).get('overview') or {}).get('unread')
    if unread is None:
        return {'unread': None}
    return {'unread': {k: _int(unread.get(k)) for k in ('info', 'warning', 'alert', 'total')}}


def parse_metrics(data):
    metrics = data.get('metrics') or {}
    cpu = metrics.get('cpu') or {}
    mem = metrics.get('memory') or {}
    return {'cpu_percent': _float(cpu.get('percentTotal')),
            'mem_percent': _float(mem.get('percentTotal')),
            'mem_total': _int(mem.get('total')),
            'mem_used': _int(mem.get('used'))}


def parse_parity(data):
    # CORRECTED 2026-08-26 against both live boxes -- see docs/verification/
    # tier0-coverage.md items 11-13. Three things the original draft got wrong:
    #
    # 1. A parityHistory row ALWAYS has correcting/paused/running == None (0 of
    #    358 rows non-null across both boxes). bool(None) is False, which would
    #    have reported "not running" as a fact the box never stated, and would
    #    have made `running` permanently False no matter what the array was
    #    doing. Live running/paused state lives on array.parityCheckStatus, NOT
    #    in history -- the collector's array domain carries it. Do not synthesise
    #    it here; pass None through and let the caller read the array domain.
    # 2. A history row has progress == None and errors non-null; an idle
    #    array.parityCheckStatus is the exact reverse. Same GraphQL type,
    #    opposite null pattern. Never share a parser between the two.
    # 3. speed is a free-form string with at least four incompatible observed
    #    formats ("136726495", "88.3 MB/s", "0", "nanB/s"). float() raises on
    #    two of them. It stays an opaque string here.
    history = data.get('parityHistory') or []
    latest = history[0] if history else None
    return {
        'last': None if latest is None else {
            'date': latest.get('date'), 'status': latest.get('status'),
            # None when the field was not returned - unknown, not "no errors".
            'errors': None if latest.get('errors') is None else _int(latest.get('errors')),
            'duration': _int(latest.get('duration')),
            'speed': latest.get('speed'), 'correcting': latest.get('correcting')},
        'running': None,
        'paused': None,
        'runs': len(history),
    }


# -- sample extraction -------------------------------------------------------

def _samples_for(domain_name, payload):
    """Numeric series worth keeping, as (metric, value) pairs."""
    if domain_name == 'array':
        # An array the API did not report has no numbers to keep. Recording
        # zeros for it would be worse than recording nothing: disk_errors
        # judges CHANGE over a window, so a zero row among real counts reads
        # as a counter reset - the peer-rebooted case - and silently forgives
        # every error that came before it (P1 triage P2-1).
        if payload['capacity'] is None:
            return []
        rows = [('array.bytes_used', payload['capacity']['used']),
                ('array.bytes_total', payload['capacity']['total'])]
        # health.evaluate_disk_errors has no history without this.
        if payload['errors_total'] is not None:
            rows.append(('array.errors_total', payload['errors_total']))
        if payload['temp_max'] is not None:
            rows.append(('array.temp_max', payload['temp_max']))
        for pool in payload['pools']:
            rows.append(('pool.%s.bytes_used' % pool['name'], pool['used']))
            rows.append(('pool.%s.bytes_total' % pool['name'], pool['size']))
        return rows
    if domain_name == 'metrics':
        return [(m, v) for m, v in (('cpu.percent', payload['cpu_percent']),
                                    ('mem.percent', payload['mem_percent'])) if v is not None]
    return []


# -- the domain table --------------------------------------------------------

def _domain(name, lane, query, parse, transport=GRAPHQL, min_tier=0):
    # `query` carries the GraphQL document for a graphql domain and the VERB
    # NAME for an agent domain. One field, because everything downstream - the
    # no-mutation assertion included - only ever asks "what did we send".
    return Domain(name, lane, query, FAST_TIMEOUT if lane == FAST else SLOW_TIMEOUT,
                  parse, transport, min_tier)


DOMAINS = {}
for _d in [
    _domain('info', FAST,
            '{ info { os { hostname release kernel uptime } '
            'versions { core { unraid api kernel } packages { php docker } } } }',
            parse_info),
    _domain('array', FAST,
            '{ array { state capacity { kilobytes { free used total } disks { free used total } } '
            'parityCheckStatus { status progress errors correcting paused running } '
            'parities { idx name device size status temp numErrors } '
            'disks { idx name device size status temp numErrors fsType fsSize fsFree fsUsed } '
            'caches { name fsType fsSize fsFree fsUsed } } }', parse_array),
    _domain('shares', FAST, '{ shares { name free used size floor } }', parse_shares),
    _domain('notifications', FAST,
            '{ notifications { overview { unread { info warning alert total } } } }',
            parse_notifications),
    _domain('metrics', FAST,
            '{ metrics { cpu { percentTotal } memory { total used free percentTotal } } }',
            parse_metrics),
    _domain('parity', FAST,
            # `errors` is deliberately NOT requested. It is typed Int (32-bit)
            # and Golem holds a history row reading 2441379360, which the API
            # cannot serialise: it answers the whole query with "Int cannot
            # represent non 32-bit signed integer value" and one bad row from
            # 2024 costs the parity domain forever. parityHistory takes no
            # arguments, so there is no way to ask for only the newest row.
            # Observed live 2026-08-26; the parser still reads the field where a
            # box does return it.
            # ponytail: field dropped to dodge an API-side Int32 overflow.
            # Revisit if Unraid widens the type.
            '{ parityHistory { date duration speed status progress '
            'correcting paused running } }', parse_parity),
]:
    DOMAINS[_d.name] = _d


def domains_for_lane(lane, tier=0):
    """The domains this lane runs against a node at this tier.

    A Tier 0 node simply has fewer domains. That IS the plan's 'degrade
    gracefully to Tier 0' rule - satisfied structurally, rather than by a check
    inside each feature that someone will forget to write.
    """
    return [d for d in DOMAINS.values()
            if d.lane == lane and d.min_tier <= int(tier or 0)]


def collect(post_fn, node, domain):
    """Run one domain against one node. Never raises.

    Fail closed (constraint 5): if we could not READ it, the domain is
    `unknown`. If the box answered and the resolver failed, that is an `error` --
    a different fact, and the operator acts on it differently.
    """
    key = node.get('key')
    try:
        data = post_fn(node['address'], node['port'], key, domain.query, domain.timeout)
    except gqlclient.AuthError as exc:
        return Result(domain.name, 'unknown', None, gqlclient.scrub(str(exc), key), [])
    except gqlclient.TransportError as exc:
        return Result(domain.name, 'unknown', None, gqlclient.scrub(str(exc), key), [])
    except gqlclient.DomainError as exc:
        return Result(domain.name, 'error', None, gqlclient.scrub(str(exc), key), [])

    try:
        payload = domain.parse(data)
        samples = _samples_for(domain.name, payload)
    except Exception as exc:                      # noqa: BLE001 - a shape we did not expect
        # A box answering in a shape we do not understand is an error on that
        # domain. It must never take the poll loop down with it.
        return Result(domain.name, 'error', None,
                      gqlclient.scrub('could not read the %s response: %s: %s'
                                      % (domain.name, type(exc).__name__, exc), key), [])
    return Result(domain.name, 'ok', payload, None, samples)


# -- slow lane ---------------------------------------------------------------

def _disk_row(disk):
    # serialNum is deliberately dropped: plan section 12 forbids a raw serial in
    # any API-bound payload, and nothing in P0 needs one.
    return {'name': disk.get('name'), 'device': disk.get('device'),
            'vendor': disk.get('vendor'), 'size': _int(disk.get('size')),
            'temp': _float(disk.get('temperature')),
            'smart_status': disk.get('smartStatus'),
            'interface': disk.get('interfaceType')}


def parse_disks(data):
    disks = data.get('disks') or []
    spares = data.get('assignableDisks') or []
    return {'count': len(disks), 'spare_count': len(spares),
            'disks': [_disk_row(d) for d in disks],
            'spares': [_disk_row(d) for d in spares]}


def parse_plugins(data):
    # M10 trap: this is the .plg list and carries NO versions at Tier 0.
    # Query.plugins is a different thing entirely (API plugins) - not asked for.
    plugins = [p for p in (data.get('installedUnraidPlugins') or []) if p]
    return {'count': len(plugins), 'plugins': sorted(plugins)}


def parse_logfiles(data):
    files = data.get('logFiles') or []
    return {'count': len(files),
            'files': [{'name': f.get('name'), 'path': f.get('path'),
                       'size': _int(f.get('size')), 'modified_at': f.get('modifiedAt')}
                      for f in files]}


for _d in [
    _domain('disks', SLOW,
            '{ disks { device name vendor size temperature smartStatus interfaceType serialNum } '
            'assignableDisks { device name vendor size temperature smartStatus '
            'interfaceType serialNum } }', parse_disks),
    _domain('plugins', SLOW, '{ installedUnraidPlugins }', parse_plugins),
    _domain('logfiles', SLOW, '{ logFiles { name path size modifiedAt } }', parse_logfiles),
]:
    DOMAINS[_d.name] = _d


# -- agent (Tier 1) ------------------------------------------------------

def parse_smart(data):
    """`data` is {device: raw smartctl JSON text, or None/'' if unreadable}.

    A present-but-failing smartctl still exits non-zero on a healthy disk with
    prefail attributes set (its exit status is a bitmask), so the agent can
    send an EMPTY STRING for a disk it read just fine. That is the same "no
    data" fact as None, not a different one, so both collapse to the same
    null entry here rather than one becoming a value and the other vanishing.
    A drive we truly could not read must still keep its key: dropping it would
    make a dead disk look like a disk that was never installed.

    serial_number and logical_unit_id are stripped before this payload goes
    anywhere: plan section 12 forbids a raw serial in an API-bound payload,
    and api/disks.php serves this dict straight to the browser.
    """
    disks = {}
    for device, raw in (data or {}).items():
        if not raw:
            disks[device] = None
            continue
        doc = json.loads(raw)
        doc.pop('serial_number', None)
        doc.pop('logical_unit_id', None)
        disks[device] = doc
    return {'count': len(disks), 'disks': disks}


def collect_agent(exec_fn, node, domain):
    """Run one agent domain against one node. Never raises.

    The same fail-closed rule as collect(): could not READ it is `unknown`,
    the peer answered and failed is `error`. One extra status this path can
    produce - `unsupported` - because an agent older than the manager is a
    version gap, not a fault, and the two must not arrive looking alike.
    """
    try:
        data = exec_fn(node, domain.query, {}, domain.timeout)
    except agentclient.VerbUnsupported:
        return Result(domain.name, 'unsupported', None,
                      'this node needs a newer agent for %s' % domain.query, [])
    except agentclient.AgentRefused as exc:
        return Result(domain.name, 'error', None, str(exc), [])
    except agentclient.AgentUnreachable as exc:
        return Result(domain.name, 'unknown', None, str(exc), [])

    try:
        return Result(domain.name, 'ok', domain.parse(data), None, [])
    except Exception as exc:                       # noqa: BLE001
        return Result(domain.name, 'error', None,
                      'could not read the %s reply: %s: %s'
                      % (domain.name, type(exc).__name__, exc), [])


for _d in [
    _domain('smart', SLOW, 'smart.attributes', parse_smart,
            transport=AGENT, min_tier=1),
]:
    DOMAINS[_d.name] = _d


# -- probe -------------------------------------------------------------------

def probe(post_fn, address, port, key):
    """Run the fast lane once against a candidate and report what it can serve.

    Fast lane only: the probe runs while an operator waits on a form, and the
    slow lane is 90 seconds (constraint 2). It answers the four questions
    enrollment actually needs to separate - can we reach it, is the key good, is
    the key scoped for everything, and is it the box they think it is.
    """
    node = {'address': address, 'port': port, 'key': key}
    results = [collect(post_fn, node, d) for d in domains_for_lane(FAST)]
    by_name = {r.domain: r for r in results}

    payloads = {r.domain: r.payload for r in results if r.status == 'ok'}
    info = payloads.get('info') or {}
    array = payloads.get('array') or {}
    shares = payloads.get('shares') or {}

    ok_count = sum(1 for r in results if r.status == 'ok')
    auth_failures = sum(1 for r in results
                        if r.status == 'unknown' and 'key rejected' in (r.error or '').lower())

    if ok_count == len(results):
        verdict = 'ok'
    elif ok_count:
        verdict = 'partial'
    elif auth_failures:
        verdict = 'bad_key'
    else:
        verdict = 'unreachable'

    return {
        'verdict': verdict,
        'domains': {r.domain: {'status': r.status, 'error': r.error} for r in results},
        'headline': {
            'hostname': info.get('hostname'),
            'unraid': info.get('unraid'),
            'api': info.get('api'),
            'array_state': array.get('state'),
            'array_empty': array.get('empty'),
            'shares': shares.get('count'),
        },
        'ok_domains': ok_count,
        'total_domains': len(results),
        'reachable': bool(by_name and ok_count > 0),
    }
