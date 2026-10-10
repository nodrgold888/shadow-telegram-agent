import asyncio
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from shadow.ai_slots import ProviderSlotsFull, XKIRO_MODELS, prepare_xkiro_bundle, prepare_xkiro_defaults
from shadow.provider_catalog import XKIRO_BASE_URL
from shadow.config import AIProvider, MAX_BACKUP_PROVIDERS
from shadow import persist
from tests.test_account_scopes import ScopeTestCase
from tests.test_ai_fallback import make_settings

KEY = "sk-xt-test-only-example"


class BundlePlanTests(unittest.TestCase):
    def test_saved_key_completes_four_models_and_keeps_eight_existing_backups(self):
        providers = tuple(AIProvider("Other", "https://other.example/v1", "other-private-key", f"other{slot}", slot)
                          for slot in range(2, 8))
        qwen = AIProvider("Saved Qwen", XKIRO_BASE_URL, KEY, XKIRO_MODELS[0][0], 8)
        original = make_settings(ai_extra_providers=providers + (qwen,))
        updated, values = prepare_xkiro_defaults(original)
        self.assertEqual(len(updated.backup_providers), 11)
        for old in original.backup_providers:
            self.assertIn(old, updated.backup_providers)
        xkiro = [p for p in updated.backup_providers if p.base_url == XKIRO_BASE_URL]
        self.assertEqual({p.model for p in xkiro}, {model for model, _ in XKIRO_MODELS})
        self.assertTrue(all(p.api_key == KEY for p in xkiro))
        self.assertEqual(updated.backup_providers[0], qwen)
        self.assertEqual(values["XKIRO_DEFAULTS_VERSION"], "1")
        self.assertFalse(updated.reply_enabled)
        repeated, writes = prepare_xkiro_defaults(updated)
        self.assertEqual(repeated, updated)
        self.assertEqual(writes, {"XKIRO_DEFAULTS_VERSION": "1"})

    def test_no_xkiro_key_leaves_settings_untouched(self):
        original = make_settings()
        updated, values = prepare_xkiro_defaults(original)
        self.assertIs(updated, original)
        self.assertEqual(values, {})

    def test_existing_xkiro_keys_and_manual_choice_are_preserved(self):
        original = make_settings(ai_base_url=XKIRO_BASE_URL, ai_api_key=KEY,
                                 ai_model=XKIRO_MODELS[0][0], ai_work_mode="manual",
                                 ai_extra_providers=(AIProvider("Sonnet", XKIRO_BASE_URL,
                                                   KEY + "-second", XKIRO_MODELS[1][0], 2),))
        updated, values = prepare_xkiro_defaults(original)
        self.assertEqual(updated.ai_work_mode, "manual")
        self.assertEqual(updated.ai_primary, original.ai_primary)
        self.assertEqual(next(p.api_key for p in updated.backup_providers if p.slot == 2), KEY + "-second")
        self.assertNotIn("AI_API_KEY_2", values)

    def test_adds_all_four_preserves_other_ai_and_does_not_enable_replies(self):
        original = make_settings()
        updated, values, providers = prepare_xkiro_bundle(original, KEY, XKIRO_MODELS[0][0])
        self.assertEqual({p.model for p in providers}, {model for model, _ in XKIRO_MODELS})
        self.assertEqual(len({p.slot for p in updated.backup_providers}), 5)
        self.assertEqual(updated.ai_base_url, original.ai_base_url)
        self.assertEqual(updated.backup_providers[0].model, XKIRO_MODELS[0][0])
        self.assertFalse(updated.reply_enabled)
        self.assertEqual(updated.approved_chat_ids, original.approved_chat_ids)
        self.assertEqual(values["AI_FIRST_SLOT"], str(providers[0].slot))

    def test_repeat_save_rotates_key_and_reuses_slots(self):
        first, _, first_providers = prepare_xkiro_bundle(make_settings(), KEY, XKIRO_MODELS[0][0])
        second, _, second_providers = prepare_xkiro_bundle(first, KEY + "-new", XKIRO_MODELS[3][0])
        self.assertEqual([p.slot for p in first_providers], [p.slot for p in second_providers])
        self.assertEqual(len(second.backup_providers), 5)
        self.assertTrue(all(p.api_key == KEY + "-new" for p in second_providers))
        self.assertEqual(second.backup_providers[0].model, XKIRO_MODELS[3][0])

    def test_reuses_first_slot_and_fails_before_partial_changes_when_full(self):
        empty = make_settings(ai_base_url="", ai_api_key="", ai_model="")
        first, _, providers = prepare_xkiro_bundle(empty, KEY, XKIRO_MODELS[0][0])
        self.assertEqual(providers[0].slot, 1)
        self.assertEqual(first.ai_api_key, KEY)
        full = make_settings(ai_extra_providers=tuple(
            AIProvider("Other", "https://other.example/v1", "example-key", f"model{slot}", slot)
            for slot in range(2, MAX_BACKUP_PROVIDERS - 1)
        ))
        with self.assertRaises(ProviderSlotsFull):
            prepare_xkiro_bundle(full, KEY, XKIRO_MODELS[0][0])
        self.assertEqual(len(full.backup_providers), MAX_BACKUP_PROVIDERS - 2)

    def test_rejects_invalid_key_or_primary(self):
        for key, primary in ((None, XKIRO_MODELS[0][0]), ("short", XKIRO_MODELS[0][0]),
                             (KEY, []), (KEY, "anthropic/claude-sonnet-5.5")):
            with self.subTest(primary=primary), self.assertRaises(ValueError):
                prepare_xkiro_bundle(make_settings(), key, primary)


