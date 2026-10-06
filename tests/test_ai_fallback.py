import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.assistant import ShadowAssistant, should_fall_back
from shadow.config import Settings


class QuotaError(Exception):
    status_code = 429
    message = "You have no credits remaining."


class BadRequest(Exception):
    status_code = 400
    message = "bad request"


def make_settings(**overrides):
    base = dict(
        telegram_api_id=None, telegram_api_hash="", telegram_session="", openai_api_key="sk-openai",
        openai_model="gpt-5-mini", approved_chat_ids=frozenset(), reply_enabled=False,
        group_reply_mode="mentions", context_messages=12, max_reply_chars=3800, admin_token="", setup_token="",
        ai_base_url="https://api.backup.example/v1", ai_api_key="backup-key", ai_model="backup-model",
    )
    base.update(overrides)
    return Settings(**base)


def build(settings, openai_error=None):
    openai_client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(
        side_effect=openai_error, return_value=SimpleNamespace(output=[], output_text="OpenAI javobi"))))
    compat_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Zaxira javobi"))])))))

    def factory(**kwargs):
        return compat_client if "base_url" in kwargs else openai_client
    with patch("shadow.assistant.AsyncOpenAI", side_effect=factory):
        assistant = ShadowAssistant(settings)
    return assistant, openai_client, compat_client


class FallbackTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, assistant):
        return await assistant.reply_with_files(
            chat_title="Chat", history="", message="Salom", directory=Path("."),
        )

    async def test_quota_error_falls_back_to_backup_provider(self):
        assistant, openai_client, compat = build(make_settings(), openai_error=QuotaError())
        answer, files = await self.ask(assistant)
        self.assertEqual((answer, files), ("Zaxira javobi", []))
        self.assertEqual(assistant.last_provider, "zaxira")
        self.assertEqual(compat.chat.completions.create.call_args.kwargs["model"], "backup-model")

    async def test_healthy_openai_is_used_first(self):
        assistant, _, compat = build(make_settings())
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "OpenAI javobi")
        self.assertEqual(assistant.last_provider, "openai")
        compat.chat.completions.create.assert_not_called()

    async def test_primary_flag_skips_openai(self):
        assistant, openai_client, _ = build(make_settings(ai_primary=True))
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Zaxira javobi")
        openai_client.responses.create.assert_not_called()

    async def test_backup_only_setup_works_without_openai_key(self):
        assistant, openai_client, _ = build(make_settings(openai_api_key=""))
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Zaxira javobi")
        openai_client.responses.create.assert_not_called()

    async def test_other_errors_are_not_masked(self):
        assistant, _, compat = build(make_settings(), openai_error=BadRequest())
        with self.assertRaises(BadRequest):
            await self.ask(assistant)
        compat.chat.completions.create.assert_not_called()

    async def test_without_backup_the_error_is_raised(self):
        assistant, _, _ = build(make_settings(ai_base_url="", ai_api_key="", ai_model=""), openai_error=QuotaError())
        with self.assertRaises(QuotaError):
            await self.ask(assistant)

    async def test_public_bank_reply_also_falls_back(self):
        assistant, _, _ = build(make_settings(), openai_error=QuotaError())
        self.assertEqual(await assistant.reply_public_bank(history="", message="kredit"), "Zaxira javobi")

    async def test_check_reports_each_model_without_raising(self):
        assistant, _, _ = build(make_settings(), openai_error=QuotaError())
        result = await assistant.check()
        by_model = {m["model"]: m for m in result["models"]}
        self.assertFalse(by_model["gpt-5-mini"]["replied"])
        self.assertEqual(by_model["gpt-5-mini"]["error"], "QuotaError")
        self.assertTrue(by_model["backup-model (zaxira)"]["replied"])


class HelperAndConfigTests(unittest.TestCase):
    def test_fallback_classification(self):
        self.assertTrue(should_fall_back(QuotaError()))
        self.assertFalse(should_fall_back(BadRequest()))
        self.assertTrue(should_fall_back(type("APIConnectionError", (Exception,), {})()))

    def test_backup_url_must_be_https(self):
        env = {"AI_BASE_URL": "http://evil.example/v1", "SHADOW_STATE_FILE": ""}
        with patch.dict(os.environ, env):
            with self.assertRaises(ValueError):
                Settings.from_env()
        env = {"AI_BASE_URL": "https://api.backup.example/v1/", "AI_API_KEY": "k", "AI_MODEL": "m", "SHADOW_STATE_FILE": ""}
        with patch.dict(os.environ, env):
            settings = Settings.from_env()
        self.assertEqual(settings.ai_base_url, "https://api.backup.example/v1")
        self.assertTrue(settings.compat_ai_ready and settings.ai_ready)

    def test_ai_ready_with_either_provider(self):
        self.assertFalse(make_settings(openai_api_key="", ai_base_url="").ai_ready)
        self.assertTrue(make_settings(openai_api_key="").ai_ready)


if __name__ == "__main__":
    unittest.main()
