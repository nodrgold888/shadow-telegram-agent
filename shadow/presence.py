from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable

from telethon import functions

log = logging.getLogger("shadow.presence")

INTERVAL_SECONDS = 120
JITTER = 0.1


class OnlinePresence:
    """Keeps the connected Telegram account showing as "online".

    Telegram only shows an account as online for a few minutes after the last
    status update, so we repeat it every couple of minutes. This changes what
    contacts see; it never sends or reads messages.
    """

    def __init__(self, enabled: bool, interval: float = INTERVAL_SECONDS) -> None:
        self.enabled = enabled
        self.interval = interval
        self.ok_count = 0
        self.fail_count = 0
        self.last_ok_at: float | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    async def ping(self, client) -> bool:
        """Mark the account online once. Never raises."""
        try:
            await client(functions.account.UpdateStatusRequest(offline=False))
        except Exception as exc:
            self.fail_count += 1
            self.last_error = type(exc).__name__
            return False
        self.ok_count += 1
        self.last_ok_at = time.time()
        self.last_error = None
        return True

    def next_delay(self) -> float:
        return self.interval * random.uniform(1 - JITTER, 1 + JITTER)

    async def run(self, get_client: Callable[[], object | None]) -> None:
        log.info("Always-online presence enabled (every ~%ss)", self.interval)
        while True:
            client = get_client()
            if client is not None and client.is_connected():
                if not await self.ping(client):
                    log.warning("Could not update online status: %s", self.last_error)
            await asyncio.sleep(self.next_delay())

    def start(self, get_client: Callable[[], object | None]) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self.run(get_client))

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval,
            "ok": self.ok_count,
            "failed": self.fail_count,
            "last_ok_age_seconds": None if self.last_ok_at is None else round(time.time() - self.last_ok_at),
            "last_error": self.last_error,
        }
