import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shadow import persist
from shadow.accounts import dump_accounts, remember
from shadow.config import Settings
from shadow.persist import (
    enter_scope, load_friend_chats, load_local_settings, reset_scope, save_approved_chats, save_friend_chats, save_reply_enabled,
)
from tests.test_greeting import make_agent

CLEAN_ENV = {key: "" for key in (*persist.SCOPED_DEFAULTS, "TELEGRAM_SESSION", "TELEGRAM_ACCOUNTS", "TELEGRAM_ACCOUNT_SETTINGS", "OPENAI_API_KEY")}
CLEAN_ENV["GROUP_REPLY_MODE"] = "mentions"


class ScopeTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.json"
        self.env = mock.patch.dict(os.environ, {**CLEAN_ENV, "SHADOW_STATE_FILE": str(self.path)})
        self.env.start()
        reset_scope()

    def tearDown(self):
        reset_scope()
        self.env.stop()
        self.directory.cleanup()

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class PersistScopeTests(ScopeTestCase):
    async def test_without_a_scope_nothing_changes(self):
        await save_approved_chats("5")
        self.assertEqual(self.saved(), {"APPROVED_CHAT_IDS": "5"})
        self.assertEqual(load_local_settings()["APPROVED_CHAT_IDS"], "5")

    async def test_first_account_keeps_what_it_had_and_writes_go_to_its_bundle(self):
        await save_approved_chats("5")
        await save_reply_enabled(True)
        await enter_scope("1", migrate_globals=True)
        settings = load_local_settings()
        self.assertEqual((settings["APPROVED_CHAT_IDS"], settings["REPLY_ENABLED"]), ("5", "true"))
        await save_approved_chats("7")
        saved = self.saved()
        self.assertEqual(saved["APPROVED_CHAT_IDS"], "5")  # the global value is left alone
        self.assertEqual(json.loads(saved["TELEGRAM_ACCOUNT_SETTINGS"])["1"]["APPROVED_CHAT_IDS"], "7")
        self.assertEqual(load_local_settings()["APPROVED_CHAT_IDS"], "7")

    async def test_another_account_starts_blank_and_each_keeps_its_own_values(self):
        await save_approved_chats("5")
        await save_reply_enabled(True)
        await save_friend_chats("9:oila")
        await enter_scope("1", migrate_globals=True)
        await enter_scope("2", migrate_globals=False)
        blank = load_local_settings()
        self.assertEqual((blank["APPROVED_CHAT_IDS"], blank["REPLY_ENABLED"], blank["FRIEND_CHAT_IDS"]), ("", "false", ""))
        self.assertEqual(load_friend_chats(), {})
        await save_approved_chats("8")
        await save_friend_chats("4:ish")
        await enter_scope("1", migrate_globals=False)
        back = load_local_settings()
        self.assertEqual((back["APPROVED_CHAT_IDS"], back["REPLY_ENABLED"]), ("5", "true"))
        self.assertEqual(load_friend_chats(), {9: "oila"})
        await enter_scope("2", migrate_globals=False)
        self.assertEqual(load_local_settings()["APPROVED_CHAT_IDS"], "8")
        self.assertEqual(load_friend_chats(), {4: "ish"})

    async def test_boot_picks_the_account_whose_session_is_live(self):
        await save_approved_chats("5")
        await enter_scope("1", migrate_globals=True)
        await save_approved_chats("7")
        await enter_scope("2", migrate_globals=False)
        await save_approved_chats("8")
        accounts = remember(remember({}, 1, "@a", "session-a"), 2, "@b", "session-b")
        state = self.saved()
        state.update({"TELEGRAM_ACCOUNTS": dump_accounts(accounts), "TELEGRAM_SESSION": "session-a"})
        self.path.write_text(json.dumps(state), encoding="utf-8")
        reset_scope()
        self.assertEqual(Settings.from_env().approved_chat_ids, frozenset({7}))
        state["TELEGRAM_SESSION"] = "session-b"
        self.path.write_text(json.dumps(state), encoding="utf-8")
        reset_scope()
        self.assertEqual(Settings.from_env().approved_chat_ids, frozenset({8}))

    async def test_a_session_that_is_not_saved_stays_in_legacy_mode(self):
        await save_approved_chats("5")
        state = self.saved()
        state["TELEGRAM_SESSION"] = "unknown"
        self.path.write_text(json.dumps(state), encoding="utf-8")
        reset_scope()
        self.assertEqual(Settings.from_env().approved_chat_ids, frozenset({5}))


