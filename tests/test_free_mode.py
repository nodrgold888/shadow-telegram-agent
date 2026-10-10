"""No-credit routing must never silently fall back to a paid API."""
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.ai_slots import prepare_free_defaults
from shadow.assistant import ShadowAssistant
from shadow.config import AIProvider, Settings
from shadow.development import DevelopmentError, ask_ai
from shadow.image_gen import gemini_api_key
from shadow.model_routing import ordered_backup_providers
from shadow.provider_catalog import OPENROUTER_BASE_URL, XKIRO_BASE_URL, LOCAL_AI_SLOT
from tests.test_ai_work_mode import configured
from tests.test_ai_fallback import make_settings


class NoCredits(Exception):
    status_code = 402


def free_settings():
    return replace(configured(ai_work_mode="free"), openai_api_key="paid-openai-key",
                   ai_first_slot=4, ai_primary=False,
                   ai_extra_providers=configured().ai_extra_providers + (
                       AIProvider("Router Free", OPENROUTER_BASE_URL, "router-test-key", "openrouter/free", 5),
                       AIProvider("Paid disguised as free", "https://example.com/v1", "example-key", "model:free", 6),
                   ))


class FreePolicyTests(unittest.TestCase):
    def test_all_roles_exclude_paid_models_even_with_paid_model_selected_first(self):
        settings = free_settings()
        for purpose in ("chat", "selection", "development", "analysis", "review", "reasoning"):
            self.assertEqual([p.model for p in ordered_backup_providers(settings, purpose)],
                             ["qwen/qwen3.8-max:free", "openrouter/free"])
        self.assertEqual(len(settings.backup_providers), 6)  # paid configuration is preserved
        self.assertTrue(settings.ai_ready)

    def test_paid_credentials_do_not_count_as_free_readiness(self):
        settings = make_settings(ai_work_mode="free")
        self.assertFalse(settings.ai_ready)
        self.assertFalse(settings.compat_ai_ready)
        local = replace(settings, local_ai_base_url="http://ollama:11434/v1",
                        local_ai_api_key="local-only", local_ai_model="qwen-local")
        self.assertTrue(local.ai_ready)
        self.assertEqual(ordered_backup_providers(local)[0].slot, LOCAL_AI_SLOT)
        self.assertEqual(gemini_api_key(replace(settings, ai_base_url="https://generativelanguage.googleapis.com/v1beta/openai")), "")

    def test_new_config_defaults_to_free(self):
        with patch.dict(os.environ, {"AI_WORK_MODE": "", "SHADOW_STATE_FILE": ""}):
            self.assertEqual(Settings.from_env().ai_work_mode, "free")

    def test_migration_reuses_only_own_router_key_and_preserves_existing_providers(self):
        original = make_settings(ai_base_url=OPENROUTER_BASE_URL, ai_model="paid/model", ai_api_key="own-router-key")
        updated, values = prepare_free_defaults(original)
        self.assertEqual(updated.ai_work_mode, "free")
        self.assertEqual(updated.backup_providers[0], original.backup_providers[0])
        free = ordered_backup_providers(updated)[0]
        self.assertEqual((free.model, free.api_key), ("openrouter/free", "own-router-key"))
        self.assertEqual(values["AI_COST_POLICY_VERSION"], "1")
        again, values = prepare_free_defaults(updated)
        self.assertEqual(again.backup_providers, updated.backup_providers)
        self.assertNotIn("AI_API_KEY_3", values)

    def test_full_slots_still_get_free_policy_without_overwriting_keys(self):
        original = make_settings(ai_base_url=OPENROUTER_BASE_URL, ai_model="paid/model",
            ai_extra_providers=tuple(AIProvider(str(i), OPENROUTER_BASE_URL, "own-key", "paid/model", i) for i in range(2, 13)))
        updated, values = prepare_free_defaults(original)
        self.assertEqual(updated.backup_providers, original.backup_providers)
        self.assertFalse(updated.ai_ready)
        self.assertEqual(values, {"AI_WORK_MODE": "free", "AI_COST_POLICY_VERSION": "1"})


class FreeCallsTests(unittest.IsolatedAsyncioTestCase):
    def build(self, content="tushunarli", fail_xkiro=False):
        attempts = []
        constructors = []

        class Client:
            def __init__(self, **options):
                constructors.append(options)
                async def create(**request):
                    attempts.append((options.get("base_url"), request["model"]))
                    if fail_xkiro and options.get("base_url") == XKIRO_BASE_URL:
                        raise NoCredits()
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))])
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        return Client, constructors, attempts

    async def test_chat_and_complex_replies_fall_back_only_to_another_free_service(self):
        factory, constructors, attempts = self.build(fail_xkiro=True)
        with patch("shadow.assistant.AsyncOpenAI", factory):
            assistant = ShadowAssistant(free_settings())
            for message in ("salom", "debug this code and compare alternatives"):
                self.assertEqual(await assistant.reply(chat_title="Test", history="", message=message), "tushunarli")
        self.assertIsNone(assistant.client)
        self.assertEqual(len(constructors), 2)
        self.assertEqual(attempts[0], (XKIRO_BASE_URL, "qwen/qwen3.8-max:free"))
        self.assertTrue(all(model in {"qwen/qwen3.8-max:free", "openrouter/free"} for _, model in attempts))
        self.assertEqual(assistant.last_model, "openrouter/free")

    async def test_diagnostics_never_construct_or_probe_paid_clients(self):
        factory, constructors, attempts = self.build()
        with patch("shadow.assistant.AsyncOpenAI", factory):
            assistant = ShadowAssistant(free_settings())
            results = await assistant.check()
        self.assertEqual(len(results["models"]), 2)
        self.assertTrue(all(p["replied"] for p in results["models"]))
        self.assertEqual(len(constructors), 2)
        self.assertEqual(len(attempts), 2)

    async def test_development_falls_back_from_no_credits_without_paid_calls(self):
        factory, constructors, attempts = self.build(content='{"ok":true}', fail_xkiro=True)
        with patch("shadow.development.AsyncOpenAI", factory), patch.dict("shadow.development._DEV_COOLDOWNS", {}, clear=True):
            for purpose in ("selection", "development", "analysis", "review"):
                self.assertTrue((await ask_ai(free_settings(), [], 100, purpose=purpose))["ok"])
        self.assertTrue(all(o.get("base_url") in {XKIRO_BASE_URL, OPENROUTER_BASE_URL} for o in constructors))
        self.assertTrue(all(model in {"qwen/qwen3.8-max:free", "openrouter/free"} for _, model in attempts))

    async def test_no_free_provider_fails_before_any_network_call(self):
        with patch("shadow.development.AsyncOpenAI") as client:
            with self.assertRaisesRegex(DevelopmentError, "Bepul AI sozlanmagan"):
                await ask_ai(make_settings(ai_work_mode="free"), [], 100)
            client.assert_not_called()
        with patch("shadow.assistant.AsyncOpenAI") as client:
            assistant = ShadowAssistant(make_settings(ai_work_mode="free"))
            with self.assertRaisesRegex(RuntimeError, "Bepul rejim faol"):
                await assistant.reply(chat_title="Test", history="", message="salom")
            client.assert_not_called()

    async def test_explicit_professional_choice_restores_paid_clients(self):
        factory, constructors, _ = self.build()
        with patch("shadow.assistant.AsyncOpenAI", factory):
            assistant = ShadowAssistant(free_settings())
            assistant.update_settings(replace(assistant.settings, ai_work_mode="professional"))
        self.assertIsNotNone(assistant.client)
        self.assertEqual(len(assistant.compat_clients), 6)
