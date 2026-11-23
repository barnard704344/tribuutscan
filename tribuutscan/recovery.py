"""Bounded scheduling for wide tracking recovery searches (not the normal solve)."""
import math


class SearchBudget:
    """Run one available wide search per interval, rotating fairly between methods.

    Use acquisition time for replayable behaviour; frame count supplies a bounded
    cadence when timestamps are absent. Reset on a new piece, never on a failed try.
    """
    def __init__(self, interval=.20):
        self.interval = interval
        self.last = None
        self.next_method = 0

    def choose(self, stamp, frame, available):
        now = float(stamp) if stamp is not None and math.isfinite(stamp) else frame / 30.
        if self.last is not None and 0 <= now - self.last < self.interval - 1e-9:
            return None
        methods = ('object features', 'scene features', 'shape')
        for offset in range(len(methods)):
            index = (self.next_method + offset) % len(methods)
            if methods[index] in available:
                self.last = now
                self.next_method = (index + 1) % len(methods)
                return methods[index]
        return None
