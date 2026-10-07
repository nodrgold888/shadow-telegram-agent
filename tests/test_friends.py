import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shadow import persist


def make_agent(**env):
    from shadow.config import Settings
    from shadow.telegram_agent import TelegramAgent
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "APPROVED_CHAT_IDS": "*", **env}):
        agent = TelegramAgent(Settings.from_env())
    agent.connected = True
    agent.client = SimpleNamespace(send_message=mock.AsyncMock())
    agent.assistant = SimpleNamespace()
    agent.reply_enabled = True
    return agent


def event(agent, chat_id):
    return SimpleNamespace(client=agent.client, chat_id=chat_id, raw_text="Salom", message=SimpleNamespace(voice=None, media=None),
                           is_private=True)


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_looks_each_sender_up_once_and_never_for_own_messages(self):
        agent = make_agent()
        agent._me_id = 111
        lookups = []

        def message(text, sender_id, out=False, first="Aziz"):
            async def get_sender():
                lookups.append(sender_id)
                return SimpleNamespace(id=sender_id, first_name=first)
            return SimpleNamespace(raw_text=text, sender_id=sender_id, out=out, get_sender=get_sender)

        newest_first = [message("uchinchi", 7), message("men yozdim", 111, out=True), message("ikkinchi", 7), message("birinchi", 8, first="Vali")]

        async def iter_messages(chat_id, limit):
            for item in newest_first:
                yield item

        agent.client = SimpleNamespace(iter_messages=iter_messages)
        history = await agent._history(5)
        self.assertEqual(history.splitlines(), ["Vali: birinchi", "Aziz: ikkinchi", "Shadow/men: men yozdim", "Aziz: uchinchi"])
        self.assertEqual(sorted(lookups), [7, 8])  # no lookup for the own message, one per other sender


class FriendListTests(unittest.IsolatedAsyncioTestCase):
    async def test_friend_chats_are_ignored_even_when_every_chat_is_approved(self):
        agent = make_agent()
        agent.friend_ids = {5: "ish"}
        agent._maybe_public_bank_reply = mock.AsyncMock()
        agent._can_reply = mock.Mock(return_value=True)
        agent._locks = mock.MagicMock(side_effect=AssertionError("friend chat must not be processed"))
        await agent._on_message(event(agent, 5))
        agent.client.send_message.assert_not_called()
        agent._maybe_public_bank_reply.assert_not_called()

    def test_can_reply_is_false_for_friends(self):
        agent = make_agent()
        self.assertTrue(agent._can_reply(7))
        agent.friend_ids = {7: "oila"}
        self.assertFalse(agent._can_reply(7))

    async def test_friend_is_saved_and_loaded_from_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "state.json")
            with mock.patch.dict(os.environ, {"SHADOW_STATE_FILE": state}):
                agent = make_agent(SHADOW_STATE_FILE=state)
                agent.dialogs = mock.AsyncMock(return_value=[{"id": 5}, {"id": 6}])
                result = await agent.update_chat_friend(5, True, "oila")
                self.assertTrue(result["persisted"])
                self.assertEqual(persist.load_friend_chats(), {5: "oila"})
                await agent.update_chat_friend(5, True, "ish")
                self.assertEqual(persist.load_friend_chats(), {5: "ish"})
                await agent.update_chat_friend(5, False)
                self.assertEqual(persist.load_friend_chats(), {})

    async def test_unknown_chat_cannot_be_added(self):
        agent = make_agent()
        agent.dialogs = mock.AsyncMock(return_value=[{"id": 1}])
        with self.assertRaises(ValueError):
            await agent.update_chat_friend(99, True)

    def test_invalid_saved_value_is_ignored(self):
        with mock.patch.dict(os.environ, {"FRIEND_CHAT_IDS": "1,abc", "SHADOW_STATE_FILE": ""}):
            self.assertEqual(persist.load_friend_chats(), {})
        with mock.patch.dict(os.environ, {"FRIEND_CHAT_IDS": "1, 2:ish, 3:nomalum", "SHADOW_STATE_FILE": ""}):
            self.assertEqual(persist.load_friend_chats(), {1: "dostlar", 2: "ish", 3: "dostlar"})

    async def test_unknown_category_is_rejected(self):
        agent = make_agent()
        agent.dialogs = mock.AsyncMock(return_value=[{"id": 1}])
        with self.assertRaises(ValueError):
            await agent.update_chat_friend(1, True, "begona")


if __name__ == "__main__":
    unittest.main()
