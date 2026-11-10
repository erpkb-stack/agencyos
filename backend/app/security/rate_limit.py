"""Sliding-window, in-memory rate limiter (single process).

A sliding window counts hits in the last ``window_seconds`` from now, so a client cannot double its
allowance by straddling a clock-minute boundary (a weakness of fixed windows).
For multi-process deployments replace with a shared store (e.g. Redis); the interface stays.
"""

from __future__ import annotations

import threading
import time
from collections import deque

_MAX_KEYS = 10_000


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record a hit; return False when the key has exceeded its limit within the window."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False  # rejected hits are not recorded, so waiting always helps
            hits.append(now)
            if len(self._hits) > _MAX_KEYS:  # bound memory: drop idle keys
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
            return True

    def exceeded(self, key: str) -> bool:
        """True if ``key`` is at its limit right now (does not record a hit)."""
        cutoff = time.monotonic() - self.window
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return False
            while hits and hits[0] <= cutoff:
                hits.popleft()
            return len(hits) >= self.limit

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
