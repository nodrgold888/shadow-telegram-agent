from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from urllib.error import HTTPError
import tempfile
import threading
from pathlib import Path

from .chat_memory import MAX_CHAT_PROFILE_BYTES, normalize_chat_profiles

log = logging.getLogger("shadow.persist")

RENDER_API = "https://api.render.com/v1"


_local_lock = threading.Lock()
_LOCAL_KEYS = {"TELEGRAM_SESSION", "APPROVED_CHAT_IDS", "REPLY_ENABLED", "SHADOW_MODEL_SELECTION", "SHADOW_CHAT_PROFILES", "FRIEND_CHAT_IDS", "GREET_UNKNOWN", "VIDEO_UNKNOWN", "VOICE_UNKNOWN", "NOTIFY_UNKNOWN"}


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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{RENDER_API}/services/{service_id}/env-vars"
    update = urllib.request.Request(
        f"{url}/{key}", data=json.dumps({"value": value}).encode(),
        method="PUT", headers=headers,
    )
    try:
        with urllib.request.urlopen(update, timeout=20) as response:
            response.read()
        return
    except HTTPError as exc:
        if exc.code != 404:
            raise
    # Older Render services may not have a key yet. Create it on first save,
    # then subsequent writes use the update endpoint above.
    create = urllib.request.Request(
        url, data=json.dumps({"key": key, "value": value}).encode(),
        method="POST", headers=headers,
    )
    with urllib.request.urlopen(create, timeout=20) as response:
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


FRIEND_CATEGORIES = {"oila": "Oila", "ish": "Ish", "dostlar": "Do‘stlar"}
DEFAULT_FRIEND_CATEGORY = "dostlar"


