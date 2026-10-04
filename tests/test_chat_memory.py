import unittest
from unittest.mock import AsyncMock, patch

from shadow.chat_memory import normalize_chat_profile, normalize_chat_profiles
from shadow.config import Settings
from shadow.telegram_agent import TelegramAgent


class ChatMemoryTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        settings = Settings(
            None, "", "", "", "gpt-5-mini", frozenset({101, 202}), False,
            "mentions", 12, 3800, "", "",
        )
        with patch("shadow.telegram_agent.load_chat_profiles", return_value={}):
            return TelegramAgent(settings)

    def test_profile_has_bounded_known_fields(self):
        profile = normalize_chat_profile({"style": "  do‘stona  ", "unknown": "x"})
        self.assertEqual(profile["style"], "do‘stona")
        self.assertEqual(profile["memory"], "")
        with self.assertRaises(ValueError):
            normalize_chat_profile({"notes": "x" * 1401})

    def test_saved_profiles_validate_chat_ids(self):
        profiles = normalize_chat_profiles({"-100123": {"notes": "Guruh qaydi"}})
        self.assertEqual(profiles["-100123"]["notes"], "Guruh qaydi")
        with self.assertRaises(ValueError):
            normalize_chat_profiles({"not-a-chat": {"notes": "x"}})

    async def test_profiles_are_isolated_and_removed_when_approval_is_revoked(self):
        agent = self.make_agent()
        agent.chat_profiles = {
            "101": normalize_chat_profile({"memory": "Private profile A"}),
            "202": normalize_chat_profile({"memory": "Private profile B"}),
        }
        self.assertEqual(agent.chat_profile_for(101)["memory"], "Private profile A")
        self.assertEqual(agent.chat_profile_for(202)["memory"], "Private profile B")
        with patch("shadow.telegram_agent.save_approved_chats", new=AsyncMock(return_value=True)), patch(
            "shadow.telegram_agent.save_chat_profiles", new=AsyncMock(return_value=True)
        ):
            await agent.update_chat_approval(101, False)
        self.assertNotIn("101", agent.chat_profiles)
        self.assertEqual(agent.chat_profile_for(202)["memory"], "Private profile B")
        with self.assertRaises(ValueError):
            agent.chat_profile_for(101)


if __name__ == "__main__":
    unittest.main()
