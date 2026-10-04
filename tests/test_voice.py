import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from shadow.assistant import ShadowAssistant
from shadow.config import Settings
from shadow.telegram_agent import TelegramAgent


def settings():
    return Settings(None, "", "", "test", "test", frozenset({123}), True,
                    "mentions", 12, 3800, "", "")


class VoiceAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribes_audio_with_openai_audio_api(self):
        with patch("shadow.assistant.AsyncOpenAI") as openai:
            openai.return_value.audio.transcriptions.create = AsyncMock(
                return_value=SimpleNamespace(text="Salom, qalaysiz?")
            )
            assistant = ShadowAssistant(settings())
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "voice.ogg"
                source.write_bytes(b"fake audio")
                text = await assistant.transcribe_audio(source)
            self.assertEqual(text, "Salom, qalaysiz?")
            call = openai.return_value.audio.transcriptions.create.await_args
            self.assertEqual(call.kwargs["model"], "gpt-transcribe")
            self.assertEqual(call.kwargs["extra_body"]["languages"], ["uz"])
            self.assertIn("o‘zbek", call.kwargs["prompt"].lower())
            self.assertTrue(call.kwargs["file"].name.endswith("voice.ogg"))

    async def test_generates_telegram_opus_speech(self):
        class StreamingResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def stream_to_file(self, path):
                Path(path).write_bytes(b"opus")

        with patch("shadow.assistant.AsyncOpenAI") as openai:
            openai.return_value.audio.speech.with_streaming_response.create = Mock(
                return_value=StreamingResponse()
            )
            assistant = ShadowAssistant(settings())
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "reply.ogg"
                await assistant.synthesize_speech("Assalomu alaykum", output)
                self.assertEqual(output.read_bytes(), b"opus")
            call = openai.return_value.audio.speech.with_streaming_response.create.call_args
            self.assertEqual(call.kwargs["model"], "gpt-4o-mini-tts")
            self.assertEqual(call.kwargs["response_format"], "opus")
            self.assertIn("Uzbek", call.kwargs["instructions"])

    async def test_voice_handler_transcribes_then_requests_voice_reply(self):
        agent = TelegramAgent(settings())
        async def download(message, file):
            Path(file).write_bytes(b"voice")
            return file

        agent.client = SimpleNamespace(download_media=download)
        agent.assistant = SimpleNamespace(transcribe_audio=AsyncMock(return_value="Salom"))
        agent._work_reply = AsyncMock()
        event = SimpleNamespace(
            chat_id=123, id=7, message=SimpleNamespace(voice=SimpleNamespace(duration=10)),
            file=SimpleNamespace(size=5), reply=AsyncMock(),
        )
        await agent._work_voice_reply(event, "Test")
        agent.assistant.transcribe_audio.assert_awaited_once()
        agent._work_reply.assert_awaited_once_with(
            event, "Test", "Salom", "", voice_reply=True,
        )

    async def test_voice_over_limit_is_rejected_before_download(self):
        agent = TelegramAgent(settings())
        agent.client = SimpleNamespace(download_media=AsyncMock())
        agent.assistant = SimpleNamespace(transcribe_audio=AsyncMock())
        event = SimpleNamespace(
            chat_id=123, id=8, message=SimpleNamespace(voice=SimpleNamespace(duration=181)),
            file=SimpleNamespace(size=5), reply=AsyncMock(),
        )
        await agent._work_voice_reply(event, "Test")
        agent.client.download_media.assert_not_awaited()
        event.reply.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
