from __future__ import annotations

import pytest
from fastapi import HTTPException

from conftest import ADMIN_TOKEN, login


def test_state_changing_route_rejects_missing_csrf_header(client):
    login(client, ADMIN_TOKEN)
    response = client.post("/dashboard/api/replies", json={"enabled": False})
    assert response.status_code == 403


def test_state_changing_route_rejects_mismatched_csrf_header(client):
    login(client, ADMIN_TOKEN)
    response = client.post(
        "/dashboard/api/replies",
        json={"enabled": False},
        headers={"x-shadow-csrf": "not-the-real-token"},
    )
    assert response.status_code == 403


def test_state_changing_route_accepts_matching_csrf_header(client):
    login(client, ADMIN_TOKEN)
    csrf = client.cookies.get("shadow_csrf")
    response = client.post(
        "/dashboard/api/replies",
        json={"enabled": False},
        headers={"x-shadow-csrf": csrf},
    )
    assert response.status_code == 200


def test_read_only_routes_do_not_require_csrf(client):
    login(client, ADMIN_TOKEN)
    assert client.get("/dashboard/api/status").status_code == 200


def test_login_rate_limited_after_too_many_attempts(client):
    for _ in range(5):
        response = client.post("/dashboard/auth", json={"token": "wrong"})
        assert response.status_code == 401
    limited = client.post("/dashboard/auth", json={"token": "wrong"})
    assert limited.status_code == 429


def test_rate_limiter_isolates_by_key():
    from shadow.ratelimit import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(max_attempts=2, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-a")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("client-a")
    assert exc_info.value.status_code == 429
    # A different key has its own independent bucket.
    limiter.check("client-b")
    limiter.check("client-b")
