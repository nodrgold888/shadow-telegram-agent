import os
import unittest
from unittest.mock import patch

from shadow.ai_slots import apply_provider, free_slot, parse_provider, provider_env
from shadow.persist import save_env_vars
from tests.test_ai_fallback import make_settings


def body(**over):
    data = {"name": "Mercury", "base_url": "https://openrouter.ai/api/v1/", "model": "inception/mercury-decide:free", "api_key": "sk-or-v1-abcdef"}
    data.update(over)
    return data


class AiSlotsTest(unittest.TestCase):
    def test_parse_accepts_openrouter_free_model(self):
        p = parse_provider(body())
        self.assertEqual((p.name, p.base_url, p.model), ("Mercury", "https://openrouter.ai/api/v1", "inception/mercury-decide:free"))

    def test_parse_rejects_bad_input(self):
        for bad in (
            body(base_url="http://example.com"), body(base_url=""), body(api_key="short"),
            body(api_key="has space in it"), body(model="bad model"), body(model=""), "x", body(name=5),
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                parse_provider(bad)

    def test_name_defaults_to_model(self):
        self.assertEqual(parse_provider(body(name="")).name, "mercury-decide:free")

    def test_free_slot_picks_first_incomplete(self):
        self.assertEqual(free_slot({}), 1)
        full1 = {"AI_BASE_URL": "https://a", "AI_API_KEY": "k", "AI_MODEL": "m"}
        self.assertEqual(free_slot(full1), 2)
        half2 = {**full1, "AI_BASE_URL_2": "https://b"}
        self.assertEqual(free_slot(half2), 2)
        everything = dict(full1)
        for n in range(2, 6):
            everything.update({f"AI_BASE_URL_{n}": "https://b", f"AI_API_KEY_{n}": "k", f"AI_MODEL_{n}": "m"})
        self.assertIsNone(free_slot(everything))

    def test_provider_env_names(self):
        p = parse_provider(body())
        self.assertEqual(set(provider_env(1, p)), {"AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_NAME"})
        self.assertEqual(set(provider_env(3, p)), {"AI_BASE_URL_3", "AI_API_KEY_3", "AI_MODEL_3", "AI_NAME_3"})

    def test_apply_provider(self):
        settings = make_settings()
        p = parse_provider(body())
        one = apply_provider(settings, 1, p)
        self.assertEqual(one.ai_model, p.model)
        two = apply_provider(settings, 2, p)
        self.assertEqual(two.ai_extra_providers[-1], p)
        self.assertTrue(two.compat_ai_ready)

    def test_key_not_in_repr(self):
        self.assertNotIn("sk-or-v1", repr(parse_provider(body())))


class SaveEnvVarsTest(unittest.IsolatedAsyncioTestCase):
    async def test_not_configured_returns_false(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(await save_env_vars({"A": "1"}))

    async def test_writes_each_variable_and_survives_errors(self):
        env = {"RENDER_API_KEY": "r", "RENDER_SERVICE_ID": "srv"}
        with patch.dict(os.environ, env, clear=True), patch("shadow.persist._put_env_var") as put:
            self.assertTrue(await save_env_vars({"A": "1", "B": "2"}))
            self.assertEqual([c.args[2] for c in put.call_args_list], ["A", "B"])
            put.side_effect = OSError("boom")
            self.assertFalse(await save_env_vars({"A": "1"}))


if __name__ == "__main__":
    unittest.main()
