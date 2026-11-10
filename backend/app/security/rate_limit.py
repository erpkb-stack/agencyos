"""Fixed-window, in-memory rate limiter (single process).

For multi-process deployments replace with a shared store (e.g. Redis); the interface stays.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record a hit; return False when the key is over its limit for this window."""
        window_id = int(time.time() // self.window)
        with self._lock:
            current_window, count = self._hits.get(key, (window_id, 0))
            if current_window != window_id:
                count = 0
            count += 1
            self._hits[key] = (window_id, count)
            if len(self._hits) > 10_000:  # bound memory
                self._hits = {k: v for k, v in self._hits.items() if v[0] == window_id}
            return count <= self.limit

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
