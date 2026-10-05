from __future__ import annotations

import asyncio

import pytest

from conftest import SETUP_TOKEN, login
from shadow.telegram_agent import TelegramSetupTimeout


def _csrf(client) -> str:
    return client.cookies.get("shadow_csrf")


def test_setup_code_timeout_maps_to_504(client):
    login(client, SETUP_TOKEN)

    async def boom(phone: str) -> None:
        raise TelegramSetupTimeout("Telegram bilan aloqa vaqti tugadi")

    client.app.state.agent.request_login_code = boom
    response = client.post(
        "/setup/telegram/code",
        json={"phone": "+998901234567"},
        headers={"x-shadow-csrf": _csrf(client)},
    )
    assert response.status_code == 504
    assert "aloqa" in response.json()["detail"]


def test_setup_verify_timeout_maps_to_504(client):
    login(client, SETUP_TOKEN)

    async def boom(code: str) -> str:
        raise TelegramSetupTimeout("timeout")

    client.app.state.agent.complete_login = boom
    response = client.post(
        "/setup/telegram/verify",
        json={"code": "12345"},
        headers={"x-shadow-csrf": _csrf(client)},
    )
    assert response.status_code == 504


def test_setup_code_happy_path(client):
    login(client, SETUP_TOKEN)

    calls = []

    async def fake_request_login_code(phone: str) -> None:
        calls.append(phone)

    client.app.state.agent.request_login_code = fake_request_login_code
    response = client.post(
        "/setup/telegram/code",
        json={"phone": "+998901234567"},
        headers={"x-shadow-csrf": _csrf(client)},
    )
    assert response.status_code == 200
    assert calls == ["+998901234567"]


def test_setup_lock_serializes_concurrent_attempts(app_settings):
    """Baseline for the existing _setup_lock behavior: two concurrent setup
    calls are serialized, not run in parallel -- this is the documented gap
    (no cancel/force-release), captured as a test so a future change to that
    behavior shows up as an intentional diff, not a silent regression."""
    from shadow.telegram_agent import TelegramAgent

    agent = TelegramAgent(app_settings)
    order: list[str] = []

    async def slow_call(label: str) -> None:
        async with agent._setup_lock:
            order.append(f"{label}-start")
            await asyncio.sleep(0.05)
            order.append(f"{label}-end")

    async def run() -> None:
        await asyncio.gather(slow_call("first"), slow_call("second"))

    asyncio.run(run())
    # Serialized: one call's start/end must not interleave with the other's.
    assert order in (
        ["first-start", "first-end", "second-start", "second-end"],
        ["second-start", "second-end", "first-start", "first-end"],
    )
