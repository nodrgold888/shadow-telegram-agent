"""A small in-process sliding-window rate limiter.

No external store is warranted here -- this is a single small bot, not a
high-traffic service -- so an in-process counter is enough to blunt casual
brute-forcing of /dashboard/auth, /setup/telegram/auth, and /admin/status.
Like the session store, this resets on process restart; the threat model is
noisy brute-force attempts within a single process's uptime, not long-term
throttling.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                raise HTTPException(status_code=429, detail="Juda ko‘p urinish. Birozdan so‘ng qayta urinib ko‘ring.")
            hits.append(now)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._hits.clear()


# 5 login attempts/minute is generous for a human typing a token, tight
# enough to blunt brute-forcing a short SETUP_TOKEN.
auth_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=60)
# /admin/status is polled by monitoring/health tooling, not just humans.
admin_status_limiter = SlidingWindowLimiter(max_attempts=20, window_seconds=60)
