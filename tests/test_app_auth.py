from __future__ import annotations

from conftest import ADMIN_TOKEN, SETUP_TOKEN, login


def _set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def test_login_with_admin_token_succeeds(client):
    response = client.post("/dashboard/auth", json={"token": ADMIN_TOKEN})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    cookies = _set_cookie_headers(response)
    assert any(c.startswith("shadow_session=") for c in cookies)
    assert any(c.startswith("shadow_csrf=") for c in cookies)


def test_login_with_setup_token_succeeds(client):
    response = client.post("/dashboard/auth", json={"token": SETUP_TOKEN})
    assert response.status_code == 200


def test_login_wrong_token_rejected_with_no_session_cookie(client):
    response = client.post("/dashboard/auth", json={"token": "nope"})
    assert response.status_code == 401
    assert "shadow_session" not in client.cookies


def test_session_cookie_is_opaque_not_the_raw_token(client):
    """The cookie value must never be the literal master secret."""
    response = client.post("/dashboard/auth", json={"token": ADMIN_TOKEN})
    session_cookie = client.cookies.get("shadow_session")
    assert session_cookie is not None
    assert session_cookie != ADMIN_TOKEN
    assert session_cookie != SETUP_TOKEN


def test_dashboard_status_requires_session(client):
    assert client.get("/dashboard/api/status").status_code == 401
    login(client, ADMIN_TOKEN)
    assert client.get("/dashboard/api/status").status_code == 200


def test_logout_revokes_session(client):
    login(client, ADMIN_TOKEN)
    assert client.get("/dashboard/api/status").status_code == 200
    assert client.post("/dashboard/logout").status_code == 403
    csrf = client.cookies.get("shadow_csrf")
    assert client.post("/dashboard/logout", headers={"x-shadow-csrf": csrf}).status_code == 200
    assert client.get("/dashboard/api/status").status_code == 401


def test_setup_token_session_grants_dashboard_and_setup_access(client):
    login(client, SETUP_TOKEN)
    assert client.get("/dashboard/api/status").status_code == 200

    async def noop(phone: str) -> None:
        return None

    client.app.state.agent.request_login_code = noop
    csrf = client.cookies.get("shadow_csrf")
    response = client.post(
        "/setup/telegram/code",
        json={"phone": "+998901234567"},
        headers={"x-shadow-csrf": csrf},
    )
    # The setup-token session is allowed past the auth check into the route.
    assert response.status_code == 200


def test_admin_token_session_denied_setup_access(client):
    login(client, ADMIN_TOKEN)
    csrf = client.cookies.get("shadow_csrf")
    response = client.post(
        "/setup/telegram/code",
        json={"phone": "+998901234567"},
        headers={"x-shadow-csrf": csrf},
    )
    assert response.status_code == 401


def test_secure_flag_set_over_https(https_client):
    """Regression test for the root-cause bug: the cookie's Secure attribute
    must reflect the connection's real scheme, not a hostname guess."""
    response = https_client.post("/dashboard/auth", json={"token": ADMIN_TOKEN})
    cookies = _set_cookie_headers(response)
    session_cookie = next(c for c in cookies if c.startswith("shadow_session="))
    assert "secure" in session_cookie.lower()


def test_secure_flag_not_set_over_plain_http(client):
    response = client.post("/dashboard/auth", json={"token": ADMIN_TOKEN})
    cookies = _set_cookie_headers(response)
    session_cookie = next(c for c in cookies if c.startswith("shadow_session="))
    assert "secure" not in session_cookie.lower()


def test_cookie_secure_override_env_forces_secure_over_http(app_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from shadow.app import create_app
    from shadow.config import Settings

    monkeypatch.setenv("SHADOW_COOKIE_SECURE", "true")
    settings = Settings.from_env()
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as test_client:
        response = test_client.post("/dashboard/auth", json={"token": ADMIN_TOKEN})
        cookies = _set_cookie_headers(response)
        session_cookie = next(c for c in cookies if c.startswith("shadow_session="))
        assert "secure" in session_cookie.lower()
