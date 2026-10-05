from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request

from . import db
from .chat_memory import normalize_chat_profiles

log = logging.getLogger("shadow.persist")

RENDER_API = "https://api.render.com/v1"


def load_local_settings() -> dict[str, str]:
    """Seed values for Settings.from_env() from SQLite (authoritative once populated;
    falls back to raw env vars inside Settings.from_env() itself when a key is absent)."""
    result: dict[str, str] = {}
    for key in db.LEGACY_ENV_KV_KEYS:
        value = db.get_kv(key)
        if value is not None:
            result[key] = value
    return result


def _backup_configured() -> bool:
    return bool(os.getenv("RENDER_API_KEY", "").strip() and os.getenv("RENDER_SERVICE_ID", "").strip())


def persistence_available() -> bool:
    """SQLite is always available as the primary store; this reports whether the
    redundant Render-env-var backup/restore safety net is also configured."""
    return _backup_configured()


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


def _backup_kv(key: str, value: str) -> None:
    """Best-effort write-through to the legacy Render env-var store. Never raises
    past this function; a failure here does not affect the SQLite write's success."""
    if not _backup_configured():
        return
    try:
        _put_env_var(
            os.environ["RENDER_SERVICE_ID"].strip(),
            os.environ["RENDER_API_KEY"].strip(),
            key,
            value,
        )
    except Exception as exc:  # never log secret values (e.g. the session string)
        log.warning("SQLite save for %s succeeded but Render backup failed: %s", key, type(exc).__name__)


async def _save_kv(key: str, value: str) -> bool:
    """Returns False (without raising) only when the primary SQLite write fails,
    so a successful save is never reported lost just because the optional
    Render backup also failed."""
    try:
        await asyncio.to_thread(db.set_kv, key, value)
    except Exception as exc:
        log.error("Could not save %s to SQLite: %s", key, type(exc).__name__)
        return False
    await asyncio.to_thread(_backup_kv, key, value)
    return True


async def save_session(session: str) -> bool:
    """Store the Telegram session. Primary copy lives in SQLite; also
    write-through to Render env vars when configured, as a restore path after
    a redeploy wipes the (free-plan, non-persistent) container disk."""
    return await _save_kv("TELEGRAM_SESSION", session)


async def save_approved_chats(value: str) -> bool:
    """Persist the dashboard allowlist."""
    return await _save_kv("APPROVED_CHAT_IDS", value)


async def save_model_selection(openai_model: str, complex_openai_model: str) -> bool:
    """Persist the dashboard's two model choices as one atomic selection."""
    value = json.dumps(
        {"openai_model": openai_model, "complex_openai_model": complex_openai_model},
        separators=(",", ":"),
    )
    return await _save_kv("SHADOW_MODEL_SELECTION", value)


async def save_reply_enabled(enabled: bool) -> bool:
    """Persist the user's explicit reply switch choice."""
    return await _save_kv("REPLY_ENABLED", "true" if enabled else "false")


def load_chat_profiles() -> dict[str, dict[str, str]]:
    """Load owner-entered per-chat context without retaining message transcripts."""
    return db.list_chat_profiles()


async def save_chat_profiles(profiles: dict[str, dict[str, str]]) -> bool:
    """Persist chat-scoped notes. Caller always passes the complete set, which
    is stored as one real table (no more 64KB JSON-blob size ceiling)."""
    try:
        normalized = normalize_chat_profiles(profiles)
    except ValueError:
        return False
    try:
        await asyncio.to_thread(db.replace_all_chat_profiles, normalized)
    except Exception as exc:
        log.error("Could not save chat profiles to SQLite: %s", type(exc).__name__)
        return False
    if _backup_configured():
        value = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        await asyncio.to_thread(_backup_kv, "SHADOW_CHAT_PROFILES", value)
    return True
