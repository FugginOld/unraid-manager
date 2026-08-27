"""Readers for the flash-resident configuration.

The flash config is the authoritative node registry: it survives loss of the
pool and of the database. The daemon only ever READS it -- every write is the
PHP layer's, on an explicit user action, because flash is a USB stick with
finite write endurance (plan section 3).

Deliberately hand-rolled rather than configparser: these files are hand-editable
on a stick, and a duplicate section or a stray '%' must degrade to a skipped
node, not to a daemon that will not start.
"""

import os

CFG_DIR = '/boot/config/plugins/unraid-manager'
MANAGER_CFG = CFG_DIR + '/manager.cfg'
NODES_CFG = CFG_DIR + '/nodes.cfg'
KEYS_DIR = CFG_DIR + '/keys'

MANAGER_DEFAULTS = {'db_path': '', 'poll_fast': 30, 'poll_slow': 600,
                    # Health thresholds. Duplicated from health.DEFAULT_THRESHOLDS
                    # rather than imported: this module is the flash-config reader
                    # and must not depend on the evaluator. test_config.py asserts
                    # the two agree.
                    'capacity_high_water': 90, 'temp_warn': 50, 'temp_crit': 60,
                    'error_window_min': 15}

# key -> (minimum, maximum), inclusive. Outside the range is nonsense and falls
# back rather than producing an indicator that can never fire.
THRESHOLD_BOUNDS = {'capacity_high_water': (50, 99), 'temp_warn': (20, 99),
                    'temp_crit': (20, 99), 'error_window_min': (1, 1440)}


def _clean(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        value = value[1:-1]
    return value.strip()


def _pairs(path):
    """Yield (section, key, value). section is None before any [header]."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
    except OSError:
        return
    section = None
    for line in lines:
        line = line.strip()
        if not line or line[0] in '#;':
            continue
        if line[0] == '[' and line.endswith(']'):
            section = line[1:-1].strip()
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        yield section, key.strip(), _clean(value)


def _as_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def read_manager_cfg(path=MANAGER_CFG):
    cfg = dict(MANAGER_DEFAULTS)
    for _section, key, value in _pairs(path):
        if key == 'db_path':
            cfg['db_path'] = value
        elif key in ('poll_fast', 'poll_slow'):
            cfg[key] = _as_int(value, MANAGER_DEFAULTS[key])
        elif key in THRESHOLD_BOUNDS:
            cfg[key] = _as_int(value, MANAGER_DEFAULTS[key])
    if cfg['poll_fast'] < 5:
        cfg['poll_fast'] = MANAGER_DEFAULTS['poll_fast']
    if cfg['poll_slow'] < cfg['poll_fast']:
        cfg['poll_slow'] = MANAGER_DEFAULTS['poll_slow']
    for key, (low, high) in THRESHOLD_BOUNDS.items():
        if not low <= cfg[key] <= high:
            cfg[key] = MANAGER_DEFAULTS[key]
    if cfg['temp_crit'] <= cfg['temp_warn']:
        # An inverted pair makes one of the two bands unreachable. Refuse the
        # pair rather than silently keeping half of a nonsensical setting.
        cfg['temp_warn'] = MANAGER_DEFAULTS['temp_warn']
        cfg['temp_crit'] = MANAGER_DEFAULTS['temp_crit']
    return cfg


def read_nodes_cfg(path=NODES_CFG):
    raw = {}
    for section, key, value in _pairs(path):
        if section is None:
            continue
        raw.setdefault(section, {})[key] = value

    nodes = []
    for node_id, fields in raw.items():
        address = fields.get('address', '').strip()
        if not address:
            continue                      # a section with no address is not a node
        port = _as_int(fields.get('port'), 0)
        if port <= 0 or port > 65535:
            continue                      # unusable; skip rather than crash the daemon
        nodes.append({
            'id': node_id,
            'name': fields.get('name', '').strip() or address,
            'address': address,
            'port': port,
            'tier': _as_int(fields.get('tier'), 0),
            'enabled': _as_int(fields.get('enabled'), 1) != 0,
        })
    nodes.sort(key=lambda n: n['name'].lower())
    return nodes


def read_key(keys_dir, node_id):
    """The node's API key, or None. Never logged, never returned over HTTP."""
    if not node_id or '/' in node_id or '\\' in node_id or node_id.startswith('.'):
        return None
    try:
        with open(os.path.join(keys_dir, node_id + '.key'), 'r', encoding='utf-8') as fh:
            key = fh.read().strip()
    except OSError:
        return None
    return key or None