class AgentScopeTests(ScopeTestCase):
    async def test_switching_accounts_swaps_allowlist_friends_memory_and_switches(self):
        await save_approved_chats("5")
        await save_reply_enabled(True)
        await save_friend_chats("9:oila")
        agent = make_agent()
        agent.accounts = {}
        with mock.patch("shadow.telegram_agent.ShadowAssistant") as assistant:
            await agent._enter_account_scope(1)
            self.assertEqual(agent.settings.approved_chat_ids, frozenset({5}))
            self.assertEqual((agent.reply_enabled, agent.friend_ids), (True, {9: "oila"}))
            self.assertIsNotNone(agent.assistant)
            agent.accounts = remember({}, 1, "@a", "session-a")
            await agent._enter_account_scope(2)
            self.assertEqual(agent.settings.approved_chat_ids, frozenset())
            self.assertEqual((agent.reply_enabled, agent.friend_ids, agent.chat_profiles), (False, {}, {}))
            self.assertIsNone(agent.assistant)
            self.assertEqual(agent.greeting.reply_count, 0)
            await agent._enter_account_scope(1)
            self.assertEqual((agent.settings.approved_chat_ids, agent.reply_enabled, agent.friend_ids), (frozenset({5}), True, {9: "oila"}))
            assistant.assert_called()

    async def test_entering_the_same_account_again_keeps_its_saved_data(self):
        agent = make_agent()
        agent.accounts = {}
        with mock.patch("shadow.telegram_agent.ShadowAssistant"):
            await agent._enter_account_scope(1)
            await save_friend_chats("1:ish")
            await agent._enter_account_scope(1)
        self.assertEqual(agent.friend_ids, {1: "ish"})


class FakeTg:
    def __init__(self, user_id, username):
        self.user_id, self.username, self.handlers = user_id, username, []

    async def get_me(self):
        return mock.Mock(id=self.user_id, username=self.username, first_name=None, last_name=None)

    def add_event_handler(self, callback, event):
        self.handlers.append(callback.__name__)

    def remove_event_handler(self, callback, event=None):
        if callback.__name__ in self.handlers:
            self.handlers.remove(callback.__name__)

    async def disconnect(self):
        return None


class ActivateClientTests(ScopeTestCase):
    async def test_logging_in_a_second_account_does_not_inherit_the_first_ones_replies(self):
        await save_approved_chats("5")
        await save_reply_enabled(True)
        agent = make_agent()
        agent.accounts = {}
        agent.presence = mock.Mock()
        agent.client = None
        with mock.patch("shadow.telegram_agent.ShadowAssistant"):
            first = FakeTg(1, "first")
            await agent._activate_client(first)
            self.assertEqual((agent.account_label, agent.reply_enabled), ("@first", True))
            self.assertIn("_on_message", first.handlers)
            agent.accounts = remember({}, 1, "@first", "session-a")
            second = FakeTg(2, "second")
            await agent._activate_client(second)
            self.assertEqual((agent.account_label, agent.reply_enabled, agent.settings.approved_chat_ids), ("@second", False, frozenset()))
            self.assertNotIn("_on_message", second.handlers)  # no auto-replies until the owner sets this account up
            self.assertIn("_on_owner_command", second.handlers)
            self.assertNotIn("_on_message", first.handlers)  # the old account's listener was removed
            third = FakeTg(1, "first")
            await agent._activate_client(third)
            self.assertEqual((agent.reply_enabled, agent.settings.approved_chat_ids), (True, frozenset({5})))
            self.assertIn("_on_message", third.handlers)
