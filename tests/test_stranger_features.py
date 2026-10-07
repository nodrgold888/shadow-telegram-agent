import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shadow.greeting import NoteState


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def send_read_acknowledge(self, chat_id):
        pass

    async def download_media(self, message, file=None):
        Path(file).write_bytes(b"ogg")
        return file

    def action(self, chat_id, kind):
        class Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *args):
                return False
        return Ctx()


def make_agent(**env):
    from shadow.config import Settings
    from shadow.telegram_agent import TelegramAgent
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "APPROVED_CHAT_IDS": "5", "SHADOW_STATE_FILE": "", **env}):
        agent = TelegramAgent(Settings.from_env())
    agent.connected = True
    agent.reply_enabled = True
    agent.client = FakeClient()
    agent._me_id = 111
    agent._history = mock.AsyncMock(return_value="")
    agent.assistant = SimpleNamespace(
        last_model=None, last_provider=None,
        transcribe_audio=mock.AsyncMock(return_value="Salom, kredit haqida so‘ramoqchiman"),
        reply_greeting=mock.AsyncMock(return_value="Salom! Qanday ish bilan yozdingiz?"))
    return agent


def event(agent, chat_id=9, text="", voice=None, private=True, bot=False, first="Aziz", username="aziz"):
    async def get_sender():
        return SimpleNamespace(id=chat_id, bot=bot, deleted=False, first_name=first, last_name=None, username=username)
    return SimpleNamespace(client=agent.client, chat_id=chat_id, raw_text=text, is_private=private, id=7,
                           message=SimpleNamespace(voice=voice, media=voice), get_sender=get_sender,
                           file=SimpleNamespace(size=2000) if voice else None, reply=mock.AsyncMock())


VOICE = SimpleNamespace(duration=8)


class NoteStateTests(unittest.TestCase):
    def test_one_note_per_chat_per_gap_and_global_cap(self):
        notes = NoteState(gap_seconds=600, global_per_hour=2)
        self.assertTrue(notes.allow(1, now=0))
        self.assertFalse(notes.allow(1, now=100))
        self.assertTrue(notes.allow(2, now=100))
        self.assertFalse(notes.allow(3, now=200))
        self.assertTrue(notes.allow(1, now=4000))


class OwnerNoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_note_goes_to_saved_messages_with_name_text_and_id(self):
        agent = make_agent()
        agent.notify_unknown = True
        await agent._on_message(event(agent, text="Assalomu alaykum, ish bormi?"))
        self.assertEqual(len(agent.client.sent), 1)
        target, note = agent.client.sent[0]
        self.assertEqual(target, "me")
        for part in ("Notanish yozdi", "Aziz (@aziz)", "Chat ID: 9", "Assalomu alaykum, ish bormi?"):
            self.assertIn(part, note)

    async def test_voice_note_says_voice_and_second_message_is_rate_limited(self):
        agent = make_agent()
        agent.notify_unknown = True
        await agent._on_message(event(agent, voice=VOICE))
        await agent._on_message(event(agent, text="yana"))
        self.assertEqual(len(agent.client.sent), 1)
        self.assertIn("ovozli xabar", agent.client.sent[0][1])

    async def test_no_note_when_off_for_friends_bots_groups_or_approved_chats(self):
        agent = make_agent()
        await agent._on_message(event(agent, text="salom"))  # switch off
        agent.notify_unknown = True
        agent.friend_ids = {20: "ish"}
        await agent._on_message(event(agent, chat_id=20, text="salom"))
        await agent._on_message(event(agent, chat_id=21, text="salom", bot=True))
        await agent._on_message(event(agent, chat_id=22, text="salom", private=False))
        agent.assistant = None
        await agent._on_message(event(agent, chat_id=5, text="salom"))  # approved chat: normal path
        self.assertEqual(agent.client.sent, [])


class VoiceReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_voice_message_is_transcribed_and_answered(self):
        agent = make_agent()
        agent.voice_unknown = True
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            await agent._on_message(event(agent, voice=VOICE))
        agent.assistant.transcribe_audio.assert_awaited_once()
        agent.assistant.reply_greeting.assert_awaited_once()
        self.assertEqual(agent.client.sent, [(9, "Salom! Qanday ish bilan yozdingiz?")])
        self.assertEqual(agent.voice_state.reply_count, 1)

    async def test_round_video_message_from_a_stranger_is_handled_like_voice(self):
        agent = make_agent()
        agent.voice_unknown = True
        agent.assistant.describe_video = mock.AsyncMock(return_value=("Ko'rinishi: odam salom bermoqda", True))
        ev = event(agent, voice=None)
        ev.message = SimpleNamespace(voice=None, video_note=VOICE, media=VOICE)
        ev.file = SimpleNamespace(size=2000)
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            await agent._on_message(ev)
        agent.assistant.describe_video.assert_awaited_once()
        agent.assistant.transcribe_audio.assert_not_called()
        self.assertIn("avtomatik tahlil", agent.assistant.reply_greeting.await_args.kwargs["message"])
        self.assertEqual(agent.client.sent, [(9, "Salom! Qanday ish bilan yozdingiz?")])

    async def test_round_video_message_in_an_approved_chat_is_not_ignored_any_more(self):
        agent = make_agent()
        ev = event(agent, chat_id=5, voice=None)
        ev.message = SimpleNamespace(voice=None, video_note=VOICE, media=VOICE, mentioned=False)
        ev.file = SimpleNamespace(size=2000, name=None, ext=".mp4")

        async def get_chat():
            return SimpleNamespace(first_name="Aziz")

        ev.get_chat = get_chat
        ev.is_reply = False
        agent._work_voice_reply = mock.AsyncMock()
        agent.client.send_read_acknowledge = mock.AsyncMock()
        await agent._on_message(ev)
        agent._work_voice_reply.assert_awaited_once()
        self.assertTrue(agent._work_voice_reply.await_args.kwargs["video_note"])

    async def test_ordinary_video_in_an_approved_chat_is_looked_at_but_not_from_strangers(self):
        agent = make_agent()
        agent.voice_unknown = True
        agent.assistant.describe_video = mock.AsyncMock(return_value=("x", True))
        stranger = event(agent, chat_id=9, voice=None)
        stranger.message = SimpleNamespace(voice=None, video_note=None, video=VOICE, gif=None, media=VOICE)
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            await agent._on_message(stranger)
        agent.assistant.describe_video.assert_not_called()
        ev = event(agent, chat_id=5, text="Mana qarang", voice=None)
        ev.message = SimpleNamespace(voice=None, video_note=None, video=VOICE, gif=None, media=VOICE, mentioned=False)
        ev.file = SimpleNamespace(size=2000, name=None, ext=".mp4")

        async def get_chat():
            return SimpleNamespace(first_name="Aziz")

        ev.get_chat = get_chat
        ev.is_reply = False
        agent._work_voice_reply = mock.AsyncMock()
        agent.client.send_read_acknowledge = mock.AsyncMock()
        await agent._on_message(ev)
        kwargs = agent._work_voice_reply.await_args.kwargs
        self.assertTrue(kwargs["video"])
        self.assertEqual(kwargs["caption"], "Mana qarang")

    async def test_switch_off_long_oversized_friend_group_and_bot_are_ignored(self):
        agent = make_agent()
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            await agent._on_message(event(agent, voice=VOICE))  # off
            agent.voice_unknown = True
            await agent._on_message(event(agent, chat_id=10, voice=SimpleNamespace(duration=300)))
            agent.friend_ids = {11: "oila"}
            await agent._on_message(event(agent, chat_id=11, voice=VOICE))
            await agent._on_message(event(agent, chat_id=12, voice=VOICE, private=False))
            await agent._on_message(event(agent, chat_id=13, voice=VOICE, bot=True))
        agent.assistant.transcribe_audio.assert_not_called()
        self.assertEqual(agent.client.sent, [])

    async def test_daily_limit_per_chat(self):
        agent = make_agent()
        agent.voice_unknown = True
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            for _ in range(8):
                await agent._on_message(event(agent, voice=VOICE))
        self.assertEqual(len(agent.client.sent), 5)


class FlagTests(unittest.IsolatedAsyncioTestCase):
    async def test_flags_are_saved_reported_and_unknown_names_rejected(self):
        agent = make_agent()
        with mock.patch("shadow.telegram_agent.save_stranger_flag", new=mock.AsyncMock(return_value=True)) as save:
            result = await agent.set_stranger_flag("voice_unknown", True)
        save.assert_awaited_once_with("voice_unknown", True)
        self.assertEqual(result, {"voice_unknown": True, "persisted": True})
        status = agent.status()
        self.assertTrue(status["voice_unknown"]["enabled"])
        self.assertFalse(status["notify_unknown"]["enabled"])
        with self.assertRaises(ValueError):
            await agent.set_stranger_flag("hack", True)


if __name__ == "__main__":
    unittest.main()
