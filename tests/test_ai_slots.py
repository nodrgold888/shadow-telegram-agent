import os
import unittest
from unittest.mock import patch

from shadow.ai_slots import apply_provider, free_slot, parse_provider, provider_env, remove_slot, set_first
from shadow.config import _first_slot
from shadow.persist import delete_env_vars, save_env_vars
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
        for n in range(2, 9):
            everything.update({f"AI_BASE_URL_{n}": "https://b", f"AI_API_KEY_{n}": "k", f"AI_MODEL_{n}": "m"})
            self.assertEqual(free_slot(everything), n + 1 if n < 8 else None)

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
        self.assertEqual((two.ai_extra_providers[-1].model, two.ai_extra_providers[-1].slot), (p.model, 2))
        self.assertTrue(two.compat_ai_ready)

    def test_remove_extra_slot_keeps_the_others(self):
        base = make_settings()
        p = parse_provider(body())
        settings = apply_provider(apply_provider(base, 2, p), 3, parse_provider(body(name="Other")))
        env = {"AI_MODEL_2": "m", "AI_API_KEY_2": "k", "AI_BASE_URL_2": "u", "AI_NAME_2": "n", "AI_MODEL_3": "keep"}
        out = remove_slot(settings, 2, env)
        self.assertEqual([x.slot for x in out.ai_extra_providers], [3])
        self.assertEqual(env, {"AI_MODEL_3": "keep"})

    def test_remove_first_slot_clears_the_primary_backup(self):
        settings = apply_provider(make_settings(), 1, parse_provider(body()))
        self.assertTrue(settings.compat_ai_ready)
        env = {"AI_MODEL": "m"}
        out = remove_slot(settings, 1, env)
        self.assertEqual((out.ai_model, out.ai_api_key, env), ("", "", {}))

    def test_status_slot_numbers(self):
        settings = apply_provider(make_settings(), 3, parse_provider(body()))
        self.assertEqual([p.slot for p in settings.backup_providers][-1], 3)

    def test_first_slot_env_parsing(self):
        self.assertEqual([_first_slot(x) for x in ("", "abc", "0", "3", " 2 ", "9", "-1")], [0, 0, 0, 3, 2, 0, 0])

    def test_set_first_puts_the_provider_in_front_and_before_openai(self):
        settings = apply_provider(apply_provider(make_settings(), 2, parse_provider(body(name="A"))), 3, parse_provider(body(name="B")))
        out, env = set_first(settings, 3)
        self.assertEqual(out.backup_providers[0].name, "B")
        self.assertTrue(out.ai_primary)
        self.assertEqual((out.ai_first_slot, env), (3, {"AI_PRIMARY": "true", "AI_FIRST_SLOT": "3"}))
        # the chosen provider leads; the others keep their relative order
        self.assertEqual([p.slot for p in out.backup_providers], [3, 1, 2])

    def test_set_first_zero_gives_openai_back_the_lead(self):
        settings = apply_provider(make_settings(), 2, parse_provider(body()))
        out, env = set_first(settings, 2)
        back, env = set_first(out, 0)
        self.assertEqual((back.ai_primary, back.ai_first_slot, env), (False, 0, {"AI_PRIMARY": "false", "AI_FIRST_SLOT": "0"}))

    def test_set_first_rejects_an_empty_slot(self):
        with self.assertRaises(ValueError):
            set_first(make_settings(), 5)

    def test_removing_the_first_provider_clears_the_preference(self):
        settings = apply_provider(make_settings(), 2, parse_provider(body()))
        out, _ = set_first(settings, 2)
        env = {"AI_FIRST_SLOT": "2", "AI_MODEL_2": "m"}
        after = remove_slot(out, 2, env)
        self.assertEqual((after.ai_first_slot, env), (0, {}))

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


class DeleteEnvVarsTest(unittest.IsolatedAsyncioTestCase):
    async def test_not_configured_returns_false(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(await delete_env_vars(["A"]))

    async def test_deletes_each_key_and_reports_failure(self):
        env = {"RENDER_API_KEY": "r", "RENDER_SERVICE_ID": "srv"}
        with patch.dict(os.environ, env, clear=True), patch("shadow.persist._delete_env_var") as gone:
            self.assertTrue(await delete_env_vars(["A", "B"]))
            self.assertEqual([c.args[2] for c in gone.call_args_list], ["A", "B"])
            gone.side_effect = OSError("boom")
            self.assertFalse(await delete_env_vars(["A"]))


if __name__ == "__main__":
    unittest.main()
