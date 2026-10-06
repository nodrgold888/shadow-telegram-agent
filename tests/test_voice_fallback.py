import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from shadow import gemini_audio
from shadow.assistant import ShadowAssistant
from shadow.config import AIProvider
from shadow.gemini_audio import DEFAULT_AUDIO_MODEL, gemini_models, transcribe
from tests.test_ai_fallback import QuotaError, make_settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def audio_file(size=64):
    handle = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    handle.write(b"x" * size)
    handle.close()
    return Path(handle.name)


def gemini_settings(**over):
    gemini = AIProvider("Gemini Lite", GEMINI_URL, "g-key", "gemini-flash-lite-latest", 2)
    other = AIProvider("Other", "https://openrouter.ai/api/v1", "o-key", "x/y:free", 3)
    return make_settings(ai_base_url="", ai_api_key="", ai_model="", ai_extra_providers=(other, gemini), **over)


class ModelsTests(unittest.TestCase):
    def test_gemini_provider_models_first_then_default(self):
        settings = gemini_settings()
        self.assertEqual(gemini_models(settings), ["gemini-flash-lite-latest"])
        settings = make_settings(
            ai_base_url=GEMINI_URL, ai_api_key="k", ai_model="gemini-3.6-flash", ai_extra_providers=(),
        )
        self.assertEqual(gemini_models(settings), ["gemini-3.6-flash", DEFAULT_AUDIO_MODEL])

    def test_no_gemini_provider_uses_the_default(self):
        settings = make_settings(ai_base_url="", ai_api_key="", ai_model="", ai_extra_providers=())
        self.assertEqual(gemini_models(settings), [DEFAULT_AUDIO_MODEL])


class TranscribeTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_non_empty_transcript_wins(self):
        calls = []

        def call(key, model, data, mime):
            calls.append((key, model, mime))
            return "" if model == "a" else "salom dunyo"
        text = await transcribe("k", ["a", "b", "c"], audio_file(), call=call)
        self.assertEqual(text, "salom dunyo")
        self.assertEqual([c[1] for c in calls], ["a", "b"])
        self.assertEqual(calls[0][2], "audio/ogg")

    async def test_a_failing_model_moves_on_to_the_next(self):
        def call(key, model, data, mime):
            if model == "a":
                raise RuntimeError("quota")
            return "matn"
        self.assertEqual(await transcribe("k", ["a", "b"], audio_file(), call=call), "matn")

    async def test_all_failing_raises_all_empty_returns_blank(self):
        def boom(*args):
            raise RuntimeError("quota")
        with self.assertRaises(RuntimeError):
            await transcribe("k", ["a", "b"], audio_file(), call=boom)
        self.assertEqual(await transcribe("k", ["a"], audio_file(), call=lambda *a: ""), "")

    async def test_slow_model_times_out_and_oversize_is_refused(self):
        import time
        with mock.patch.object(gemini_audio, "AUDIO_TIMEOUT", 0.05):
            with self.assertRaises(Exception):
                await transcribe("k", ["a"], audio_file(), call=lambda *a: time.sleep(0.3))
        with mock.patch.object(gemini_audio, "MAX_AUDIO_BYTES", 10):
            with self.assertRaises(ValueError):
                await transcribe("k", ["a"], audio_file(100), call=lambda *a: "x")


def assistant_with_openai(settings, side_effect=None, text="openai matni"):
    assistant = ShadowAssistant(settings)
    create = AsyncMock(side_effect=side_effect, return_value=SimpleNamespace(text=text))
    assistant.client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    return assistant, create


class AssistantTranscribeTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_is_used_when_it_works(self):
        assistant, create = assistant_with_openai(gemini_settings(openai_api_key="sk-test"))
        with mock.patch("shadow.assistant.gemini_audio.transcribe", AsyncMock(return_value="gemini")) as gem:
            self.assertEqual(await assistant.transcribe_audio(audio_file()), "openai matni")
        gem.assert_not_awaited()

    async def test_limited_openai_falls_back_to_gemini_and_is_skipped_next_time(self):
        assistant, create = assistant_with_openai(gemini_settings(openai_api_key="sk-test"), side_effect=QuotaError())
        gem = AsyncMock(return_value="gemini matni")
        with mock.patch("shadow.assistant.gemini_audio.transcribe", gem):
            self.assertEqual(await assistant.transcribe_audio(audio_file()), "gemini matni")
            self.assertEqual(await assistant.transcribe_audio(audio_file()), "gemini matni")
        self.assertEqual(create.await_count, 1)  # the second voice message skipped the cooling OpenAI
        self.assertEqual(gem.await_count, 2)
        key, models = gem.await_args.args[:2]
        self.assertEqual((key, models), ("g-key", ["gemini-flash-lite-latest"]))

    async def test_backup_only_setup_uses_gemini_directly(self):
        assistant = ShadowAssistant(gemini_settings(openai_api_key=""))
        self.assertIsNone(assistant.client)
        with mock.patch("shadow.assistant.gemini_audio.transcribe", AsyncMock(return_value="faqat gemini")):
            self.assertEqual(await assistant.transcribe_audio(audio_file()), "faqat gemini")

    async def test_openai_error_without_a_gemini_key_is_raised(self):
        settings = make_settings(openai_api_key="sk-test", ai_base_url="", ai_api_key="", ai_model="", ai_extra_providers=())
        assistant, _ = assistant_with_openai(settings, side_effect=QuotaError())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(QuotaError):
                await assistant.transcribe_audio(audio_file())

    async def test_nothing_configured_raises_the_openai_message(self):
        settings = make_settings(openai_api_key="", ai_base_url="", ai_api_key="", ai_model="", ai_extra_providers=())
        assistant = ShadowAssistant(settings)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                await assistant.transcribe_audio(audio_file())


if __name__ == "__main__":
    unittest.main()
