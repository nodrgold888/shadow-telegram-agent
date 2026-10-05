"""Opaque, revocable login sessions.

Previously the dashboard/setup "session" cookie held the raw ADMIN_TOKEN or
SETUP_TOKEN value itself -- the long-lived master secret sat in the browser's
cookie jar, couldn't be revoked without rotating the real token (breaking
/admin/status and anything else using it), and carried no server-side
metadata at all.

This module hands out a random opaque session id instead and keeps the
{role, expiry} mapping in process memory. Logging out revokes just that
session id; the master token never leaves the login request itself. The
store is intentionally memory-only and is lost on every process restart --
on Render's free plan that already happens routinely (sleep after idle,
redeploys), and re-entering the shared token is cheap, so this is an
accepted tradeoff rather than a gap to fix.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal

Role = Literal["admin", "setup"]

_TTL_SECONDS = 1800  # mirrors the cookie's existing max_age

_lock = threading.Lock()
_sessions: dict[str, "_Session"] = {}


@dataclass
class _Session:
    role: Role
    expires_at: float


def create_session(role: Role) -> str:
    session_id = secrets.token_urlsafe(32)
    with _lock:
        _sessions[session_id] = _Session(role=role, expires_at=time.monotonic() + _TTL_SECONDS)
    return session_id


def role_for(session_id: str | None) -> Role | None:
    """Look up the session's role. Expiry is absolute (matches the cookie's
    fixed max_age, which is not re-issued per request). Returns None for a
    missing/expired/unknown id."""
    if not session_id:
        return None
    now = time.monotonic()
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at < now:
            del _sessions[session_id]
            return None
        return session.role


def revoke(session_id: str | None) -> None:
    if not session_id:
        return
    with _lock:
        _sessions.pop(session_id, None)


def reset_for_tests() -> None:
    with _lock:
        _sessions.clear()
