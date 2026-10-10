from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from urllib.error import HTTPError
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .chat_memory import MAX_CHAT_PROFILE_BYTES, normalize_chat_profiles

log = logging.getLogger("shadow.persist")

RENDER_API = "https://api.render.com/v1"


_local_lock = threading.Lock()
_LOCAL_KEYS = {"TELEGRAM_SESSION", "TELEGRAM_ACCOUNTS", "TELEGRAM_ACCOUNT_SETTINGS", "APPROVED_CHAT_IDS", "REPLY_ENABLED", "GROUP_REPLY_ENABLED", "GROUP_REPLY_MODE", "SHADOW_MODEL_SELECTION", "SHADOW_CHAT_PROFILES", "FRIEND_CHAT_IDS", "GREET_UNKNOWN", "VIDEO_UNKNOWN", "VOICE_UNKNOWN", "NOTIFY_UNKNOWN"}


# Per-account state. Each Telegram account keeps its own allowlist, friend list, chat memory, reply
# switches and model choice. They live in one JSON value (TELEGRAM_ACCOUNT_SETTINGS) keyed by account
# id; the active account's values are layered over the saved settings, and writes to these keys go
# into that account's bundle. With no active scope (an older single-account setup) nothing changes.
SCOPED_DEFAULTS = {
    "APPROVED_CHAT_IDS": "", "FRIEND_CHAT_IDS": "", "SHADOW_CHAT_PROFILES": "", "SHADOW_MODEL_SELECTION": "",
    "REPLY_ENABLED": "false", "GROUP_REPLY_ENABLED": "true", "GROUP_REPLY_MODE": "mentions",
    "GREET_UNKNOWN": "false", "VIDEO_UNKNOWN": "false", "VOICE_UNKNOWN": "false", "NOTIFY_UNKNOWN": "false",
    # AI credentials and provider order are private to the Telegram account too.
    "OPENAI_API_KEY": "", "OPENAI_MODEL": "gpt-5-mini", "OPENAI_COMPLEX_MODEL": "gpt-6-luna",
    "AI_BASE_URL": "", "AI_API_KEY": "", "AI_MODEL": "", "AI_NAME": "", "AI_PRIMARY": "", "AI_FIRST_SLOT": "",
    "AI_WORK_MODE": "professional",
    "ALWAYS_ONLINE": "true", "PUBLIC_BANK_REPLY": "false", "CONTEXT_MESSAGES": "12", "MAX_REPLY_CHARS": "3800",
}
for _slot in range(2, 9):
    for _field in ("BASE_URL", "API_KEY", "MODEL", "NAME"):
        SCOPED_DEFAULTS[f"AI_{_field}_{_slot}"] = ""
_LOCAL_KEYS.update(SCOPED_DEFAULTS)
_scope: str | None = None
_scope_checked = False
_bundles: dict[str, dict[str, str]] | None = None
_scope_lock = threading.RLock()
_default_scope = object()
_task_scope = ContextVar("shadow_account_scope", default=_default_scope)
_render_write_lock = threading.Lock()


@contextmanager
def account_scope(account_id: str | None):
    """Pin background workers and requests to their own account, including to_thread calls."""
    token = _task_scope.set(account_id)
    try:
        yield
    finally:
        _task_scope.reset(token)


def select_scope(account_id: str | None) -> None:
    """Select the administrator's default view without changing any worker's context."""
    global _scope, _scope_checked
    with _scope_lock:
        _scope, _scope_checked = account_id, True


def set_task_scope(account_id: str | None) -> None:
    _task_scope.set(account_id)


def _raw_local_settings() -> dict[str, str]:
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


def _parse_bundles(raw: str) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(account): {key: value for key, value in bundle.items()
                       if (key in SCOPED_DEFAULTS or key == "__legacy_migrated__") and isinstance(value, str)}
        for account, bundle in data.items() if isinstance(bundle, dict)
    }


