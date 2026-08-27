"""One poll's payloads, turned into verdicts an operator would act on.

Pure by construction: no I/O, no clock, no database. Every indicator is a
function of a payload and a threshold dict, which is what lets the whole set be
tested against the captured fixtures rather than against a live box.

`unknown` is not a severity. It means we could not judge, and it is excluded
from the worst-of rather than counted as bad - a box that reports no disk
temperature must not render as unreachable. That distinction was a live defect
once already (p0-exit.md) and it is easy to reintroduce here.
"""
from collections import namedtuple

OK = 'ok'
WATCH = 'watch'
WARN = 'warn'
UNKNOWN = 'unknown'

# Severity order. unknown is deliberately absent: reachability owns it, and
# P0's three-consecutive-failure rule is where it is decided.
LADDER = (OK, WATCH, WARN)

Indicator = namedtuple('Indicator', 'state value basis')

DEFAULT_THRESHOLDS = {
    'capacity_high_water': 90,   # percent used at which capacity is a warning
    'temp_warn': 50,             # degrees C at which thermal is a watch
    'temp_crit': 60,             # degrees C at which thermal is a warning
    'error_window_min': 15,      # minutes of history disk_errors looks back over
}

# How far below the high-water mark the watch band starts.
WATCH_BAND = 10


def _thresholds(overrides):
    merged = dict(DEFAULT_THRESHOLDS)
    merged.update(overrides or {})
    return merged


def evaluate_array_state(array):
    state = (array or {}).get('state')
    if state is None:
        return Indicator(UNKNOWN, None, 'array state not reported')
    if state == 'STARTED':
        return Indicator(OK, None, 'array started')
    if state == 'STOPPED':
        # As often a deliberate operator action as a fault. Visible, not alarming.
        return Indicator(WATCH, None, 'array stopped')
    return Indicator(WARN, None, 'array state is %s' % state)


def evaluate_capacity(array, thresholds):
    # Absence and emptiness are different facts. A blind array domain must not
    # borrow the empty array's clean bill of health - fail closed.
    if not array or 'capacity' not in array:
        return Indicator(UNKNOWN, None, 'no array capacity reported')

    capacity = array.get('capacity') or {}
    total = capacity.get('total') or 0
    used = capacity.get('used') or 0
    if not total:
        # Constraint 3: an array that REPORTED zero is an empty array, and it is
        # healthy. 0/0 is not 100%.
        return Indicator(OK, None, 'array is empty')

    pct = round(used * 100.0 / total, 1)
    high = float(thresholds['capacity_high_water'])
    if pct >= high:
        return Indicator(WARN, pct, '%g%% used, high-water mark is %g%%' % (pct, high))
    if pct >= high - WATCH_BAND:
        return Indicator(WATCH, pct, '%g%% used, approaching %g%%' % (pct, high))
    return Indicator(OK, pct, '%g%% used' % pct)


def evaluate_thermal(array, thresholds):
    temp = (array or {}).get('temp_max')
    if temp is None:
        return Indicator(UNKNOWN, None, 'no disk temperature reported')
    crit = float(thresholds['temp_crit'])
    warn = float(thresholds['temp_warn'])
    if temp >= crit:
        return Indicator(WARN, temp, 'hottest disk %g C, critical at %g C' % (temp, crit))
    if temp >= warn:
        return Indicator(WATCH, temp, 'hottest disk %g C, warm at %g C' % (temp, warn))
    return Indicator(OK, temp, 'hottest disk %g C' % temp)


def evaluate_disk_errors(history):
    """`history` is [(ts, value), ...] ascending, already limited to the window.

    A WINDOW, not a since-last-sample delta. A one-off jump seen only in the
    poll it happened in could never survive two-sample escalation, so it would
    be counted and then lost. Judging the whole window keeps it visible long
    enough to be escalated and to clear on its own.
    """
    rows = list(history)
    if len(rows) < 2:
        return Indicator(UNKNOWN, None, 'not enough history yet')
    first, last = rows[0][1], rows[-1][1]
    if last > first:
        return Indicator(WARN, last - first,
                         '%g new disk error(s) in the window' % (last - first))
    # last < first means a peer rebooted and its counters reset; that is not an
    # error, and a flat counter is not one however large it is.
    return Indicator(OK, last, 'no new disk errors in the window')


def evaluate(payloads, thresholds=None, errors_history=()):
    """Every indicator for one node, from one poll's payloads."""
    limits = _thresholds(thresholds)
    array = (payloads or {}).get('array')
    return {
        'array_state': evaluate_array_state(array),
        'capacity': evaluate_capacity(array, limits),
        'thermal': evaluate_thermal(array, limits),
        'disk_errors': evaluate_disk_errors(errors_history),
    }
