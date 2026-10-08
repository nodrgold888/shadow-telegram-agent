import os
import unittest
from types import SimpleNamespace
from unittest import mock

from shadow.assistant import GREETING_PROMPT
from tests.test_ai_fallback import build, make_settings
from shadow.greeting import GreetingState, MAX_REPLIES_PER_CHAT


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def send_read_acknowledge(self, chat_id):
        pass

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
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "APPROVED_CHAT_IDS": "5", **env}):
        agent = TelegramAgent(Settings.from_env())
    agent.connected = True
    agent.client = FakeClient()
    agent.reply_enabled = True
    agent.assistant = SimpleNamespace(reply_greeting=mock.AsyncMock(return_value="Salom! Nima ish bilan yozdingiz?"), last_model=None, last_provider=None)
    agent._history = mock.AsyncMock(return_value="")
    agent.greet_unknown = True
    return agent


def event(agent, chat_id=9, text="Assalomu alaykum", private=True, bot=False, media=None):
    async def get_sender():
        return SimpleNamespace(id=chat_id, bot=bot, deleted=False)
    return SimpleNamespace(client=agent.client, chat_id=chat_id, raw_text=text, is_private=private,
                           message=SimpleNamespace(voice=None, media=media), get_sender=get_sender)


class GreetingStateTests(unittest.TestCase):
    def test_per_chat_cap_and_window(self):
        state = GreetingState()
        for _ in range(MAX_REPLIES_PER_CHAT):
            self.assertTrue(state.allow(1, "salom", now=0))
        self.assertFalse(state.allow(1, "salom", now=10))
        self.assertTrue(state.allow(1, "salom", now=25 * 3600))

    def test_global_cap_muted_and_oversized(self):
        state = GreetingState(global_per_hour=2)
        self.assertTrue(state.allow(1, "a", now=0))
        self.assertTrue(state.allow(2, "a", now=0))
        self.assertFalse(state.allow(3, "a", now=1))
        state.mute(4)
        self.assertFalse(state.allow(4, "a", now=4000))
        self.assertFalse(state.allow(5, "x" * 5000, now=4000))
        self.assertFalse(state.allow(5, "   ", now=4000))


class GreetingFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_private_chat_gets_a_short_greeting(self):
        agent = make_agent()
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            await agent._on_message(event(agent))
        self.assertEqual(agent.client.sent, [(9, "Salom! Nima ish bilan yozdingiz?")])
        self.assertEqual(agent.greeting.reply_count, 1)

    async def test_switch_off_means_silence(self):
        agent = make_agent()
        agent.greet_unknown = False
        await agent._on_message(event(agent))
        self.assertEqual(agent.client.sent, [])

    async def test_never_for_groups_bots_media_or_friends(self):
        agent = make_agent()
        agent.friend_ids = {9: "ish"}
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            await agent._on_message(event(agent, chat_id=9))
            await agent._on_message(event(agent, chat_id=10, private=False))
            await agent._on_message(event(agent, chat_id=11, bot=True))
            await agent._on_message(event(agent, chat_id=12, media=object()))
        self.assertEqual(agent.client.sent, [])

    async def test_reply_switch_off_means_silence(self):
        agent = make_agent()
        agent.reply_enabled = False
        await agent._on_message(event(agent))
        self.assertEqual(agent.client.sent, [])

    async def test_stop_mutes_the_chat(self):
        agent = make_agent()
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            await agent._on_message(event(agent))
            await agent._on_message(event(agent, text="stop"))
            await agent._on_message(event(agent, text="yana salom"))
        self.assertEqual([text for _, text in agent.client.sent], ["Salom! Nima ish bilan yozdingiz?", "Xo‘p, boshqa yozmayman."])

    async def test_approved_chats_are_not_greeted(self):
        agent = make_agent()
        agent._on_message  # approved chat 5 goes the normal reply path, never the greeting
        agent._maybe_greet_unknown = mock.AsyncMock()
        agent.assistant = None
        await agent._on_message(event(agent, chat_id=5))
        agent._maybe_greet_unknown.assert_not_called()

    async def test_switch_is_saved_and_reported(self):
        agent = make_agent()
        with mock.patch("shadow.telegram_agent.save_greet_unknown", new=mock.AsyncMock(return_value=True)) as save:
            result = await agent.set_greet_unknown(False)
        save.assert_awaited_once_with(False)
        self.assertEqual(result, {"greet_unknown": False, "persisted": True})
        self.assertFalse(agent.status()["greet_unknown"]["enabled"])


class GreetingPromptTests(unittest.TestCase):
    def test_prompt_keeps_the_honesty_and_privacy_rules(self):
        self.assertIn("inson emassiz", GREETING_PROMPT)
        self.assertIn("Shadow AI", GREETING_PROMPT)
        self.assertIn("Hech qachon inkor etmang", GREETING_PROMPT)
        self.assertIn("shaxsiy ma’lumot", GREETING_PROMPT)


if __name__ == "__main__":
    unittest.main()


class NoReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_closed_chat_gets_no_second_tushundim(self):
        assistant, _, compat = build(make_settings(openai_api_key=""))
        for text, expected in (("NOREPLY", ""), ("noreply.", ""), ("Salom, kim bu?", "Salom, kim bu?")):
            compat.chat.completions.create.return_value = mock.Mock(
                choices=[mock.Mock(message=mock.Mock(content=text))])
            self.assertEqual(await assistant.reply_greeting(history="", message="Ташладими"), expected)

    def test_the_prompt_closes_a_chat_only_once(self):
        self.assertIn("NOREPLY", GREETING_PROMPT)
