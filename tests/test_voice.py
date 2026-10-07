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


class VideoNoteTests(unittest.IsolatedAsyncioTestCase):
    def test_spoken_media_recognises_voice_and_round_video(self):
        from shadow.telegram_agent import spoken_media
        voice, note = SimpleNamespace(duration=3), SimpleNamespace(duration=9)
        self.assertEqual(spoken_media(SimpleNamespace(voice=voice, video_note=None)), ("voice", voice))
        self.assertEqual(spoken_media(SimpleNamespace(voice=None, video_note=note)), ("video_note", note))
        self.assertEqual(spoken_media(SimpleNamespace(voice=None, video_note=None, video=object())), (None, None))
        self.assertEqual(spoken_media(SimpleNamespace()), (None, None))

    async def test_video_note_is_downloaded_as_mp4_transcribed_and_answered_in_text(self):
        agent = TelegramAgent(settings())
        seen = {}

        async def download(message, file):
            seen["name"] = Path(file).name
            Path(file).write_bytes(b"video")
            return file

        agent.client = SimpleNamespace(download_media=download)
        agent.assistant = SimpleNamespace(transcribe_audio=AsyncMock(return_value="Salom, kreditni so'ramoqchiman"))
        agent._work_reply = AsyncMock()
        event = SimpleNamespace(
            chat_id=123, id=7, message=SimpleNamespace(voice=None, video_note=SimpleNamespace(duration=12)),
            file=SimpleNamespace(size=5), reply=AsyncMock(),
        )
        await agent._work_voice_reply(event, "Test", video_note=True)
        self.assertEqual(seen["name"], "videonote.mp4")
        agent._work_reply.assert_awaited_once_with(event, "Test", "Salom, kreditni so'ramoqchiman", "", voice_reply=False)

    async def test_silent_video_note_gets_a_clear_message(self):
        agent = TelegramAgent(settings())

        async def download(message, file):
            Path(file).write_bytes(b"video")
            return file

        agent.client = SimpleNamespace(download_media=download)
        agent.assistant = SimpleNamespace(transcribe_audio=AsyncMock(return_value=""))
        agent._work_reply = AsyncMock()
        event = SimpleNamespace(
            chat_id=123, id=7, message=SimpleNamespace(voice=None, video_note=SimpleNamespace(duration=12)),
            file=SimpleNamespace(size=5), reply=AsyncMock(),
        )
        agent._can_reply = lambda chat_id: True
        await agent._work_voice_reply(event, "Test", video_note=True)
        agent._work_reply.assert_not_awaited()
        self.assertIn("Video xabarni tushunib bo", event.reply.await_args.args[0])

    async def test_gemini_fallback_gets_the_video_mime_type(self):
        from shadow.assistant import ShadowAssistant
        from tests.test_voice_fallback import gemini_settings
        assistant = ShadowAssistant(gemini_settings(openai_api_key=""))
        clip = Path(tempfile.mkdtemp()) / "videonote.mp4"
        clip.write_bytes(b"video")
        gem = AsyncMock(return_value="matn")
        with patch("shadow.assistant.gemini_audio.transcribe", gem):
            self.assertEqual(await assistant.transcribe_audio(clip), "matn")
        self.assertEqual(gem.await_args.args[3], "video/mp4")


if __name__ == "__main__":
    unittest.main()
