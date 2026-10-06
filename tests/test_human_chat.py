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

    def test_prompt_no_longer_pushes_a_question_and_emoji_on_every_reply(self):
        self.assertNotIn("O‘rinli bo‘lsa savol bilan suhbatni davom ettiring", assistant.SYSTEM_PROMPT)
        self.assertIn("Har javobni savol bilan tugatmang", assistant.SYSTEM_PROMPT)
        self.assertIn("What makes a reply sound like a bot", assistant.SKILL_PROMPT)
        self.assertIn("Small talk examples", assistant.SKILL_PROMPT)

    def test_bank_answers_are_davr_bank_only(self):
        skill = assistant.SKILL_PROMPT
        self.assertIn("Davr Bank only", skill)
        self.assertIn("Do not name, describe, compare, rate or recommend any other bank", skill)
        self.assertNotIn("statistics/rates", skill)  # no market-average comparison source
        self.assertIn("Faqat Davr Bank", assistant.PUBLIC_BANK_PROMPT)
        self.assertIn("rasmiy vakili ekaningizni da’vo qilmang", assistant.PUBLIC_BANK_PROMPT)
        self.assertIn("Davr Bank", assistant.PUBLIC_SKILL_PROMPT)

    def test_bank_agent_preset_is_davr_bank_only(self):
        from shadow.agents import AGENTS
        bank = next(a for a in AGENTS if a.id == "bank")
        self.assertIn("Davr Bank", bank.instructions)
        self.assertIn("boshqa banklarni tilga olmang", bank.instructions)

    def test_honesty_rules_survive_the_style_change(self):
        self.assertIn("rost ayting", assistant.SYSTEM_PROMPT)
        self.assertIn("inson yoki akkaunt egasining o‘zi deb da’vo qilmang", assistant.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
