import unittest

from shadow import assistant
from shadow.policy import typing_delay


class HumanChatTests(unittest.TestCase):
    def test_typing_delay_is_short_and_bounded(self):
        self.assertEqual(typing_delay(""), 0.4)
        self.assertAlmostEqual(typing_delay("a" * 50), 1.0)
        self.assertEqual(typing_delay("a" * 5000), 3.5)

    def test_skills_are_part_of_the_prompt(self):
        self.assertIn("Natural, human-sounding conversation", assistant.SKILL_PROMPT)
        self.assertIn("Instagram and TikTok video download", assistant.SKILL_PROMPT)

    def test_human_style_never_denies_being_ai(self):
        self.assertIn("say plainly that you are Shadow AI", assistant.SKILL_PROMPT)
        self.assertIn("Never claim to be a human", assistant.SKILL_PROMPT)


if __name__ == "__main__":
    unittest.main()
