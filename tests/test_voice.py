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
    def test_videos_and_gifs_only_count_when_asked_for(self):
        from shadow.telegram_agent import spoken_media
        video = SimpleNamespace(duration=5)
        message = SimpleNamespace(voice=None, video_note=None, video=video, gif=None)
        self.assertEqual(spoken_media(message), (None, None))
        self.assertEqual(spoken_media(message, include_video=True), ("video", video))
        message.gif = video  # a GIF is also stored as a video document: it must be recognised as a GIF first
        self.assertEqual(spoken_media(message), (None, None))
        self.assertEqual(spoken_media(message, include_video=True), ("gif", video))

    def test_spoken_media_recognises_voice_and_round_video(self):
        from shadow.telegram_agent import spoken_media
        voice, note = SimpleNamespace(duration=3), SimpleNamespace(duration=9)
        self.assertEqual(spoken_media(SimpleNamespace(voice=voice, video_note=None)), ("voice", voice))
        self.assertEqual(spoken_media(SimpleNamespace(voice=None, video_note=note)), ("video_note", note))
        self.assertEqual(spoken_media(SimpleNamespace(voice=None, video_note=None, video=object())), (None, None))
        self.assertEqual(spoken_media(SimpleNamespace()), (None, None))

    def _video_event(self, **message):
        return SimpleNamespace(
            chat_id=123, id=7, message=SimpleNamespace(voice=None, video_note=SimpleNamespace(duration=12), **message),
            file=SimpleNamespace(size=5), reply=AsyncMock(),
        )

    def _agent(self, describe, seen_name=None):
        agent = TelegramAgent(settings())

        async def download(message, file):
            if seen_name is not None:
                seen_name.append(Path(file).name)
            Path(file).write_bytes(b"video")
            return file

        agent.client = SimpleNamespace(download_media=download)
        agent.assistant = SimpleNamespace(describe_video=describe, transcribe_audio=AsyncMock())
        agent._work_reply = AsyncMock()
        agent._can_reply = lambda chat_id: True
        return agent

    async def test_video_note_is_analysed_and_answered_in_text(self):
        names = []
        agent = self._agent(AsyncMock(return_value=("Ko'rinishi: odam qo'l silkitmoqda. Aytilgani: salom", True)), names)
        event = self._video_event()
        await agent._work_voice_reply(event, "Test", video_note=True)
        self.assertEqual(names, ["video.mp4"])
        args = agent._work_reply.await_args
        self.assertEqual(args.args[:2], (event, "Test"))
        self.assertIn("avtomatik tahlil", args.args[2])
        self.assertIn("odam qo'l silkitmoqda", args.args[2])
        self.assertNotIn("voice_reply", args.kwargs)  # answered in text, not by voice

    async def test_without_vision_the_reply_is_told_it_did_not_see_the_picture(self):
        agent = self._agent(AsyncMock(return_value=("salom, qalaysiz", False)))
        await agent._work_voice_reply(self._video_event(), "Test", video_note=True)
        message = agent._work_reply.await_args.args[2]
        self.assertIn("tasvirini ko‘ra olmadingiz", message)
        self.assertIn("Tasvirni ko‘rgandek javob bermang", message)
        self.assertIn("salom, qalaysiz", message)

    async def test_silent_unseen_video_note_gets_a_clear_message(self):
        agent = self._agent(AsyncMock(return_value=("", False)))
        event = self._video_event()
        await agent._work_voice_reply(event, "Test", video_note=True)
        agent._work_reply.assert_not_awaited()
        self.assertIn("Video xabarni tushunib bo", event.reply.await_args.args[0])

    async def test_ordinary_video_uses_its_caption_and_stays_quiet_when_nothing_is_known(self):
        agent = self._agent(AsyncMock(return_value=("Ko'rinishi: mashina. Aytilgani: nutq yo'q", True)))
        event = self._video_event()
        event.message.video_note = None
        event.message.video = SimpleNamespace(duration=20)
        await agent._work_voice_reply(event, "Test", video=True, caption="Shu mashina qanday?")
        message = agent._work_reply.await_args.args[2]
        self.assertIn("mashina", message)
        self.assertIn("Videoga yozilgan matn: Shu mashina qanday?", message)
        quiet = self._agent(AsyncMock(return_value=("", False)))
        event2 = self._video_event()
        event2.message.video_note = None
        event2.message.video = SimpleNamespace(duration=20)
        await quiet._work_voice_reply(event2, "Test", video=True)
        quiet._work_reply.assert_not_awaited()
        event2.reply.assert_not_awaited()

    async def test_gif_is_looked_at_as_a_reaction(self):
        describe = AsyncMock(return_value=("Ko'rinishi: mushuk raqsga tushmoqda. Ma'nosi: quvonch", True))
        agent = self._agent(describe)
        event = self._video_event()
        event.message.video_note = None
        event.message.gif = SimpleNamespace(duration=3)
        await agent._work_voice_reply(event, "Test", gif=True)
        describe.assert_awaited_once()
        self.assertTrue(describe.await_args.kwargs["gif"])
        message = agent._work_reply.await_args.args[2]
        self.assertIn("GIF (qisqa animatsiya)", message)
        self.assertIn("mushuk", message)
        self.assertIn("tasvirlab bermang", message)

    async def test_unseen_gif_without_caption_gets_no_answer_and_with_caption_no_invention(self):
        agent = self._agent(AsyncMock(return_value=("", False)))
        event = self._video_event()
        event.message.video_note = None
        await agent._work_voice_reply(event, "Test", gif=True)
        agent._work_reply.assert_not_awaited()
        event.reply.assert_not_awaited()
        await agent._work_voice_reply(event, "Test", gif=True, caption="mana")
        message = agent._work_reply.await_args.args[2]
        self.assertIn("o‘ylab topmang", message)
        self.assertIn("Videoga yozilgan matn: mana", message)

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