class BundlePersistenceTests(ScopeTestCase):
    async def test_whole_bundle_is_one_write_and_private_to_active_account(self):
        await persist.enter_scope("1", migrate_globals=False)
        _, values, _ = prepare_xkiro_bundle(make_settings(), KEY, XKIRO_MODELS[0][0])
        with patch("shadow.persist._save_local", wraps=persist._save_local) as write:
            self.assertTrue(await persist.save_env_vars(values))
            self.assertEqual(write.call_count, 1)
            self.assertEqual(write.call_args.args[0], "TELEGRAM_ACCOUNT_SETTINGS")
        self.assertEqual(persist.load_local_settings()["AI_API_KEY_2"], KEY)
        await persist.enter_scope("2", migrate_globals=False)
        self.assertEqual(persist.load_local_settings()["AI_API_KEY_2"], "")
        await persist.enter_scope("1", migrate_globals=False)
        with patch.dict(os.environ, {"SHADOW_STATE_FILE": "", "RENDER_API_KEY": "example", "RENDER_SERVICE_ID": "srv"}), \
                patch("shadow.persist._put_env_var") as put:
            self.assertTrue(await persist.save_env_vars(values))
            self.assertEqual(put.call_count, 1)
            self.assertEqual(put.call_args.args[2], "TELEGRAM_ACCOUNT_SETTINGS")
            self.assertEqual(json.loads(put.call_args.args[3])["1"]["AI_API_KEY_2"], KEY)


class BundleApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_xkiro_provider_save_installs_defaults_in_one_write(self):
        module = importlib.import_module("shadow.app")
        fake_agent = SimpleNamespace(settings=make_settings(), assistant=None, _setup_lock=asyncio.Lock())
        with patch.object(module, "agent", fake_agent), \
                patch.object(module, "settings", SimpleNamespace(setup_token="test", admin_token="")), \
                patch.object(module, "save_env_vars", AsyncMock(return_value=True)) as save, patch.dict(os.environ, {}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
                result = await client.post("/dashboard/api/ai-providers", json={"api_key": KEY,
                    "base_url": XKIRO_BASE_URL, "model": XKIRO_MODELS[0][0], "name": "xKiro"},
                    headers={"Authorization": "Bearer test"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["xkiro_defaults"])
            self.assertEqual(len([p for p in fake_agent.settings.backup_providers
                                  if p.base_url == XKIRO_BASE_URL]), 4)
            self.assertNotIn(KEY, result.text)
            save.assert_awaited_once()
            self.assertEqual(save.call_args.args[0]["XKIRO_DEFAULTS_VERSION"], "1")

    async def test_auth_and_single_save_without_returning_key(self):
        module = importlib.import_module("shadow.app")
        fake_agent = SimpleNamespace(settings=make_settings(), assistant=None, _setup_lock=asyncio.Lock())
        with patch.object(module, "agent", fake_agent), \
                patch.object(module, "settings", SimpleNamespace(setup_token="test", admin_token="")), \
                patch.object(module, "save_env_vars", AsyncMock(return_value=True)) as save, \
                patch.object(module, "ShadowAssistant", Mock()) as assistant, patch.dict(os.environ, {}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
                denied = await client.post("/dashboard/api/ai-providers/xkiro-bundle", json={"api_key": KEY})
                self.assertEqual(denied.status_code, 401)
                save.assert_not_awaited()
                result = await client.post("/dashboard/api/ai-providers/xkiro-bundle", json={"api_key": KEY},
                                           headers={"Authorization": "Bearer test"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(len(result.json()["providers"]), 4)
            self.assertNotIn(KEY, result.text)
            save.assert_awaited_once()
            assistant.assert_called_once_with(fake_agent.settings)
            self.assertTrue(fake_agent.settings.ai_ready)


if __name__ == "__main__":
    unittest.main()
