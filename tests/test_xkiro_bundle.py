import asyncio
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from shadow.ai_slots import ProviderSlotsFull, XKIRO_MODELS, prepare_xkiro_bundle
from shadow.config import AIProvider
from shadow import persist
from tests.test_account_scopes import ScopeTestCase
from tests.test_ai_fallback import make_settings

KEY = "sk-xt-test-only-example"


class BundlePlanTests(unittest.TestCase):
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
            for slot in range(2, 7)
        ))
        with self.assertRaises(ProviderSlotsFull):
            prepare_xkiro_bundle(full, KEY, XKIRO_MODELS[0][0])
        self.assertEqual(len(full.backup_providers), 6)

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
