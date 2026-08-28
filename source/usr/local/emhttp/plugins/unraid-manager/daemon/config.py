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

DYNAMIX_CFG = '/boot/config/plugins/dynamix/dynamix.cfg'

# Unraid's own Settings -> Disk Settings, which the operator has already filled
# in, mapped onto ours. P1 exit finding F-8: shipping unrelated constants meant
# two answers on one box to "how hot is too hot" - Raven said 45/55 in Unraid
# and this daemon said 50/60, so a disk at 47 C was warm to Unraid and fine to
# us.
#
# Read from the MANAGER's flash, and applied to every node: a peer's own disk
# settings live on the peer and Tier 0 exposes no way to ask for them. That is
# a fleet default taken from one box, not each box's own preference, and the
# settings page says so.
#
# hotssd/maxssd (60/70) are deliberately not read: telling an SSD from a
# spinner needs a rotational flag the physical enumeration does not carry, and
# guessing from a model string would be worse than one conservative threshold.
UNRAID_THRESHOLD_KEYS = {'hot': 'temp_warn', 'max': 'temp_crit',
                         'warning': 'capacity_watch', 'critical': 'capacity_high_water'}

MANAGER_DEFAULTS = {'db_path': '', 'poll_fast': 30, 'poll_slow': 600,
                    # capacity_watch is the band below the high-water mark.
                    # Unraid has both numbers; before F-8 this was a fixed
                    # ten-point band under health.WATCH_BAND.
                    'capacity_watch': 80,
                    # Health thresholds. Duplicated from health.DEFAULT_THRESHOLDS
                    # rather than imported: this module is the flash-config reader
                    # and must not depend on the evaluator. test_config.py asserts
                    # the two agree.
                    'capacity_high_water': 90, 'temp_warn': 50, 'temp_crit': 60,
                    'error_window_min': 15}

# key -> (minimum, maximum), inclusive. Outside the range is nonsense and falls
# back rather than producing an indicator that can never fire.
THRESHOLD_BOUNDS = {'capacity_high_water': (50, 99), 'capacity_watch': (10, 98),
                    'temp_warn': (20, 99), 'temp_crit': (20, 99),
                    'error_window_min': (1, 1440)}


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


def read_unraid_thresholds(path=DYNAMIX_CFG):
    """Unraid's own disk thresholds, as our keys. Missing file -> {}.

    Never raises and never partially applies nonsense: a value that is not an
    integer in range is dropped, leaving our own default in place. This file is
    written by another plugin's settings page and is not ours to trust blindly.
    """
    out = {}
    fahrenheit = False
    for section, key, value in _pairs(path):
        # [display] only, which is the section common.php reads. Without this
        # a `warning=` in any other section of a file neither half owns would
        # diverge the two readers by file order.
        if section != 'display':
            continue
        if key == 'unit':
            fahrenheit = value.strip().upper().startswith('F')
            continue
        ours = UNRAID_THRESHOLD_KEYS.get(key)
        if ours is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        out[ours] = number

    # Unraid stores its disk temperatures in whatever unit its Date & Time
    # page names, so on an F-configured box `hot="113"` means 45 C. Without
    # this it fails the 20-99 bound and is silently discarded, and "follow
    # Unraid's Disk Settings" quietly does not - which is worse than not
    # offering to follow them at all.
    if fahrenheit:
        for key in ('temp_warn', 'temp_crit'):
            if key in out:
                out[key] = int(round((out[key] - 32) * 5.0 / 9.0))

    # Range-checked LAST, after any conversion. Another plugin's file is not
    # ours to trust: a value outside the band leaves our own default in place
    # rather than producing a threshold that can never fire.
    return {k: v for k, v in out.items()
            if THRESHOLD_BOUNDS[k][0] <= v <= THRESHOLD_BOUNDS[k][1]}


def read_manager_cfg(path=MANAGER_CFG, dynamix_path=DYNAMIX_CFG):
    """Our settings, over Unraid's disk settings, over our own constants.

    An operator who has filled in Settings -> Disk Settings has already said
    how hot is too hot; duplicating that judgement in a second place is what
    F-8 named. A value set explicitly on OUR settings page still wins - it is
    the fleet override for a manager whose own box is not representative.
    """
    defaults = dict(MANAGER_DEFAULTS)
    defaults.update(read_unraid_thresholds(dynamix_path))
    cfg = dict(defaults)
    for _section, key, value in _pairs(path):
        if key == 'db_path':
            cfg['db_path'] = value
        elif key in ('poll_fast', 'poll_slow'):
            cfg[key] = _as_int(value, defaults[key])
        elif key in THRESHOLD_BOUNDS:
            # A BLANK value means "inherit": the settings page writes every key
            # on every save, so an empty one is the only way an operator can say
            # "use Unraid's" after having once typed a number of their own.
            cfg[key] = _as_int(value, defaults[key])
    if cfg['poll_fast'] < 5:
        cfg['poll_fast'] = defaults['poll_fast']
    if cfg['poll_slow'] < cfg['poll_fast']:
        cfg['poll_slow'] = defaults['poll_slow']
    for key, (low, high) in THRESHOLD_BOUNDS.items():
        if not low <= cfg[key] <= high:
            cfg[key] = defaults[key]
    # Both guards fall back to OUR OWN constants, never to `defaults`:
    # `defaults` already carries Unraid's values, so an inversion that came
    # from dynamix.cfg would be "refused" by restoring the very pair that was
    # inverted. A nonsensical pair from another plugin's file is exactly as
    # unusable as one typed into ours.
    if cfg['temp_crit'] <= cfg['temp_warn']:
        # An inverted pair makes one of the two bands unreachable. Refuse the
        # pair rather than silently keeping half of a nonsensical setting.
        cfg['temp_warn'] = MANAGER_DEFAULTS['temp_warn']
        cfg['temp_crit'] = MANAGER_DEFAULTS['temp_crit']
    if cfg['capacity_watch'] >= cfg['capacity_high_water']:
        # Same rule for the capacity pair: a watch band at or above the
        # high-water mark can never be the one that fires.
        cfg['capacity_watch'] = MANAGER_DEFAULTS['capacity_watch']
        cfg['capacity_high_water'] = MANAGER_DEFAULTS['capacity_high_water']
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