def parse_friend_chats(raw: str) -> dict[int, str]:
    """`5:oila,7` -> {5: "oila", 7: "dostlar"}. A bare id or unknown category means "dostlar"."""
    result: dict[int, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        chat, _, category = item.partition(":")
        result[int(chat)] = category.strip() if category.strip() in FRIEND_CATEGORIES else DEFAULT_FRIEND_CATEGORY
    return result


def format_friend_chats(friends: dict[int, str]) -> str:
    return ",".join(f"{chat}:{friends[chat]}" for chat in sorted(friends))


def load_friend_chats() -> dict[int, str]:
    """Chats (friends) where Shadow never writes. Bad values are ignored, never fatal."""
    raw = load_local_settings().get("FRIEND_CHAT_IDS", os.getenv("FRIEND_CHAT_IDS", "")).strip()
    try:
        return parse_friend_chats(raw)
    except ValueError:
        log.warning("Ignoring invalid FRIEND_CHAT_IDS")
        return {}


async def save_friend_chats(value: str) -> bool:
    if os.getenv("SHADOW_STATE_FILE", "").strip():
        try:
            await asyncio.to_thread(_save_local, "FRIEND_CHAT_IDS", value)
            return True
        except Exception as exc:
            log.error("Could not save friend list locally: %s", type(exc).__name__)
            return False
    if not persistence_available():
        return False
    try:
        await asyncio.to_thread(
            _put_env_var, os.environ["RENDER_SERVICE_ID"].strip(), os.environ["RENDER_API_KEY"].strip(),
            "FRIEND_CHAT_IDS", value)
    except Exception as exc:
        log.warning("Could not persist friend list: %s", type(exc).__name__)
        return False
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


def load_chat_profiles() -> dict[str, dict[str, str]]:
    """Load owner-entered per-chat context without retaining message transcripts."""
    raw = load_local_settings().get(
        "SHADOW_CHAT_PROFILES",
        os.getenv("SHADOW_CHAT_PROFILES", ""),
    ).strip()
    if not raw:
        return {}
    try:
        return normalize_chat_profiles(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Saved chat profiles are invalid") from exc


async def save_chat_profiles(profiles: dict[str, dict[str, str]]) -> bool:
    """Persist chat-scoped notes privately in local state or a Render service variable."""
    try:
        normalized = normalize_chat_profiles(profiles)
        value = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if len(value.encode("utf-8")) > MAX_CHAT_PROFILE_BYTES:
            return False
    except ValueError:
        return False
    if os.getenv("SHADOW_STATE_FILE", "").strip():
        try:
            await asyncio.to_thread(_save_local, "SHADOW_CHAT_PROFILES", value)
            return True
        except Exception as exc:
            log.error("Could not save chat profiles locally: %s", type(exc).__name__)
            return False
    if not persistence_available():
        return False
    try:
        await asyncio.to_thread(
            _put_env_var,
            os.environ["RENDER_SERVICE_ID"].strip(),
            os.environ["RENDER_API_KEY"].strip(),
            "SHADOW_CHAT_PROFILES",
            value,
        )
    except Exception as exc:
        log.warning("Could not persist chat profiles: %s", type(exc).__name__)
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


def _load_flag(key: str) -> bool:
    return load_local_settings().get(key, os.getenv(key, "")).strip().lower() in {"1", "true", "yes", "on"}


async def _save_flag(key: str, enabled: bool) -> bool:
    value = "true" if enabled else "false"
    if os.getenv("SHADOW_STATE_FILE", "").strip():
        try:
            await asyncio.to_thread(_save_local, key, value)
            return True
        except Exception as exc:
            log.error("Could not save local state: %s", type(exc).__name__)
            return False
    if not persistence_available():
        return False
    try:
        await asyncio.to_thread(
            _put_env_var, os.environ["RENDER_SERVICE_ID"].strip(), os.environ["RENDER_API_KEY"].strip(), key, value)
    except Exception as exc:
        log.warning("Could not persist %s: %s", key, type(exc).__name__)
        return False
    return True


def load_greet_unknown() -> bool:
    return _load_flag("GREET_UNKNOWN")


async def save_greet_unknown(enabled: bool) -> bool:
    return await _save_flag("GREET_UNKNOWN", enabled)


STRANGER_FLAGS = {"voice_unknown": "VOICE_UNKNOWN", "notify_unknown": "NOTIFY_UNKNOWN"}


def load_stranger_flags() -> dict[str, bool]:
    return {name: _load_flag(key) for name, key in STRANGER_FLAGS.items()}


async def save_stranger_flag(name: str, enabled: bool) -> bool:
    return await _save_flag(STRANGER_FLAGS[name], enabled)


def load_video_unknown() -> bool:
    return _load_flag("VIDEO_UNKNOWN")


async def save_video_unknown(enabled: bool) -> bool:
    return await _save_flag("VIDEO_UNKNOWN", enabled)


async def save_env_vars(values: dict[str, str]) -> bool:
    """Write several variables to the Render service (a restart follows). Never logs values."""
    if os.getenv("SHADOW_STATE_FILE", "").strip() or not persistence_available():
        return False
    service_id = os.environ["RENDER_SERVICE_ID"].strip()
    api_key = os.environ["RENDER_API_KEY"].strip()
    try:
        for key, value in values.items():
            await asyncio.to_thread(_put_env_var, service_id, api_key, key, value)
    except Exception as exc:
        log.warning("Could not persist AI provider: %s", type(exc).__name__)
        return False
    return True


def _delete_env_var(service_id: str, api_key: str, key: str) -> None:
    request = urllib.request.Request(
        f"{RENDER_API}/services/{service_id}/env-vars/{key}", method="DELETE",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except HTTPError as exc:
        if exc.code != 404:  # already gone is fine
            raise


async def delete_env_vars(keys: list[str]) -> bool:
    """Remove variables from the Render service (a restart follows)."""
    if os.getenv("SHADOW_STATE_FILE", "").strip() or not persistence_available():
        return False
    service_id = os.environ["RENDER_SERVICE_ID"].strip()
    api_key = os.environ["RENDER_API_KEY"].strip()
    try:
        for key in keys:
            await asyncio.to_thread(_delete_env_var, service_id, api_key, key)
    except Exception as exc:
        log.warning("Could not remove AI provider: %s", type(exc).__name__)
        return False
    return True