def _load_bundles() -> dict[str, dict[str, str]]:
    global _bundles
    if _bundles is None:
        raw = _raw_local_settings().get("TELEGRAM_ACCOUNT_SETTINGS", os.getenv("TELEGRAM_ACCOUNT_SETTINGS", ""))
        _bundles = _parse_bundles(raw)
    return _bundles


def _derive_scope() -> None:
    """At boot the active account is the saved one whose session is the live TELEGRAM_SESSION."""
    global _scope, _scope_checked
    _scope_checked = True
    raw = _raw_local_settings()
    session = raw.get("TELEGRAM_SESSION", os.getenv("TELEGRAM_SESSION", "")).strip()
    if not session:
        return
    from .accounts import parse_accounts
    accounts = parse_accounts(raw.get("TELEGRAM_ACCOUNTS", os.getenv("TELEGRAM_ACCOUNTS", "")))
    for account_id, saved in accounts.items():
        if saved["session"] == session:
            _scope = account_id
            return


def current_scope() -> str | None:
    pinned = _task_scope.get()
    if pinned is not _default_scope:
        return pinned
    with _scope_lock:
        if not _scope_checked:
            _derive_scope()
        return _scope


def load_local_settings() -> dict[str, str]:
    data = _raw_local_settings()
    scope = current_scope()
    if scope is None:
        return data
    bundle = _load_bundles().get(scope, {})
    return {**data, **{key: bundle.get(key, default) for key, default in SCOPED_DEFAULTS.items()}}


async def enter_scope(account_id: str, migrate_globals: bool) -> bool:
    """Make `account_id` the active account. An account seen for the first time starts from a blank bundle
    (reply off, nothing approved), except the first account ever, which keeps the settings it already had."""
    global _scope, _scope_checked
    with _scope_lock:
        bundles = _load_bundles()
        created = account_id not in bundles
        raw = _raw_local_settings()
        legacy = (
            {key: raw.get(key, os.getenv(key, "")) for key in SCOPED_DEFAULTS
             if raw.get(key, os.getenv(key, "")).strip()} if migrate_globals else {}
        )
        changed = False
        if created:
            bundles[account_id] = legacy
            changed = True
        bundle = bundles[account_id]
        if migrate_globals and bundle.get("__legacy_migrated__") != "1":
            # Before per-account settings existed, the live session's values were global.
            # Adopt missing values only for that live account, and do it once.
            for key, value in legacy.items():
                bundle.setdefault(key, value)
            changed = True
        if bundle.get("__legacy_migrated__") != "1":
            # Mark secondary accounts too, so they can never inherit another account's
            # old global settings after a restart makes them the active Telegram session.
            bundle["__legacy_migrated__"] = "1"
            changed = True
        if _task_scope.get() is _default_scope:
            _scope, _scope_checked = account_id, True
        else:
            _task_scope.set(account_id)
        snapshot = json.dumps(bundles, ensure_ascii=False, separators=(",", ":"))
    if not changed:
        return True
    return await _save_setting("TELEGRAM_ACCOUNT_SETTINGS", snapshot)


def reset_scope() -> None:
    """For tests: forget the cached scope and bundles."""
    global _scope, _scope_checked, _bundles
    with _scope_lock:
        _scope, _scope_checked, _bundles = None, False, None
    _task_scope.set(_default_scope)


def _scoped_write(key: str, value: str) -> tuple[str, str]:
    """A write to a per-account key goes into the active account's bundle (and the bundle is what is stored)."""
    if key not in SCOPED_DEFAULTS:
        return key, value
    with _scope_lock:
        scope = current_scope()
        if scope is None:
            return key, value
        bundles = _load_bundles()
        bundles.setdefault(scope, {})[key] = value
        return "TELEGRAM_ACCOUNT_SETTINGS", json.dumps(bundles, ensure_ascii=False, separators=(",", ":"))


