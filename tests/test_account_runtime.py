import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from shadow import persist, telegram_agent
from shadow.config import Settings
from shadow.panel_login import PanelLogin
from shadow.persist import account_scope, current_scope, load_local_settings, save_reply_enabled
from shadow.runtime import AccountRuntime


class Client:
    instances = []

    def __init__(self, session, *args, **kwargs):
        self.session = session
        self.key = session.save()
        self.live = False
        self.authorized = True
        self.handlers = []
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.sent = []
        Client.instances.append(self)

    async def connect(self):
        self.connect_calls += 1
        self.live = True

    def is_connected(self):
        return self.live

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        key = {"session-a": 11, "session-b": 22}.get(self.key, 33)
        return SimpleNamespace(id=key, username="one" if key == 11 else "two", first_name="Owner", last_name=None)

    async def disconnect(self):
        self.disconnect_calls += 1
        self.live = False

    def add_event_handler(self, callback, event):
        self.handlers.append((callback, event))

    def remove_event_handler(self, callback):
        self.handlers = [(handler, event) for handler, event in self.handlers if handler != callback]

    def list_event_handlers(self):
        return self.handlers

    async def send_message(self, target, text):
        self.sent.append((target, text))

    async def send_code_request(self, phone):
        return SimpleNamespace(phone_code_hash="fake-hash")

    async def sign_in(self, **kwargs):
        pass


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"
        accounts = {"11": {"label": "@one", "session": "session-a"},
                    "22": {"label": "@two", "session": "session-b"}}
        bundles = {
            "11": {"__legacy_migrated__": "1", "REPLY_ENABLED": "true", "APPROVED_CHAT_IDS": "77",
                   "OPENAI_API_KEY": "sk-account-one", "OPENAI_MODEL": "gpt-5-mini", "ALWAYS_ONLINE": "false",
                   "SHADOW_CHAT_PROFILES": json.dumps({"77": {"memory": "First account context"}})},
            "22": {"__legacy_migrated__": "1", "REPLY_ENABLED": "false", "APPROVED_CHAT_IDS": "88",
                   "OPENAI_API_KEY": "sk-account-two", "OPENAI_MODEL": "gpt-6-luna", "ALWAYS_ONLINE": "false"},
        }
        env = {key: "" for key in persist.SCOPED_DEFAULTS}
        env.update(TELEGRAM_API_ID="1", TELEGRAM_API_HASH="test-hash", TELEGRAM_SESSION="session-a",
                   TELEGRAM_ACCOUNTS=json.dumps(accounts), TELEGRAM_ACCOUNT_SETTINGS=json.dumps(bundles),
                   SHADOW_STATE_FILE=str(self.path), ADMIN_TOKEN="test-admin", GROUP_REPLY_MODE="mentions")
        self.environment = mock.patch.dict(os.environ, env)
        self.environment.start()
        persist.reset_scope()
        Client.instances = []
        self.client_patch = mock.patch.object(telegram_agent, "TelegramClient", Client)
        self.session_patch = mock.patch.object(telegram_agent, "StringSession",
                                               side_effect=lambda raw="session-c": SimpleNamespace(save=lambda: raw))
        self.client_patch.start()
        self.session_patch.start()
        self.runtime = AccountRuntime(Settings.from_env())
        await self.runtime.start()
        await self.wait_connected()

    async def wait_connected(self):
        async def wait():
            while not all(item["connected"] for item in self.runtime.account_list()):
                await asyncio.sleep(.005)
        await asyncio.wait_for(wait(), timeout=2)

    async def asyncTearDown(self):
        await self.runtime.stop()
        self.client_patch.stop()
        self.session_patch.stop()
        self.environment.stop()
        persist.reset_scope()
        self.tmp.cleanup()

    async def test_both_accounts_run_with_private_settings_memory_and_listeners(self):
        one, two = self.runtime._workers["11"], self.runtime._workers["22"]
        self.assertTrue(one.connection_alive and two.connection_alive)
        self.assertIsNot(one.client, two.client)
        self.assertIsNot(one._locks, two._locks)
        self.assertEqual(one.settings.openai_api_key, "sk-account-one")
        self.assertEqual(two.settings.openai_api_key, "sk-account-two")
        self.assertTrue(one._can_reply(77))
        self.assertFalse(two._can_reply(77))
        self.assertNotEqual(one.chat_profiles, two.chat_profiles)
        self.assertTrue(one.status()["reply_listener_registered"])
        self.assertFalse(two.status()["reply_listener_registered"])
        self.assertNotIn("sk-account", json.dumps(self.runtime.status()))
        self.assertNotIn("session-a", json.dumps(self.runtime.status()))

    async def test_switching_view_keeps_both_workers_and_inflight_contexts_alive(self):
        one, two = self.runtime._workers["11"], self.runtime._workers["22"]
        entered, release = asyncio.Event(), asyncio.Event()

        async def first_request():
            with self.runtime.request_context("11"):
                entered.set()
                await release.wait()
                self.assertIs(self.runtime.client, one.client)
                self.assertEqual(current_scope(), "11")
                await save_reply_enabled(False)

        request = asyncio.create_task(first_request())
        await entered.wait()
        with self.runtime.request_context():
            result = await self.runtime.switch_account("22")
            self.assertTrue(result["changed"])
            self.assertIs(self.runtime.client, two.client)
        release.set()
        await request
        self.assertTrue(one.connection_alive and two.connection_alive)
        self.assertEqual([one.client.disconnect_calls, two.client.disconnect_calls], [0, 0])
        saved = json.loads(self.path.read_text())
        self.assertEqual(json.loads(saved["TELEGRAM_ACCOUNT_SETTINGS"])["11"]["REPLY_ENABLED"], "false")
        self.assertEqual(saved["TELEGRAM_SESSION"], "session-b")

    async def test_concurrent_scope_reads_and_saves_do_not_mix_accounts(self):
        barrier = asyncio.Event()

        async def save(key, enabled):
            with account_scope(key):
                await barrier.wait()
                self.assertEqual(current_scope(), key)
                await save_reply_enabled(enabled)
                await asyncio.sleep(.005)
                self.assertEqual(load_local_settings()["REPLY_ENABLED"], str(enabled).lower())

        tasks = [asyncio.create_task(save("11", False)), asyncio.create_task(save("22", True))]
        barrier.set()
        await asyncio.gather(*tasks)
        bundles = json.loads(json.loads(self.path.read_text())["TELEGRAM_ACCOUNT_SETTINGS"])
        self.assertEqual((bundles["11"]["REPLY_ENABLED"], bundles["22"]["REPLY_ENABLED"]), ("false", "true"))

    async def test_concurrent_render_writes_keep_both_account_updates(self):
        arrived, release = threading.Event(), threading.Event()
        writes = []
        def put(service_id, api_key, key, value):
            if not writes and not arrived.is_set():
                arrived.set()
                release.wait(2)
            writes.append(json.loads(value))
        async def save(key, enabled):
            with account_scope(key):
                return await save_reply_enabled(enabled)
        with mock.patch.dict(os.environ, {"SHADOW_STATE_FILE": "", "RENDER_SERVICE_ID": "test-service",
                                           "RENDER_API_KEY": "test-render-key"}), \
             mock.patch.object(persist, "_put_env_var_serial", side_effect=put):
            first = asyncio.create_task(save("11", False))
            self.assertTrue(await asyncio.to_thread(arrived.wait, 1))
            second = asyncio.create_task(save("22", True))
            try:
                async def wait_for_bundle():
                    while persist._bundles["22"]["REPLY_ENABLED"] != "true":
                        await asyncio.sleep(.005)
                await asyncio.wait_for(wait_for_bundle(), 1)
            finally:
                release.set()
            self.assertEqual(await asyncio.gather(first, second), [True, True])
        self.assertEqual((writes[-1]["11"]["REPLY_ENABLED"], writes[-1]["22"]["REPLY_ENABLED"]), ("false", "true"))

    async def test_physical_disconnect_is_reported_and_recovery_restores_listener(self):
        worker = self.runtime._workers["11"]
        worker.client.live = False
        worker.client.handlers = []
        self.assertFalse(worker.status()["connected"])
        self.assertFalse(self.runtime.account_list()[0]["connected"])
        task = asyncio.create_task(worker._watchdog())
        try:
            async def wait():
                while not worker.status()["reply_runtime_ready"]:
                    await asyncio.sleep(.005)
            await asyncio.wait_for(wait(), 1)
            self.assertTrue(worker.connection_alive)
            self.assertTrue(self.runtime._workers["22"].connection_alive)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_auth_sessions_remain_on_their_own_account_after_another_switch(self):
        from shadow import app as module
        login = PanelLogin()
        cookie_one = login.verify_code(login.issue_code(account_id="11"))
        cookie_two = login._new_session(__import__('time').time())
        login.select_session_account(cookie_two, "22")
        with mock.patch.object(module, "agent", self.runtime), mock.patch.object(module, "panel_login", login):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
                for cookie, key in [(cookie_one, 11), (cookie_two, 22), (cookie_one, 11)]:
                    result = await client.get('/dashboard/api/status', cookies={'shadow_setup': cookie})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()['account_id'], key)
                result = await client.get('/dashboard/auth/info')
                self.assertEqual([item['connected'] for item in result.json()['accounts']], [True, True])
                self.assertNotIn('session-a', result.text)

    async def test_login_code_uses_existing_target_connection_without_switching(self):
        await self.runtime.send_login_code("22", "test login code")
        self.assertEqual(self.runtime.account_id, 11)
        self.assertEqual(len(Client.instances), 2)
        self.assertEqual(self.runtime._workers["22"].client.sent, [("me", "test login code")])
        self.assertEqual(self.runtime._workers["11"].client.sent, [])

    async def test_unchanged_boot_does_not_rewrite_the_render_account_registry(self):
        with mock.patch('shadow.runtime.save_accounts', new_callable=mock.AsyncMock) as save:
            worker = self.runtime._workers["11"]
            await worker._remember_account("session-a")
            save.assert_not_awaited()

    async def test_adding_account_keeps_existing_workers_and_new_settings_empty(self):
        old_clients = [worker.client for worker in self.runtime._workers.values()]
        await self.runtime.request_login_code("+998901234567")
        self.assertTrue(all(client.live for client in old_clients))
        result = await self.runtime.complete_login("123456")
        self.assertEqual(result["account_id"], 33)
        new = self.runtime._workers["33"]
        self.assertFalse(new.reply_enabled)
        self.assertFalse(new.settings.openai_api_key)
        self.assertEqual(new.chat_profiles, {})
        self.assertEqual(new.settings.approved_chat_ids, frozenset())
        self.assertTrue(all(client.live and not client.disconnect_calls for client in old_clients))
        self.assertEqual(len(self.runtime.account_list()), 3)
        self.assertTrue(new._watchdog_task and not new._watchdog_task.done())

    async def test_failed_account_setup_does_not_change_existing_clients(self):
        await self.runtime.request_login_code("+998901234567")
        with mock.patch.object(self.runtime._setup_worker._login_client, "sign_in",
                               new=mock.AsyncMock(side_effect=ValueError("invalid code"))):
            with self.assertRaises(ValueError):
                await self.runtime.complete_login("000000")
        self.assertEqual(self.runtime.account_id, 11)
        self.assertTrue(all(worker.connection_alive for worker in self.runtime._workers.values()))

    async def test_full_registry_rejects_new_account_without_deleting_existing_ones(self):
        from shadow.accounts import MAX_ACCOUNTS
        for key in range(100, 100 + MAX_ACCOUNTS - 2):
            self.runtime._registry[str(key)] = {"label": "saved", "session": "test-saved"}
        before = dict(self.runtime._registry)
        await self.runtime.request_login_code("+998901234567")
        with self.assertRaisesRegex(ValueError, "royxati tolgan"):
            await self.runtime.complete_login("123456")
        self.assertEqual(self.runtime._registry, before)
        self.assertEqual(self.runtime.account_id, 11)
        self.assertTrue(all(worker.connection_alive for worker in self.runtime._workers.values()))

    async def test_callbacks_pin_their_own_account_even_from_wrong_dispatch_context(self):
        async def callback(worker, event):
            self.assertEqual(current_scope(), worker.expected_id)
            await save_reply_enabled(event)
        with mock.patch.object(telegram_agent.TelegramAgent, "_on_message", new=callback):
            with account_scope("22"):
                await self.runtime._workers["11"]._on_message(False)
                self.assertEqual(current_scope(), "22")
        with account_scope("11"):
            self.assertEqual(load_local_settings()["REPLY_ENABLED"], "false")

    async def test_removing_one_account_only_stops_its_worker(self):
        one, two = self.runtime._workers["11"], self.runtime._workers["22"]
        await self.runtime.forget_account("22")
        self.assertTrue(one.connection_alive)
        self.assertFalse(two.connection_alive)
        self.assertIsNone(two._watchdog_task)
        self.assertEqual([account["id"] for account in self.runtime.account_list()], ["11"])

    async def test_hanging_connection_has_deadline_and_does_not_block_startup(self):
        await self.runtime.stop()
        async def hang(client):
            await asyncio.Event().wait()
        with mock.patch.object(Client, "connect", new=hang), \
             mock.patch.object(telegram_agent, "CONNECTION_TIMEOUT_SECONDS", .02):
            await asyncio.wait_for(self.runtime.start(), .1)
            await asyncio.sleep(.06)
            for worker in self.runtime._workers.values():
                self.assertFalse(worker.connection_alive)
                self.assertEqual(worker.last_error, "reconnect_timeout")
                self.assertGreater(worker._connection_failures, 0)
                self.assertFalse(worker._setup_lock.locked())

    async def test_saved_session_wrong_identity_is_rejected(self):
        worker = self.runtime._workers["11"]
        await worker.stop()
        wrong = Client(SimpleNamespace(save=lambda: "session-b"))
        with account_scope("11"):
            with self.assertRaises(ValueError):
                await worker._activate_client(wrong)
        self.assertTrue(worker.session_revoked)
        self.assertIs(self.runtime._workers["22"].client, Client.instances[1])

    async def test_background_guardian_checks_both_accounts_with_separate_preferences(self):
        from shadow.guardian import Guardian
        studio = SimpleNamespace(state_dir=Path(self.tmp.name), jobs={})
        guardian = Guardian(self.runtime, studio)
        guardian._source_check = lambda: {"id": "source", "state": "ok"}
        for key in ("11", "22"):
            with self.runtime.request_context(key):
                guardian.configure(ai_review=False, auto_repair=False)
        guardian.start()
        try:
            async def wait():
                while not guardian.scopes or any(not state["cycles"] for state in guardian.scopes.values()):
                    await asyncio.sleep(.005)
            await asyncio.wait_for(wait(), 1)
            self.assertEqual(len(guardian.scopes), 2)
            with self.runtime.request_context("22"):
                guardian.configure(enabled=False)
            with self.runtime.request_context("11"):
                _, state = guardian._scope()
                state["next_check_at"] = None
            await guardian._check_due_accounts()
            with self.runtime.request_context("11"):
                self.assertEqual(guardian.snapshot()["cycles"], 2)
            with self.runtime.request_context("22"):
                self.assertEqual(guardian.snapshot()["cycles"], 1)
                self.assertFalse(guardian.snapshot()["enabled"])
        finally:
            await guardian.close()
