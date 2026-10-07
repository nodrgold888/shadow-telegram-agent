import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from shadow import telegram_agent
from tests.test_friends import make_agent


class HangingClient:
    """Telethon-like client whose network calls never finish."""

    def __init__(self, *args, **kwargs):
        self.disconnected = False

    async def connect(self):
        await asyncio.sleep(3600)

    async def send_code_request(self, phone):
        await asyncio.sleep(3600)

    async def sign_in(self, **kwargs):
        await asyncio.sleep(3600)

    async def disconnect(self):
        self.disconnected = True


class SetupTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_code_request_times_out_and_releases_the_lock(self):
        agent = make_agent(TELEGRAM_API_ID="1", TELEGRAM_API_HASH="abc")
        created = []

        def factory(*args, **kwargs):
            created.append(HangingClient())
            return created[-1]

        with mock.patch.object(telegram_agent, "TelegramClient", factory), \
                mock.patch.object(telegram_agent, "SETUP_NETWORK_TIMEOUT_SECONDS", 0.05):
            with self.assertRaises(telegram_agent.TelegramSetupTimeout):
                await agent.request_login_code("+998901234567")
            self.assertTrue(created[0].disconnected)
            self.assertFalse(agent._setup_lock.locked())
            with self.assertRaises(telegram_agent.TelegramSetupTimeout):
                await agent.request_login_code("+998901234567")

    async def test_verify_and_password_time_out(self):
        agent = make_agent()
        agent._login_client = HangingClient()
        agent._login_phone = "+998901234567"
        agent._login_hash = "hash"
        with mock.patch.object(telegram_agent, "SETUP_NETWORK_TIMEOUT_SECONDS", 0.05):
            with self.assertRaises(telegram_agent.TelegramSetupTimeout):
                await agent.complete_login("12345")
            with self.assertRaises(telegram_agent.TelegramSetupTimeout):
                await agent.complete_password("secret")
            self.assertFalse(agent._setup_lock.locked())


if __name__ == "__main__":
    unittest.main()
