from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("shadow.persist")

RENDER_API = "https://api.render.com/v1"


_local_lock = threading.Lock()
_LOCAL_KEYS = {"TELEGRAM_SESSION", "APPROVED_CHAT_IDS", "REPLY_ENABLED", "SHADOW_MODEL_SELECTION"}


def load_local_settings() -> dict[str, str]:
    filename = os.getenv("SHADOW_STATE_FILE", "").strip()
    if not filename:
        return {}
    path = Path(filename)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(
        key not in _LOCAL_KEYS or not isinstance(value, str)
        for key, value in data.items()
    ):
        raise ValueError("Invalid local Shadow state")
    return data


def _save_local(key: str, value: str) -> None:
    with _local_lock:
        path = Path(os.environ["SHADOW_STATE_FILE"]).resolve()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = load_local_settings()
        data[key] = value
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".shadow-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def persistence_available() -> bool:
    return bool(os.getenv("SHADOW_STATE_FILE", "").strip()) or bool(os.getenv("RENDER_API_KEY", "").strip() and os.getenv("RENDER_SERVICE_ID", "").strip())


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
    if os.getenv("SHADOW_STATE_FILE", "").strip():
        try:
            await asyncio.to_thread(_save_local, "TELEGRAM_SESSION", session)
            return True
        except Exception as exc:
            log.error("Could not save local state: %s", type(exc).__name__)
            return False
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


async def save_approved_chats(value: str) -> bool:
    """Persist the dashboard allowlist when Render persistence is configured."""
    if os.getenv("SHADOW_STATE_FILE", "").strip():
        try:
            await asyncio.to_thread(_save_local, "APPROVED_CHAT_IDS", value)
            return True
        except Exception as exc:
            log.error("Could not save local state: %s", type(exc).__name__)
            return False
    if not persistence_available():
        return False
    try:
        await asyncio.to_thread(
            _put_env_var,
            os.environ["RENDER_SERVICE_ID"].strip(),
            os.environ["RENDER_API_KEY"].strip(),
            "APPROVED_CHAT_IDS",
            value,
        )
    except Exception as exc:
        log.warning("Could not persist chat permissions: %s", type(exc).__name__)
        return False
    return True


async def save_model_selection(openai_model: str, complex_openai_model: str) -> bool:
    """Persist the dashboard's two model choices as one atomic selection."""
    value = json.dumps(
        {"openai_model": openai_model, "complex_openai_model": complex_openai_model},
        separators=(",", ":"),
    )
    if os.getenv("SHADOW_STATE_FILE", "").strip():
        try:
            await asyncio.to_thread(_save_local, "SHADOW_MODEL_SELECTION", value)
            return True
        except Exception as exc:
            log.error("Could not save model selection locally: %s", type(exc).__name__)
            return False
    if not persistence_available():
        return False
    try:
        await asyncio.to_thread(
            _put_env_var,
            os.environ["RENDER_SERVICE_ID"].strip(),
            os.environ["RENDER_API_KEY"].strip(),
            "SHADOW_MODEL_SELECTION",
            value,
        )
    except Exception as exc:
        log.warning("Could not persist model selection: %s", type(exc).__name__)
        return False
    return True


async def save_reply_enabled(enabled: bool) -> bool:
    """Persist the user's explicit reply switch choice, when configured."""
    if os.getenv("SHADOW_STATE_FILE", "").strip():
        try:
            await asyncio.to_thread(_save_local, "REPLY_ENABLED", "true" if enabled else "false")
            return True
        except Exception as exc:
            log.error("Could not save local state: %s", type(exc).__name__)
            return False
    if not persistence_available():
        return False
    try:
        await asyncio.to_thread(
            _put_env_var,
            os.environ["RENDER_SERVICE_ID"].strip(),
            os.environ["RENDER_API_KEY"].strip(),
            "REPLY_ENABLED",
            "true" if enabled else "false",
        )
    except Exception as exc:
        log.warning("Could not persist reply mode: %s", type(exc).__name__)
        return False
    return True
