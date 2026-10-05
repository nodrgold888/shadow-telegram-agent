from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Render's free tier sleeps after ~15 minutes without inbound traffic. Pinging every
# 5 minutes leaves room for two missed pings before the service would sleep.
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
JITTER = 0.1
# A sleeping service needs 30-60s to cold start, so the timeout must be generous.
REQUEST_TIMEOUT_SECONDS = 60
RETRY_DELAYS = (5, 15, 45)
USER_AGENT = "shadow-keepalive/2"


def keepalive_url() -> str:
    """Public /ping URL to call, or "" when keep-alive is disabled."""
    explicit = os.getenv("KEEPALIVE_URL", "").strip()
    if explicit:
        return explicit
    base = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    return f"{base}/ping" if base else ""


def keepalive_interval() -> int:
    try:
        raw = int(os.getenv("KEEPALIVE_INTERVAL_SECONDS", "") or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, raw)


def _ping(url: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read(1)
        return response.status


@dataclass
class KeepAlive:
    """Pings our own public URL so the free Render service does not idle-sleep.

    It only runs while the process is alive; the GitHub Actions workflow is the
    independent outside pinger that can wake a service that is already asleep.
    """

    url: str
    interval: int = DEFAULT_INTERVAL_SECONDS
    retry_delays: tuple[float, ...] = RETRY_DELAYS
    timeout: float = REQUEST_TIMEOUT_SECONDS
    ok_count: int = 0
    fail_count: int = 0
    consecutive_failures: int = 0
    last_ok_at: float | None = None
    last_error: str | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)

    async def ping_once(self) -> bool:
        """One ping with short retries. Never raises."""
        attempts = len(self.retry_delays) + 1
        for attempt in range(attempts):
            try:
                await asyncio.to_thread(_ping, self.url, self.timeout)
            except Exception as exc:
                self.last_error = type(exc).__name__
                if attempt < attempts - 1:
                    await asyncio.sleep(self.retry_delays[attempt])
                continue
            self.ok_count += 1
            self.consecutive_failures = 0
            self.last_ok_at = time.time()
            self.last_error = None
            return True
        self.fail_count += 1
        self.consecutive_failures += 1
        return False

    def next_delay(self) -> float:
        return self.interval * random.uniform(1 - JITTER, 1 + JITTER)

    async def run(self) -> None:
        logger.info("Keep-alive ping enabled: %s every ~%ss", self.url, self.interval)
        # Ping straight away so a fresh deploy is confirmed reachable from outside.
        await asyncio.sleep(10)
        while True:
            if not await self.ping_once():
                # Log loudly only when it keeps failing, not on a single blip.
                level = logging.ERROR if self.consecutive_failures >= 3 else logging.WARNING
                logger.log(level, "Keep-alive ping failed (%s), %s in a row", self.last_error, self.consecutive_failures)
            await asyncio.sleep(self.next_delay())

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "url": self.url,
            "interval_seconds": self.interval,
            "ok": self.ok_count,
            "failed": self.fail_count,
            "consecutive_failures": self.consecutive_failures,
            "last_ok_age_seconds": None if self.last_ok_at is None else round(time.time() - self.last_ok_at),
            "last_error": self.last_error,
        }


def build_keepalive() -> KeepAlive | None:
    url = keepalive_url()
    return KeepAlive(url, keepalive_interval()) if url else None
