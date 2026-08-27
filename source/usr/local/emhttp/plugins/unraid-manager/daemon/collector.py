"""What we ask each peer, and what we keep from the answer.

One domain is one GraphQL request, always. A batched query that includes a
resolver the box cannot satisfy returns data:null for EVERYTHING -- observed
live, with upsDevices on a box that has no UPS. So a domain failing costs that
domain and nothing else (constraint 1).

Query text here is identical to scripts/capture_fixtures.py, and every field
name is checked against docs/verification/graphql-schema-raven.json.
"""
from collections import namedtuple

import gqlclient

FAST = 'fast'
SLOW = 'slow'
FAST_TIMEOUT = 10
SLOW_TIMEOUT = 90

KB = 1024

Domain = namedtuple('Domain', 'name lane query timeout parse')
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
    }


def parse_array(data):
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
    unread = (((data.get('notifications') or {}).get('overview') or {}).get('unread')) or {}
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
        rows = [('array.bytes_used', payload['capacity']['used']),
                ('array.bytes_total', payload['capacity']['total']),
                # health.evaluate_disk_errors has no history without this.
                ('array.errors_total', payload['errors_total'])]
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

def _domain(name, lane, query, parse):
    return Domain(name, lane, query, FAST_TIMEOUT if lane == FAST else SLOW_TIMEOUT, parse)


DOMAINS = {}
for _d in [
    _domain('info', FAST,
            '{ info { os { hostname release kernel uptime } '
            'versions { core { unraid api kernel } } } }', parse_info),
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


def domains_for_lane(lane):
    return [d for d in DOMAINS.values() if d.lane == lane]


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