def _save_local(key: str, value: str) -> None:
    key, value = _scoped_write(key, value)
    with _local_lock:
        if key == "TELEGRAM_ACCOUNT_SETTINGS":
            with _scope_lock:
                value = json.dumps(_load_bundles(), ensure_ascii=False, separators=(",", ":"))
        path = Path(os.environ["SHADOW_STATE_FILE"]).resolve()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = _raw_local_settings()
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
    key, value = _scoped_write(key, value)
    # Serialize remote writes and refresh the shared bundle after acquiring the
    # lock: concurrent account saves must never overwrite each other's settings.
    with _render_write_lock:
        if key == "TELEGRAM_ACCOUNT_SETTINGS":
            with _scope_lock:
                value = json.dumps(_load_bundles(), ensure_ascii=False, separators=(",", ":"))
        _put_env_var_serial(service_id, api_key, key, value)


def _put_env_var_serial(service_id: str, api_key: str, key: str, value: str) -> None:
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


async def _save_setting(key: str, value: str) -> bool:
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


async def _save_flag(key: str, enabled: bool) -> bool:
    return await _save_setting(key, "true" if enabled else "false")


def load_accounts_raw() -> str:
    return load_local_settings().get("TELEGRAM_ACCOUNTS", os.getenv("TELEGRAM_ACCOUNTS", ""))


async def save_accounts(value: str) -> bool:
    """Persist the saved Telegram accounts (they hold sessions, so they live in the same secret store)."""
    return await _save_setting("TELEGRAM_ACCOUNTS", value)


async def save_group_reply_enabled(enabled: bool) -> bool:
    """Persist the independent group-reply switch when persistence is configured."""
    return await _save_flag("GROUP_REPLY_ENABLED", enabled)


async def save_group_reply_mode(mode: str) -> bool:
    """Persist which group messages Shadow answers ("mentions" or "all")."""
    return await _save_setting("GROUP_REPLY_MODE", mode)


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
    # Resolve the whole account bundle before awaiting I/O: one persisted snapshot instead
    # of separate Render updates that can restart midway through a multi-model setup.
    with _scope_lock:
        writes: dict[str, str] = {}
        for key, value in values.items():
            target, scoped_value = _scoped_write(key, value)
            writes[target] = scoped_value
    values = writes
    local_state = os.getenv("SHADOW_STATE_FILE", "").strip()
    if local_state:
        try:
            for key, value in values.items():
                await asyncio.to_thread(_save_local, key, value)
            return True
        except Exception as exc:
            log.warning("Could not persist account AI settings locally: %s", type(exc).__name__)
            return False
    if not persistence_available():
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
    local_state = os.getenv("SHADOW_STATE_FILE", "").strip()
    if local_state:
        try:
            for key in keys:
                if key in SCOPED_DEFAULTS:
                    await asyncio.to_thread(_save_local, key, "")
                else:
                    with _local_lock:
                        path = Path(local_state).resolve()
                        data = _raw_local_settings()
                        data.pop(key, None)
                        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            return True
        except Exception as exc:
            log.warning("Could not remove account AI settings locally: %s", type(exc).__name__)
            return False
    if not persistence_available():
        return False
    service_id = os.environ["RENDER_SERVICE_ID"].strip()
    api_key = os.environ["RENDER_API_KEY"].strip()
    try:
        updates: dict[str, str] = {}
        global_keys: list[str] = []
        for key in keys:
            storage_key, value = _scoped_write(key, "")
            if storage_key == key:
                global_keys.append(key)
            else:
                updates[storage_key] = value
        for key, value in updates.items():
            await asyncio.to_thread(_put_env_var, service_id, api_key, key, value)
        for key in global_keys:
            await asyncio.to_thread(_delete_env_var, service_id, api_key, key)
    except Exception as exc:
        log.warning("Could not remove AI provider: %s", type(exc).__name__)
        return False
    return True
