import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shadow.accounts import MAX_ACCOUNTS, dump_accounts, forget, parse_accounts, public_view, remember
from tests.test_greeting import make_agent


class RegistryTests(unittest.TestCase):
    def test_roundtrip_and_bad_input_is_ignored(self):
        accounts = remember({}, 11, "@first", "session-one")
        self.assertEqual(parse_accounts(dump_accounts(accounts)), accounts)
        for raw in ("", "   ", "not json", "[]", '{"x":{"label":"a","session":"s"}}', '{"5":"text"}', '{"5":{"label":"a"}}'):
            self.assertEqual(parse_accounts(raw), {})

    def test_remember_refreshes_and_evicts_the_oldest(self):
        accounts = {}
        for number in range(1, MAX_ACCOUNTS + 2):
            accounts = remember(accounts, number, f"@u{number}", f"s{number}")
        self.assertEqual(len(accounts), MAX_ACCOUNTS)
        self.assertNotIn("1", accounts)
        accounts = remember(accounts, 5, "@renamed", "fresh")
        self.assertEqual(accounts["5"], {"label": "@renamed", "session": "fresh"})
        self.assertEqual(len(accounts), MAX_ACCOUNTS)

    def test_the_active_account_cannot_be_forgotten(self):
        accounts = remember(remember({}, 1, "@a", "sa"), 2, "@b", "sb")
        self.assertEqual(list(forget(accounts, "2", 1)), ["1"])
        with self.assertRaises(ValueError):
            forget(accounts, "1", 1)
        with self.assertRaises(ValueError):
            forget(accounts, "9", 1)

    def test_the_panel_never_sees_a_session(self):
        accounts = remember(remember({}, 1, "@a", "SECRET-SESSION-A"), 2, "@b", "SECRET-SESSION-B")
        view = public_view(accounts, 2)
        self.assertEqual(view, [{"id": "1", "label": "@a", "active": False}, {"id": "2", "label": "@b", "active": True}])
        self.assertNotIn("SECRET", json.dumps(view))
        self.assertEqual([a["active"] for a in public_view(accounts, None)], [False, False])


class FakeClient:
    instances = []

    def __init__(self, session, *args, **kwargs):
        self.session_string = session.save() if hasattr(session, "save") else str(session)
        self.authorized = True
        self.disconnected = False
        self.session = SimpleNamespace(save=lambda: self.session_string)
        FakeClient.instances.append(self)

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        self.disconnected = True


class SwitchAccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeClient.instances = []
        self.directory = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SHADOW_STATE_FILE": str(Path(self.directory.name) / "state.json")})
        self.env.start()
        self.agent = make_agent()
        self.agent.settings = replace(self.agent.settings, telegram_api_id=1, telegram_api_hash="hash", telegram_session="session-a")
        self.agent.account_id, self.agent.account_label = 1, "@first"
        self.agent.accounts = remember(remember({}, 1, "@first", "session-a"), 2, "@second", "session-b")

        async def activate(client):
            self.agent.client = client
            self.agent.account_id, self.agent.account_label = 2, "@second"
            self.agent.connected = True
        self.agent._activate_client = mock.AsyncMock(side_effect=activate)
        self.patch = mock.patch("shadow.telegram_agent.TelegramClient", FakeClient)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self.directory.cleanup()

    async def test_switching_connects_the_saved_session_and_makes_it_current(self):
        with mock.patch("shadow.telegram_agent.StringSession", side_effect=lambda value: SimpleNamespace(save=lambda: value)):
            result = await self.agent.switch_account("2")
        self.assertEqual((result["account"], result["changed"]), ("@second", True))
        self.assertEqual(self.agent.settings.telegram_session, "session-b")
        saved = json.loads((Path(self.directory.name) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["TELEGRAM_SESSION"], "session-b")
        self.assertIn("2", json.loads(saved["TELEGRAM_ACCOUNTS"]))

    async def test_a_dead_session_keeps_the_current_account(self):
        def dead(session, *args, **kwargs):
            client = FakeClient(session)
            client.authorized = False
            return client
        with mock.patch("shadow.telegram_agent.TelegramClient", dead), \
                mock.patch("shadow.telegram_agent.StringSession", side_effect=lambda value: SimpleNamespace(save=lambda: value)):
            with self.assertRaises(ValueError):
                await self.agent.switch_account("2")
        self.agent._activate_client.assert_not_awaited()
        self.assertEqual((self.agent.account_id, self.agent.settings.telegram_session), (1, "session-a"))

    async def test_unknown_and_current_accounts(self):
        with self.assertRaises(ValueError):
            await self.agent.switch_account("99")
        result = await self.agent.switch_account("1")
        self.assertFalse(result["changed"])
        self.assertEqual(FakeClient.instances, [])

    async def test_forgetting_saves_the_shorter_list(self):
        await self.agent.forget_account("2")
        self.assertEqual([a["id"] for a in self.agent.account_list()], ["1"])
        with self.assertRaises(ValueError):
            await self.agent.forget_account("1")
