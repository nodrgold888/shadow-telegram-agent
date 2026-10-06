import asyncio
import os
import unittest
from unittest import mock

from shadow.diagnostics import safe_error_detail


class FakeApiError(Exception):
    status_code = 404
    message = "The model `gpt-6-luna` does not exist or you do not have access to it."


class DiagnosticsTests(unittest.TestCase):
    def test_openai_reason_is_kept_with_status(self):
        detail = safe_error_detail(FakeApiError())
        self.assertTrue(detail.startswith("[404] "))
        self.assertIn("does not exist", detail)

    def test_keys_are_masked_and_text_is_truncated(self):
        error = Exception("Incorrect API key provided: sk-proj-abcdEFGH1234567890 and rnd_secretvalue123")
        detail = safe_error_detail(error)
        self.assertNotIn("abcdEFGH", detail)
        self.assertNotIn("secretvalue", detail)
        self.assertEqual(len(safe_error_detail(Exception("x" * 1000))), 240)

    def test_empty_message_falls_back_to_class_name(self):
        self.assertEqual(safe_error_detail(TimeoutError()), "TimeoutError")


class AiCheckTests(unittest.TestCase):
    def make_agent(self, **env):
        from shadow.config import Settings
        from shadow.telegram_agent import TelegramAgent
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", **env}):
            return TelegramAgent(Settings.from_env())

    def test_missing_key_is_reported(self):
        result = asyncio.run(self.make_agent().ai_check())
        self.assertEqual((result["ok"], result["error"]), (False, "openai_key_missing"))

    def test_upstream_error_is_returned_not_raised(self):
        agent = self.make_agent(OPENAI_API_KEY="sk-test")

        class Broken:
            async def check(self):
                raise FakeApiError()
        agent.assistant = Broken()
        result = asyncio.run(agent.ai_check())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "FakeApiError")
        self.assertIn("gpt-6-luna", result["detail"])

    def test_success_lists_models(self):
        agent = self.make_agent(OPENAI_API_KEY="sk-test")

        class Fine:
            async def check(self):
                return {"models": [{"model": "gpt-5-mini", "replied": True}]}

            async def chat_test(self):
                return {"ok": True, "seconds": 1.2, "provider": "openai", "preview": "Salom"}
        agent.assistant = Fine()
        result = asyncio.run(agent.ai_check())
        self.assertTrue(result["ok"])
        self.assertEqual(result["models"][0]["model"], "gpt-5-mini")
        self.assertEqual(result["chat_test"]["seconds"], 1.2)


if __name__ == "__main__":
    unittest.main()
