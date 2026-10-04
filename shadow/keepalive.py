from __future__ import annotations

import asyncio
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 600


def keepalive_url() -> str:
    """Public /healthz URL to ping, or "" when keep-alive is disabled."""
    explicit = os.getenv("KEEPALIVE_URL", "").strip()
    if explicit:
        return explicit
    base = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    return f"{base}/healthz" if base else ""


def keepalive_interval() -> int:
    try:
        return max(60, int(os.getenv("KEEPALIVE_INTERVAL_SECONDS", "") or DEFAULT_INTERVAL_SECONDS))
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS


def _ping(url: str) -> None:
    with urllib.request.urlopen(url, timeout=15) as response:
        response.read(1)


async def run_keepalive(url: str, interval: int) -> None:
    """Ping our own public URL so Render's free tier does not idle-sleep."""
    logger.info("Keep-alive ping enabled every %ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_ping, url)
        except Exception as exc:  # network blips must never stop the loop
            logger.warning("Keep-alive ping failed: %s", exc)
