import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import mock

from shadow import public_bank as pb
from shadow.public_bank import PublicBankState


class PolicyTests(unittest.TestCase):
    def test_bank_topic_detection(self):
        for text in (
            "Assalomu alaykum, kredit olmoqchiman", "Humo kartamni qanday bloklayman?",
            "ipoteka foizi qancha", "Какой тариф на перевод?", "Davr bank omonat shartlari",
            "how do I open a deposit account",
        ):
            self.assertTrue(pb.is_bank_question(text), text)
        for text in ("Salom, qalaysan?", "Ertaga uchrashamizmi", "Menga rasm tashla", ""):
            self.assertFalse(pb.is_bank_question(text), text)

    def test_stop_words(self):
        for text in ("stop", "Stop!", "/stop", "To'xtat", "стоп"):
            self.assertTrue(pb.is_stop_request(text), text)
        self.assertFalse(pb.is_stop_request("stop loss kredit"))

    def test_topic_gate_and_followups(self):
        state = PublicBankState()
        self.assertFalse(state.should_answer(1, "Salom, qalaysan?", now=0))
        self.assertTrue(state.should_answer(1, "kredit olmoqchiman", now=0))
        state.mark_replied(1, now=0)
        self.assertTrue(state.should_answer(1, "yoshim 25 bo'lsa-chi?", now=60))
        self.assertFalse(state.should_answer(1, "yoshim 25 bo'lsa-chi?", now=pb.SESSION_WINDOW_SECONDS + 60))

    def test_oversized_and_muted_are_ignored(self):
        state = PublicBankState()
        self.assertFalse(state.should_answer(1, "kredit " * 500, now=0))
        state.mute(2)
        self.assertFalse(state.should_answer(2, "kredit", now=0))

    def test_rate_limits(self):
        state = PublicBankState(per_chat_per_hour=2, global_per_hour=3)
        self.assertTrue(state.allow(1, now=0))
        self.assertTrue(state.allow(1, now=1))
        self.assertFalse(state.allow(1, now=2))
        self.assertTrue(state.allow(2, now=3))
        self.assertFalse(state.allow(3, now=4))
        self.assertTrue(state.allow(1, now=3700))

    def test_disclosure_only_once(self):
        state = PublicBankState()
        self.assertTrue(state.needs_disclosure(1))
        state.mark_replied(1)
        self.assertFalse(state.needs_disclosure(1))


class FakeClient:
    def __init__(self):
        self.sent = []

    @asynccontextmanager
    async def action(self, chat_id, kind):
        yield

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def send_read_acknowledge(self, chat_id):
        pass

    async def iter_messages(self, *args, **kwargs):
        return
        yield


class FakeAssistant:
    def __init__(self):
        self.calls = []

    async def reply_public_bank(self, *, history, message):
        self.calls.append(message)
        return "Kredit shartlari bank bo‘yicha farq qiladi."


def make_event(text, *, private=True, sender=None, chat_id=555, media=None):
    sender = sender or SimpleNamespace(id=42, bot=False, deleted=False)

    async def get_sender():
        return sender
    return SimpleNamespace(chat_id=chat_id, raw_text=text, is_private=private,
                           message=SimpleNamespace(media=media), get_sender=get_sender)


class AgentTests(unittest.TestCase):
    def setUp(self):
        from shadow.config import Settings
        from shadow.telegram_agent import TelegramAgent
        patcher = mock.patch.dict(os.environ, {"PUBLIC_BANK_REPLY": "true", "SHADOW_LOCAL_SETTINGS": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.agent = TelegramAgent(Settings.from_env())
        self.agent.client = FakeClient()
        self.agent.assistant = FakeAssistant()
        self.agent.reply_enabled = True
        self.agent.connected = True
        self.agent._me_id = 1
        for name, replacement in (("human_typing_delay", lambda text: 0), ("read_delay", lambda: 0)):
            patcher = mock.patch(f"shadow.telegram_agent.{name}", replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_event(self, event):
        asyncio.run(self.agent._maybe_public_bank_reply(event))
        return self.agent.client.sent

    def test_bank_question_gets_answer_with_ai_disclosure_once(self):
        sent = self.run_event(make_event("kredit olmoqchiman"))
        self.assertEqual(len(sent), 1)
        self.assertIn("Shadow AI", sent[0][1])
        self.assertIn("inson emasman", sent[0][1])
        sent = self.run_event(make_event("foiz qancha?"))
        self.assertEqual(len(sent), 2)
        self.assertNotIn("Shadow AI", sent[1][1])

    def test_other_topics_groups_bots_and_media_are_ignored(self):
        self.assertEqual(self.run_event(make_event("salom, qalaysan")), [])
        self.assertEqual(self.run_event(make_event("kredit", private=False)), [])
        self.assertEqual(self.run_event(make_event("kredit", sender=SimpleNamespace(id=9, bot=True, deleted=False))), [])
        self.assertEqual(self.run_event(make_event("kredit", sender=SimpleNamespace(id=777000, bot=False, deleted=False))), [])
        self.assertEqual(self.run_event(make_event("kredit", media=object())), [])
        self.assertEqual(self.agent.assistant.calls, [])

    def test_disabled_by_default_setting(self):
        from dataclasses import replace
        self.agent.settings = replace(self.agent.settings, public_bank_reply=False)
        self.assertEqual(self.run_event(make_event("kredit olmoqchiman")), [])

    def test_reply_switch_off_means_no_answer(self):
        self.agent.reply_enabled = False
        self.assertEqual(self.run_event(make_event("kredit olmoqchiman")), [])

    def test_stop_mutes_the_chat(self):
        self.run_event(make_event("kredit olmoqchiman"))
        sent = self.run_event(make_event("stop"))
        self.assertIn("boshqa yozmayman", sent[-1][1])
        before = len(sent)
        self.assertEqual(len(self.run_event(make_event("kredit foizi?"))), before)

    def test_stop_from_stranger_without_conversation_is_ignored(self):
        self.assertEqual(self.run_event(make_event("stop")), [])


if __name__ == "__main__":
    unittest.main()
