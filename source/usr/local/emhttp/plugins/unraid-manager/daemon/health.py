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
    'capacity_watch': 80,        # ...and at which it is worth watching
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
    if not array or array.get('capacity') is None:
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
    # Unraid has its own warning/critical utilization pair and the operator has
    # already filled it in (P1 exit F-8), so the watch level is a THRESHOLD now
    # rather than a fixed ten points under the high-water mark. WATCH_BAND
    # remains the fallback for a config that carries only the one number.
    watch = float(thresholds.get('capacity_watch') or (high - WATCH_BAND))
    if pct >= high:
        return Indicator(WARN, pct, '%g%% used, high-water mark is %g%%' % (pct, high))
    if pct >= watch:
        return Indicator(WATCH, pct, '%g%% used, approaching %g%%' % (pct, high))
    return Indicator(OK, pct, '%g%% used' % pct)


def _hottest_physical(disks):
    """The hottest disk in the physical enumeration, or None.

    P1 exit finding F-4: `array.temp_max` covers array-assigned disks and
    parities only. Raven's array is empty, so every one of its eleven disks was
    invisible to this indicator - the card read "no disk temperature reported"
    while the Disks tab displayed 33-40 C for that same box, one tab over. A
    box with no thermal monitoring at all is the failure this indicator exists
    to prevent.
    """
    temps = [d.get('temp') for d in (disks or {}).get('disks') or []
             if isinstance(d.get('temp'), (int, float)) and not isinstance(d.get('temp'), bool)]
    return max(temps) if temps else None


def evaluate_thermal(array, thresholds, disks=None):
    """Hottest disk anywhere on the box, array-assigned or not.

    The MAX of both sources, not a fallback to one: an unassigned disk cooking
    in a bay is exactly as much of a problem as an array one, and taking only
    the array's number is what made Raven blind. The inventory is a slow-lane
    payload and may be up to ten minutes old, so this can hold a warning a
    little past the event - the conservative direction, and the basis says
    where the reading came from.
    """
    from_array = (array or {}).get('temp_max')
    from_disks = _hottest_physical(disks)
    if from_array is None and from_disks is None:
        return Indicator(UNKNOWN, None, 'no disk temperature reported')
    if from_array is None or (from_disks is not None and from_disks > from_array):
        temp, where = from_disks, ' (from the last disk inventory)'
    else:
        temp, where = from_array, ''
    crit = float(thresholds['temp_crit'])
    warn = float(thresholds['temp_warn'])
    if temp >= crit:
        return Indicator(WARN, temp,
                         'hottest disk %g C%s, critical at %g C' % (temp, where, crit))
    if temp >= warn:
        return Indicator(WATCH, temp,
                         'hottest disk %g C%s, warm at %g C' % (temp, where, warn))
    return Indicator(OK, temp, 'hottest disk %g C%s' % (temp, where))


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
    #
    # P1 exit finding F-6: the count is named when there is one. On Raven the
    # card read "OK disk errors - no new disk errors in the window" while the
    # Disks tab showed 192 errors on Golem's disk15, one tab over. Both were
    # true and together they read as a contradiction. The indicator judges
    # CHANGE, deliberately - a stable historical count is not an incident - so
    # it says so rather than leaving the operator to reconcile two screens.
    if last:
        return Indicator(OK, last,
                         'no new disk errors in the window; %g recorded in total' % last)
    return Indicator(OK, last, 'no disk errors recorded')


def evaluate(payloads, thresholds=None, errors_history=()):
    """Every indicator for one node, from one poll's payloads."""
    limits = _thresholds(thresholds)
    array = (payloads or {}).get('array')
    return {
        'array_state': evaluate_array_state(array),
        'capacity': evaluate_capacity(array, limits),
        'thermal': evaluate_thermal(array, limits, (payloads or {}).get('disks')),
        'disk_errors': evaluate_disk_errors(errors_history),
    }


# -- hysteresis ---------------------------------------------------------------

ESCALATE_AFTER = 2      # consecutive agreeing samples to get worse (~60s)
CLEAR_AFTER = 5         # consecutive agreeing samples to get better (~2.5 min)


def apply_hysteresis(current, proposed, pending_state, pending_count,
                     up=ESCALATE_AFTER, down=CLEAR_AFTER):
    """Debounce one indicator. Returns (state, pending_state, pending_count).

    Asymmetric on purpose: an operator should learn quickly that something
    turned bad, and green should not come back until it is convincingly fine.
    A disk sitting exactly on the threshold would otherwise blink every poll.

    unknown is not a severity, so it does not use the ladder - but it is not a
    free pass either. Entering unknown is immediate; leaving it is immediate
    only toward ok. Coming back blind-to-bad still needs confirming, or a
    single bad reading after a blind spell flips the card with no debounce at
    all.
    """
    if proposed == current:
        return current, None, 0

    # ENTERING unknown is immediate. It means this poll could not judge, and
    # continuing to display the previous verdict would assert something we no
    # longer know.
    if proposed == UNKNOWN:
        return proposed, None, 0

    if current == UNKNOWN:
        # LEAVING unknown is asymmetric too. Good news is believed at once - the
        # node answered and it is fine. Bad news still has to be confirmed,
        # exactly as it would from ok, so one bad reading after a blind spell
        # cannot flip the card on its own.
        if proposed == OK:
            return proposed, None, 0
        needed = up
    else:
        needed = up if LADDER.index(proposed) > LADDER.index(current) else down

    if proposed != pending_state:
        pending_state, pending_count = proposed, 0
    pending_count += 1

    if pending_count >= needed:
        return proposed, None, 0
    return current, pending_state, pending_count


def node_overall(domain_statuses, indicators):
    """The chip the fleet table shows: ok | degraded | unknown.

    Worst of two rollups - domain reachability, and indicator health. Only
    reachability can produce `unknown`; an indicator that could not be judged
    is excluded rather than counted as bad.
    """
    statuses = list(domain_statuses)
    if not statuses or all(s == UNKNOWN for s in statuses):
        return 'unknown'
    if any(s in (UNKNOWN, 'error') for s in statuses):
        return 'degraded'
    if any(i.state in (WATCH, WARN) for i in indicators.values()):
        return 'degraded'
    return 'ok'
