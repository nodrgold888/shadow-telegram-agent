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
