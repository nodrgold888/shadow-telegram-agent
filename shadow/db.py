"""SQLite-backed state for Shadow.

This replaces the old "persistence" model, which was really just repeatedly
overwriting Render service environment variables through Render's HTTP API
(see git history of persist.py) as a stand-in for a real database. That had
no transactions, no schema, and silently raced under concurrent writes.

SQLite is the primary store now. On Render's free plan there is still no
persistent disk attached, so the database file itself is wiped on every
redeploy/restart -- the same durability ceiling the old approach had. To
avoid a hard regression on that specific point, every write here also fires
a best-effort write-through to the legacy Render-env-var mechanism (see
persist.py), and a fresh/empty database seeds itself from those env vars on
first boot (`_migrate_from_legacy_env`). That backup path is optional
infrastructure, not the source of truth; SQLite is read from directly for
every load.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .chat_memory import normalize_chat_profiles

log = logging.getLogger("shadow.db")

# kv_state keys reuse the original env-var names so the legacy-env migration
# below is a direct 1:1 copy, and so persist.py's public API (which already
# used these names) needed no renaming.
LEGACY_ENV_KV_KEYS = ("TELEGRAM_SESSION", "APPROVED_CHAT_IDS", "REPLY_ENABLED", "SHADOW_MODEL_SELECTION")

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None


def _db_path() -> Path:
    raw = os.getenv("SHADOW_DB_PATH", "").strip()
    path = Path(raw) if raw else Path(".shadow-state/shadow.db")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_from_legacy_env(conn: sqlite3.Connection) -> None:
    """One-time seed from the old Render-env-var state, only on a fresh DB."""
    (existing,) = conn.execute("SELECT COUNT(*) FROM kv_state").fetchone()
    if existing:
        return
    seeded = False
    for key in LEGACY_ENV_KV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, _now()),
            )
            seeded = True
    raw_profiles = os.getenv("SHADOW_CHAT_PROFILES", "").strip()
    if raw_profiles:
        try:
            profiles = normalize_chat_profiles(json.loads(raw_profiles))
            for chat_id, profile in profiles.items():
                conn.execute(
                    "INSERT OR REPLACE INTO chat_profiles"
                    "(chat_id, style, memory, notes, routines, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        chat_id,
                        profile.get("style", ""),
                        profile.get("memory", ""),
                        profile.get("notes", ""),
                        profile.get("routines", ""),
                        _now(),
                    ),
                )
            seeded = True
        except (json.JSONDecodeError, ValueError):
            log.warning("Ignoring invalid legacy SHADOW_CHAT_PROFILES during migration")
    conn.commit()
    if seeded:
        log.info("Migrated legacy Render-env-var state into SQLite on first boot")


def _connect() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv_state ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chat_profiles ("
            "chat_id TEXT PRIMARY KEY, style TEXT NOT NULL DEFAULT '', "
            "memory TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', "
            "routines TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)"
        )
        conn.commit()
        _migrate_from_legacy_env(conn)
        _connection = conn
    return _connection


def get_kv(key: str) -> str | None:
    with _lock:
        row = _connect().execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_kv(key: str, value: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO kv_state(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, _now()),
        )
        conn.commit()


def list_chat_profiles() -> dict[str, dict[str, str]]:
    with _lock:
        rows = _connect().execute(
            "SELECT chat_id, style, memory, notes, routines FROM chat_profiles"
        ).fetchall()
    return {
        chat_id: {"style": style, "memory": memory, "notes": notes, "routines": routines}
        for chat_id, style, memory, notes, routines in rows
    }


def replace_all_chat_profiles(profiles: dict[str, dict[str, str]]) -> None:
    """Atomically replace the full chat-profiles table (matches the caller's
    in-memory model, which always holds and saves the complete set)."""
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM chat_profiles")
        conn.executemany(
            "INSERT INTO chat_profiles(chat_id, style, memory, notes, routines, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    chat_id,
                    profile.get("style", ""),
                    profile.get("memory", ""),
                    profile.get("notes", ""),
                    profile.get("routines", ""),
                    _now(),
                )
                for chat_id, profile in profiles.items()
            ],
        )
        conn.commit()


def reset_for_tests() -> None:
    """Close and forget the cached connection so a test can point SHADOW_DB_PATH
    at a fresh file and get a clean database."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None
