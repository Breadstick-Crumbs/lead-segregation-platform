"""In-process sliding-window rate limiter.

State lives in the API process, which is enough for the one API container this app runs
(see `docs/SECURITY.md#rate-limiting`). Counters reset on restart; an attacker gains one
fresh window per deploy, which is acceptable for brute-force and flood protection."""

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Limit:
    requests: int
    window_seconds: float


class RateLimiter:
    """Counts hits per key within a sliding window. Not thread-safe: it is only used from the
    event loop, and no method awaits."""

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000
    ) -> None:
        self._clock = clock
        self._max_keys = max_keys
        # Least recently hit first, so the oldest key is evicted when the table is full.
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def _window(self, key: str, limit: Limit, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        while hits and hits[0] <= now - limit.window_seconds:
            hits.popleft()
        return hits

    def retry_after(self, key: str, limit: Limit) -> float | None:
        """Seconds until `key` may try again, or None if it is under the limit. Records
        nothing, so callers can count only failures (see `hit`)."""
        now = self._clock()
        hits = self._window(key, limit, now)
        if len(hits) < limit.requests:
            return None
        return max(hits[0] + limit.window_seconds - now, 0.0)

    def hit(self, key: str, limit: Limit) -> float | None:
        """Record one request for `key`. Returns the seconds to wait if it is over the limit
        (the rejected request is not counted), otherwise None."""
        blocked = self.retry_after(key, limit)
        if blocked is not None:
            return blocked
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
            if len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        hits.append(self._clock())
        return None

    def release(self, key: str) -> None:
        """Take back the latest hit for `key`: for a caller that reserves a slot before an
        outcome is known (so parallel requests can't all pass the check) and counts only
        some outcomes."""
        hits = self._hits.get(key)
        if hits:
            hits.pop()

    def reset(self) -> None:
        self._hits.clear()
