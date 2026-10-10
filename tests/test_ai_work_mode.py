import asyncio
import importlib
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from shadow.ai_slots import XKIRO_MODELS, prepare_xkiro_bundle
from shadow.assistant import ShadowAssistant
from shadow.config import Settings
from shadow.development import DevelopmentError, ask_ai
from shadow.model_routing import ordered_backup_providers
from shadow import persist
from tests.test_account_scopes import ScopeTestCase
from tests.test_ai_fallback import make_settings


def configured(**options):
    base = make_settings(openai_api_key="", ai_base_url="", ai_api_key="", ai_model="", **options)
    return prepare_xkiro_bundle(base, "sk-xt-runtime-test-only", XKIRO_MODELS[0][0])[0]


class RoutingTests(unittest.TestCase):
    def test_professional_uses_different_models_for_chat_code_analysis_and_review(self):
        settings = configured()
        for purpose, expected in (("chat", 0), ("development", 1), ("analysis", 3), ("review", 2)):
            self.assertEqual(ordered_backup_providers(settings, purpose)[0].model, XKIRO_MODELS[expected][0])

    def test_economy_and_manual_preserve_the_owners_control(self):
        settings = configured(ai_work_mode="economy")
        for purpose in ("chat", "development", "analysis", "review"):
            self.assertEqual(ordered_backup_providers(settings, purpose)[0].model, XKIRO_MODELS[0][0])
        settings = replace(settings, ai_work_mode="manual", ai_first_slot=4)
        self.assertEqual(ordered_backup_providers(settings, "review"), settings.backup_providers)

    def test_local_host_and_unrelated_providers_keep_their_positions(self):
        base = make_settings(local_ai_base_url="http://ollama:11434/v1", local_ai_api_key="local-example", local_ai_model="qwen-local")
        settings = prepare_xkiro_bundle(base, "sk-xt-runtime-test-only", XKIRO_MODELS[0][0])[0]
        before = [(i, p) for i, p in enumerate(settings.backup_providers) if p.base_url != "https://api.xkiro.com/v1"]
        after = ordered_backup_providers(settings, "development")
        for i, provider in before:
            self.assertEqual(after[i], provider)


class ModePersistenceTests(ScopeTestCase):
    async def test_modes_are_private_and_survive_restarting_scope(self):
        await persist.enter_scope("1", migrate_globals=False)
        self.assertTrue(await persist.save_env_vars({"AI_WORK_MODE": "economy"}))
        self.assertEqual(Settings.from_env().ai_work_mode, "economy")
        await persist.enter_scope("2", migrate_globals=False)
        self.assertEqual(Settings.from_env().ai_work_mode, "professional")
        await persist.enter_scope("1", migrate_globals=False)
        self.assertEqual(Settings.from_env().ai_work_mode, "economy")


class DevelopmentRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_selection_balance_fallback_and_cooldown(self):
        attempted = []
        report = []

        class NoBalance(Exception):
            status_code = 402

        class Client:
            def __init__(self, **options):
                async def create(**request):
                    attempted.append(request["model"])
                    if request["model"] == XKIRO_MODELS[1][0]:
                        raise NoBalance()
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        with patch("shadow.development.AsyncOpenAI", Client), patch.dict("shadow.development._DEV_COOLDOWNS", {}, clear=True):
            result = await ask_ai(configured(), [], 200, purpose="development", on_success=lambda *x: report.append(x))
            self.assertTrue(result["ok"])
            self.assertEqual(attempted, [XKIRO_MODELS[1][0], XKIRO_MODELS[3][0]])
            self.assertEqual(report[0][1], XKIRO_MODELS[3][0])
            attempted.clear()
            await ask_ai(configured(), [], 200, purpose="development")
            self.assertEqual(attempted, [XKIRO_MODELS[3][0]])
            attempted.clear()
            await ask_ai(configured(), [], 200, purpose="review")
            self.assertEqual(attempted, [XKIRO_MODELS[2][0]])

    async def test_slow_requests_cannot_exceed_the_stage_budget(self):
        class Client:
            def __init__(self, **options):
                async def create(**request):
                    await asyncio.sleep(1)
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        with patch("shadow.development.AsyncOpenAI", Client), patch("shadow.development.DEV_CHAIN_BUDGET", 0.01), \
                patch.dict("shadow.development._DEV_COOLDOWNS", {}, clear=True):
            with self.assertRaises(DevelopmentError):
                await ask_ai(configured(), [], 200)


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_provider_chain_has_a_hard_time_budget(self):
        with patch("shadow.assistant.AsyncOpenAI"):
            assistant = ShadowAssistant(configured())
        attempted = []

        async def slow(provider, client):
            attempted.append(provider.model)
            await asyncio.sleep(1)

        with patch("shadow.assistant.CHAIN_BUDGET", 0.01):
            with self.assertRaises(TimeoutError):
                await assistant._try_providers(slow)
        self.assertEqual(attempted, [XKIRO_MODELS[0][0]])

    async def test_telegram_small_talk_and_complex_work_use_different_models(self):
        attempted = []

        class Client:
            def __init__(self, **options):
                async def create(**request):
                    attempted.append(request["model"])
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="tushunarli", tool_calls=[]))])
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        with patch("shadow.assistant.AsyncOpenAI", Client):
            assistant = ShadowAssistant(configured())
        await assistant.reply(chat_title="Test", history="", message="salom")
        self.assertEqual(attempted, [XKIRO_MODELS[0][0]])
        attempted.clear()
        await assistant.reply(chat_title="Test", history="", message="Debug this issue and compare the alternative designs")
        self.assertEqual(attempted, [XKIRO_MODELS[2][0]])
        self.assertEqual(assistant.last_model, XKIRO_MODELS[2][0])

    async def test_timeout_is_reported_per_model_without_blocking_healthy_models(self):
        async def slow(**request):
            await asyncio.sleep(1)

        fast_reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="salom"))])
        with patch("shadow.assistant.AsyncOpenAI"):
            assistant = ShadowAssistant(configured())
        assistant.compat_clients = [(p, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=slow if i == 0 else AsyncMock(return_value=fast_reply)
        )))) for i, p in enumerate(assistant.settings.backup_providers)]
        with patch("shadow.assistant.CHECK_TIMEOUT", 0.01):
            result = await assistant.check()
        self.assertEqual(len(result["models"]), 4)
        self.assertEqual(result["models"][0]["error"], "TimeoutError")
        self.assertTrue(all(m["replied"] for m in result["models"][1:]))
        self.assertTrue(all("seconds" in m for m in result["models"]))
        self.assertTrue(assistant._cooling(f"{assistant.compat_clients[0][0].slot}:{assistant.compat_clients[0][0].name}"))


class WorkModeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_validation_and_routes_do_not_expose_credentials(self):
        module = importlib.import_module("shadow.app")
        fake_agent = SimpleNamespace(settings=configured(), assistant=Mock(), _setup_lock=asyncio.Lock())
        with patch.object(module, "agent", fake_agent), \
                patch.object(module, "settings", SimpleNamespace(setup_token="test", admin_token="")), \
                patch.object(module, "save_env_vars", AsyncMock(return_value=True)) as save, patch.dict(os.environ, {}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
                for method in ("GET", "POST"):
                    result = await client.request(method, "/dashboard/api/ai-work-mode", json={"mode": "economy"})
                    self.assertEqual(result.status_code, 401)
                client.headers["Authorization"] = "Bearer test"
                bad = await client.post("/dashboard/api/ai-work-mode", json={"mode": []})
                self.assertEqual(bad.status_code, 400)
                save.assert_not_awaited()
                result = await client.post("/dashboard/api/ai-work-mode", json={"mode": "economy"})
                self.assertTrue(result.json()["persisted"])
                routes = await client.get("/dashboard/api/ai-work-mode")
                self.assertNotIn("sk-xt-runtime-test-only", routes.text)
                self.assertEqual(routes.json()["routes"]["development"][0]["model"], XKIRO_MODELS[0][0])
            self.assertEqual(fake_agent.settings.ai_work_mode, "economy")
            self.assertFalse(fake_agent.settings.reply_enabled)
            save.assert_awaited_once_with({"AI_WORK_MODE": "economy"})
