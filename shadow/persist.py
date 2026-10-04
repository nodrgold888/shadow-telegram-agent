from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request

log = logging.getLogger("shadow.persist")

RENDER_API = "https://api.render.com/v1"


def persistence_available() -> bool:
    return bool(os.getenv("RENDER_API_KEY", "").strip() and os.getenv("RENDER_SERVICE_ID", "").strip())


def _put_env_var(service_id: str, api_key: str, key: str, value: str) -> None:
    request = urllib.request.Request(
        f"{RENDER_API}/services/{service_id}/env-vars/{key}",
        data=json.dumps({"value": value}).encode(),
        method="PUT",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


async def save_session(session: str) -> bool:
    """Store the Telegram session in Render's secret env vars so it survives restarts.

    Returns False (without raising) when persistence is not configured or fails,
    so a successful login is never lost just because saving failed.
    """
    if not persistence_available():
        log.warning("RENDER_API_KEY is not set; the Telegram session will not survive a restart")
        return False
    try:
        await asyncio.to_thread(
            _put_env_var,
            os.environ["RENDER_SERVICE_ID"].strip(),
            os.environ["RENDER_API_KEY"].strip(),
            "TELEGRAM_SESSION",
            session,
        )
    except Exception as exc:  # never log the session itself
        log.error("Could not persist Telegram session: %s", type(exc).__name__)
        return False
    log.info("Telegram session saved to Render environment")
    return True
