import asyncio
import base64
import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import mock

from shadow import image_gen
from shadow.config import AIProvider
from shadow.image_gen import (
    DEFAULT_IMAGE_MODEL, ImageGenError, extract_image, gemini_api_key, generate_image, image_model, parse_image_command,
)
from tests.test_ai_fallback import make_settings

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
B64 = base64.b64encode(PNG).decode()


class ParseTests(unittest.TestCase):
    def test_command_forms(self):
        self.assertEqual(parse_image_command("/rasm oy ustidagi mushuk"), "oy ustidagi mushuk")
        self.assertEqual(parse_image_command("  /RASM   salom  "), "salom")
        self.assertEqual(parse_image_command("/rasm@shadow_bot salom"), "salom")
        self.assertEqual(parse_image_command("/rasm"), "")
        self.assertEqual(parse_image_command("/rasm\nikki qator"), "ikki qator")

    def test_not_a_command(self):
        for text in ("rasm chiz", "salom /rasm x", "/rasmiy x", "", None):
            self.assertIsNone(parse_image_command(text))


class KeyAndModelTests(unittest.TestCase):
    def test_key_comes_from_the_gemini_backup_provider(self):
        gemini = AIProvider("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "g-key", "m")
        other = AIProvider("Other", "https://openrouter.ai/api/v1", "o-key", "m")
        settings = make_settings(ai_base_url="", ai_api_key="", ai_model="", ai_extra_providers=(other, gemini))
        self.assertEqual(gemini_api_key(settings), "g-key")

    def test_falls_back_to_env_and_otherwise_empty(self):
        settings = make_settings(ai_base_url="", ai_api_key="", ai_model="", ai_extra_providers=())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": " env-key "}):
            self.assertEqual(gemini_api_key(settings), "env-key")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertEqual(gemini_api_key(settings), "")

    def test_model_env_is_validated(self):
        with mock.patch.dict(os.environ, {"IMAGE_MODEL": "gemini-3.1-flash-image"}):
            self.assertEqual(image_model(), "gemini-3.1-flash-image")
        with mock.patch.dict(os.environ, {"IMAGE_MODEL": "bad model; rm -rf"}):
            self.assertEqual(image_model(), DEFAULT_IMAGE_MODEL)
        with mock.patch.dict(os.environ, {"IMAGE_MODEL": ""}):
            self.assertEqual(image_model(), DEFAULT_IMAGE_MODEL)


class ExtractTests(unittest.TestCase):
    def test_convenience_property(self):
        result = SimpleNamespace(output_image=SimpleNamespace(data=B64, mime_type="image/jpeg"))
        self.assertEqual(extract_image(result), (PNG, "image/jpeg"))

    def test_steps_fallback_and_raw_bytes(self):
        block = SimpleNamespace(type="image", data=PNG)
        result = SimpleNamespace(output_image=None, steps=[
            SimpleNamespace(type="thought", content=[]),
            SimpleNamespace(type="model_output", content=[SimpleNamespace(type="text", data="x"), block]),
        ])
        self.assertEqual(extract_image(result), (PNG, "image/png"))

    def test_no_image_is_an_uzbek_error(self):
        with self.assertRaises(ImageGenError) as ctx:
            extract_image(SimpleNamespace(output_image=None, steps=[]))
        self.assertIn("Rasm yaratilmadi", str(ctx.exception))


class GenerateTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_passes_key_prompt_and_model(self):
        seen = {}

        def create(api_key, prompt, model):
            seen.update(api_key=api_key, prompt=prompt, model=model)
            return SimpleNamespace(output_image=SimpleNamespace(data=B64))
        data, mime = await generate_image("k", "mushuk", "m-1", create=create)
        self.assertEqual((data, mime), (PNG, "image/png"))
        self.assertEqual(seen, {"api_key": "k", "prompt": "mushuk", "model": "m-1"})

    async def test_input_checks(self):
        for args in (("k", "  "), ("", "mushuk"), ("k", "x" * 1001)):
            with self.assertRaises(ImageGenError):
                await generate_image(*args, create=lambda *a: None)

    async def test_quota_auth_and_unknown_errors_are_mapped_without_leaking(self):
        def failing(code, text="sk-secret-12345 prompt text"):
            class Boom(Exception):
                pass
            exc = Boom(text)
            exc.code = code

            def create(*args):
                raise exc
            return create
        with self.assertRaises(ImageGenError) as quota:
            await generate_image("k", "p", create=failing(429))
        self.assertIn("kvota", str(quota.exception))
        with self.assertRaises(ImageGenError) as auth:
            await generate_image("k", "p", create=failing(403))
        self.assertIn("kalit", str(auth.exception))
        with self.assertRaises(ImageGenError) as other:
            await generate_image("k", "p", create=failing(500))
        for error in (quota, auth, other):
            self.assertNotIn("sk-secret", str(error.exception))
            self.assertNotIn("prompt text", str(error.exception))

    async def test_missing_library_and_timeout(self):
        def no_library(*args):
            raise ImportError("google")
        with self.assertRaises(ImageGenError) as ctx:
            await generate_image("k", "p", create=no_library)
        self.assertIn("kutubxona", str(ctx.exception))

        import time
        with mock.patch.object(image_gen, "IMAGE_TIMEOUT", 0.05):
            with self.assertRaises(ImageGenError) as slow:
                await generate_image("k", "p", create=lambda *a: time.sleep(0.3))
        self.assertIn("vaqtida", str(slow.exception))


class FakeClient:
    def __init__(self):
        self.sent = []
        self.actions = []

    @asynccontextmanager
    async def action(self, chat_id, kind):
        self.actions.append(kind)
        yield

    async def send_message(self, chat_id, text):
        self.sent.append(("text", text))

    async def send_file(self, chat_id, file, caption=None, reply_to=None):
        self.sent.append(("file", file.name, file.read(), caption, reply_to))


def make_agent(**env):
    from shadow.config import Settings
    from shadow.telegram_agent import TelegramAgent
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", **env}):
        agent = TelegramAgent(Settings.from_env())
    agent.client = FakeClient()
    agent._me_id = 111
    return agent


def event(text, chat_id=111, media=None):
    return SimpleNamespace(chat_id=chat_id, raw_text=text, id=7, message=SimpleNamespace(media=media))


class OwnerCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "g"})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_command_in_saved_messages_sends_the_image(self):
        agent = make_agent(GEMINI_API_KEY="g")
        fake = mock.AsyncMock(return_value=(PNG, "image/png"))
        with mock.patch("shadow.telegram_agent.generate_image", fake):
            await agent._on_owner_command(event("/rasm mushuk"))
        fake.assert_awaited_once_with("g", "mushuk")
        kind, name, data, caption, reply_to = agent.client.sent[0]
        self.assertEqual((kind, name, data, caption, reply_to), ("file", "shadow.png", PNG, "mushuk", 7))
        self.assertEqual(agent.client.actions, ["photo"])

    async def test_only_the_owners_own_saved_messages_count(self):
        agent = make_agent(GEMINI_API_KEY="g")
        for ev in (event("/rasm x", chat_id=222), event("salom"), event("/rasm x", media=object())):
            await agent._on_owner_command(ev)
        self.assertEqual(agent.client.sent, [])

    async def test_errors_are_sent_to_the_owner_only(self):
        agent = make_agent()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            await agent._on_owner_command(event("/rasm mushuk"))
        self.assertEqual(agent.client.sent[0][0], "text")
        self.assertIn("Gemini kaliti", agent.client.sent[0][1])
        agent.client.sent.clear()
        await agent._on_owner_command(event("/rasm"))
        self.assertIn("Tavsif yozing", agent.client.sent[0][1])

    async def test_a_second_command_while_busy_is_refused(self):
        agent = make_agent(GEMINI_API_KEY="g")
        await agent._image_lock.acquire()
        await agent._on_owner_command(event("/rasm mushuk"))
        self.assertIn("kuting", agent.client.sent[0][1])


if __name__ == "__main__":
    unittest.main()
