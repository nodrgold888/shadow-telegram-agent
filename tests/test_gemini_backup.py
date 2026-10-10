import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.assistant import chat_create
from shadow.config import Settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class Rejects(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


def client_with(side_effect):
    create = AsyncMock(side_effect=side_effect)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), create


class ChatCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_with_bounded_completion_tokens_when_provider_rejects_old_name(self):
        client, create = client_with([Rejects(400, "Unsupported parameter: max_tokens"), "ok"])
        self.assertEqual(await chat_create(client, model="m", messages=[], max_tokens=50), "ok")
        self.assertIn("max_tokens", create.call_args_list[0].kwargs)
        self.assertNotIn("max_tokens", create.call_args_list[1].kwargs)
        self.assertEqual(create.call_args_list[1].kwargs["max_completion_tokens"], 50)

    async def test_other_errors_are_not_retried(self):
        for error in (Rejects(400, "tools are not supported"), Rejects(429, "max_tokens quota"), Rejects(500, "boom")):
            client, create = client_with([error])
            with self.assertRaises(Rejects):
                await chat_create(client, model="m", messages=[], max_tokens=50)
            self.assertEqual(create.await_count, 1)

    async def test_success_passes_max_tokens_through(self):
        client, create = client_with(["fine"])
        await chat_create(client, model="m", messages=[], max_tokens=50)
        self.assertEqual(create.call_args.kwargs["max_tokens"], 50)


class GeminiConfigTests(unittest.TestCase):
    def test_gemini_openai_endpoint_is_accepted_as_a_backup_provider(self):
        env = {"AI_BASE_URL": GEMINI_URL, "AI_API_KEY": "gemini-key", "AI_MODEL": "gemini-3.8-flash",
               "AI_NAME": "Gemini", "SHADOW_STATE_FILE": ""}
        with patch.dict(os.environ, env):
            settings = Settings.from_env()
        provider = settings.backup_providers[0]
        self.assertEqual(provider.base_url, GEMINI_URL.rstrip("/"))
        self.assertEqual((provider.name, provider.model), ("Gemini", "gemini-3.8-flash"))
        self.assertTrue(settings.ai_ready)


if __name__ == "__main__":
    unittest.main()
