from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Hashable


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._buckets: dict[Hashable, list[float]] = defaultdict(list)

    def allow(self, key: Hashable) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        events = self._buckets[key]
        while events and events[0] < cutoff:
            events.pop(0)
        if len(events) >= self.max_events:
            return False
        events.append(now)
        return True
