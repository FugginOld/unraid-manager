"""The manager daemon: scheduling, dispatch, lifecycle.

This module must stay import-safe -- the scheduler is unit-tested by importing
it, so nothing here opens a socket or a database at import time. The entrypoint
lives under `if __name__ == '__main__'`.
"""

import collector

BACKOFF_CAP = 600          # seconds; the slow lane's interval is the ceiling
UNKNOWN_AFTER = 3          # consecutive all-domain failures before a node is unknown


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
