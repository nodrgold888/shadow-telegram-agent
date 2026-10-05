"""Double-submit-cookie CSRF protection for state-changing dashboard/setup routes.

SameSite=Strict on the session cookie is a reasonable baseline on its own,
but isn't defense in depth. On login, a second, JS-readable cookie carries a
random token; the frontend echoes it back as a header on every non-GET
request, and this dependency checks the two match.
"""

from __future__ import annotations

import secrets

from fastapi import Cookie, Header, HTTPException

CSRF_COOKIE_NAME = "shadow_csrf"
CSRF_HEADER_NAME = "x-shadow-csrf"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(
    x_shadow_csrf: str | None = Header(default=None, alias=CSRF_HEADER_NAME),
    shadow_csrf: str | None = Cookie(default=None),
) -> None:
    if not shadow_csrf or not x_shadow_csrf or not secrets.compare_digest(x_shadow_csrf, shadow_csrf):
        raise HTTPException(status_code=403, detail="CSRF tekshiruvi muvaffaqiyatsiz")
