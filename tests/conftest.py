from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shadow import db, sessions
from shadow.app import create_app
from shadow.config import Settings
from shadow.ratelimit import admin_status_limiter, auth_limiter

ADMIN_TOKEN = "admin-secret-token"
SETUP_TOKEN = "setup-secret-token"


@pytest.fixture
def app_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("SETUP_TOKEN", SETUP_TOKEN)
    monkeypatch.setenv("SHADOW_DB_PATH", str(tmp_path / "shadow.db"))
    for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "RENDER_API_KEY", "RENDER_SERVICE_ID", "SHADOW_COOKIE_SECURE"):
        monkeypatch.delenv(key, raising=False)
    db.reset_for_tests()
    sessions.reset_for_tests()
    auth_limiter.reset_for_tests()
    admin_status_limiter.reset_for_tests()
    return Settings.from_env()


@pytest.fixture
def client(app_settings):
    app = create_app(app_settings)
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def https_client(app_settings):
    app = create_app(app_settings)
    with TestClient(app, base_url="https://shadow.example.com") as test_client:
        yield test_client


def login(client: TestClient, token: str) -> None:
    response = client.post("/dashboard/auth", json={"token": token})
    assert response.status_code == 200, response.text
