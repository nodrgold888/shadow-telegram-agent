from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

CODE_TTL_SECONDS = 300
SESSION_TTL_SECONDS = 12 * 3600
REQUEST_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS_PER_CODE = 5
LOCKOUT_SECONDS = 900
SESSION_PREFIX = "s."


class LoginError(Exception):
    """Raised with a user-facing (Uzbek) message and the HTTP status to answer with."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class PanelLogin:
    """One-time login codes delivered to the owner's Telegram Saved Messages.

    Codes and sessions live in memory only: a restart signs everyone out, which
    is fine for a single-owner panel. A code is single use, expires after five
    minutes, allows five wrong guesses, and a failed code locks logins for 15 minutes.
    """

    _code: str | None = None
    _code_expires: float = 0
    _attempts: int = 0
    _last_request: float = float("-inf")
    _locked_until: float = 0
    _sessions: dict[str, float] = field(default_factory=dict)

    def issue_code(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        if now < self._locked_until:
            raise LoginError("Juda ko‘p xato urinish. Birozdan keyin qayta urinib ko‘ring.", 429)
        if now - self._last_request < REQUEST_COOLDOWN_SECONDS:
            raise LoginError("Kod yaqinda yuborilgan. Bir daqiqa kuting.", 429)
        self._last_request = now
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._code_expires = now + CODE_TTL_SECONDS
        self._attempts = 0
        return self._code

    def verify_code(self, supplied: str, now: float | None = None) -> str:
        """Return a new session token, or raise LoginError."""
        now = time.time() if now is None else now
        if now < self._locked_until:
            raise LoginError("Juda ko‘p xato urinish. Birozdan keyin qayta urinib ko‘ring.", 429)
        if not self._code or now > self._code_expires:
            self._code = None
            raise LoginError("Kod muddati tugagan. Yangi kod so‘rang.", 400)
        self._attempts += 1
        if not secrets.compare_digest(supplied.strip().encode(), self._code.encode()):
            if self._attempts >= MAX_ATTEMPTS_PER_CODE:
                self._code = None
                self._locked_until = now + LOCKOUT_SECONDS
            raise LoginError("Kod noto‘g‘ri.", 400)
        self._code = None
        return self._new_session(now)

    def _new_session(self, now: float) -> str:
        self._sessions = {token: exp for token, exp in self._sessions.items() if exp > now}
        token = SESSION_PREFIX + secrets.token_urlsafe(32)
        self._sessions[token] = now + SESSION_TTL_SECONDS
        return token

    def session_valid(self, token: str | None, now: float | None = None) -> bool:
        if not token or not token.startswith(SESSION_PREFIX):
            return False
        now = time.time() if now is None else now
        expires = self._sessions.get(token)
        if expires is None:
            return False
        if expires <= now:
            self._sessions.pop(token, None)
            return False
        return True

    def end_session(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