class DescribeVideoTests(unittest.IsolatedAsyncioTestCase):
    def _assistant(self, **over):
        from tests.test_voice_fallback import gemini_settings
        return ShadowAssistant(gemini_settings(openai_api_key="", **over))

    async def test_gemini_sees_the_video(self):
        assistant = self._assistant()
        clip = Path(tempfile.mkdtemp()) / "video.mp4"
        clip.write_bytes(b"video")
        gem = AsyncMock(return_value="Ko'rinishi: it. Aytilgani: nutq yo'q")
        with patch("shadow.assistant.gemini_audio.describe_video", gem):
            self.assertEqual(await assistant.describe_video(clip), ("Ko'rinishi: it. Aytilgani: nutq yo'q", True))

    async def test_when_gemini_fails_only_the_sound_is_transcribed(self):
        assistant = self._assistant()
        clip = Path(tempfile.mkdtemp()) / "video.mp4"
        clip.write_bytes(b"video")
        with patch("shadow.assistant.gemini_audio.describe_video", AsyncMock(side_effect=RuntimeError("boom"))), \
                patch.object(assistant, "transcribe_audio", AsyncMock(return_value="salom")):
            self.assertEqual(await assistant.describe_video(clip), ("salom", False))
        with patch("shadow.assistant.gemini_audio.describe_video", AsyncMock(side_effect=RuntimeError("boom"))), \
                patch.object(assistant, "transcribe_audio", AsyncMock(side_effect=RuntimeError("x"))):
            self.assertEqual(await assistant.describe_video(clip), ("", False))

    async def test_gif_prompt_is_used_and_there_is_no_sound_fallback(self):
        assistant = self._assistant()
        clip = Path(tempfile.mkdtemp()) / "video.mp4"
        clip.write_bytes(b"gif")
        gem = AsyncMock(return_value="Ko'rinishi: x")
        with patch("shadow.assistant.gemini_audio.describe_video", gem):
            self.assertEqual(await assistant.describe_video(clip, gif=True), ("Ko'rinishi: x", True))
        self.assertTrue(gem.await_args.kwargs["gif"])
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        transcribe = AsyncMock(return_value="shovqin")
        with patch("shadow.assistant.gemini_audio.describe_video", failing), patch.object(assistant, "transcribe_audio", transcribe):
            self.assertEqual(await assistant.describe_video(clip, gif=True), ("", False))
        transcribe.assert_not_awaited()

    async def test_gemini_audio_helper_sends_the_gif_prompt_for_gifs(self):
        from shadow import gemini_audio
        clip = Path(tempfile.mkdtemp()) / "video.mp4"
        clip.write_bytes(b"gif")
        seen = []

        def call(key, model, data, mime, *prompt):
            seen.append(prompt)
            return "Ko'rinishi: x"

        await gemini_audio.describe_video("k", ["a"], clip, call=call, gif=True)
        await gemini_audio.describe_video("k", ["a"], clip, call=call)
        self.assertEqual(seen, [(gemini_audio.GIF_PROMPT,), ()])
        self.assertIn("GIF", gemini_audio.GIF_PROMPT)

    async def test_gemini_audio_helper_tries_the_next_model_and_rejects_big_files(self):
        from shadow import gemini_audio
        clip = Path(tempfile.mkdtemp()) / "video.mp4"
        clip.write_bytes(b"video")
        calls = []

        def call(key, model, data, mime):
            calls.append((model, mime))
            if model == "a":
                raise RuntimeError("down")
            return "Ko'rinishi: x"

        self.assertEqual(await gemini_audio.describe_video("k", ["a", "b"], clip, call=call), "Ko'rinishi: x")
        self.assertEqual(calls, [("a", "video/mp4"), ("b", "video/mp4")])
        clip.write_bytes(b"x" * (gemini_audio.MAX_VIDEO_BYTES + 1))
        with self.assertRaises(ValueError):
            await gemini_audio.describe_video("k", ["a"], clip, call=call)


if __name__ == "__main__":
    unittest.main()
