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
        self.assertIn("Odatda bir gap yetadi", assistant.SYSTEM_PROMPT)
        self.assertIn("What makes a reply sound like a bot", assistant.SKILL_PROMPT)
        self.assertIn("Small talk examples", assistant.SKILL_PROMPT)

    def test_skill_forbids_unprompted_offers_and_meta_comments(self):
        skill = assistant.SKILL_PROMPT
        self.assertIn("kredit boyicha yordam kerakmidi?", skill)
        self.assertIn("yana salom", skill)
        self.assertIn("The one test", skill)
        self.assertIn("so‘ramagan xizmat yoki mavzuni", assistant.SYSTEM_PROMPT)

    def test_skill_has_plain_greeting_examples(self):
        skill = assistant.SKILL_PROMPT
        self.assertIn("salom  →  salom", skill)
        self.assertIn("Never (each of these is a bot)", skill)

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

    def test_clarifying_questions_name_the_options(self):
        skill = assistant.SKILL_PROMPT
        self.assertIn("Clarifying questions: name the options", skill)
        self.assertIn("Avto salondan yangi mashinami, yoki bozordan (ikkinchi qol)mi?", skill)
        self.assertIn("ask one short question that names the options", skill)
        self.assertIn("Clarifying questions: name the options", assistant.PUBLIC_SKILL_PROMPT)

    def test_named_model_is_not_asked_for_again(self):
        self.assertIn("Never ask for a detail the user already gave", assistant.SKILL_PROMPT)
        self.assertIn("Tracker", assistant.SKILL_PROMPT)
        self.assertIn("the make is already known", assistant.SKILL_PROMPT)

    def test_real_uzbek_texting_skill_is_loaded_last(self):
        skill = assistant.SKILL_PROMPT
        self.assertIn("Real Uzbek texting", skill)
        self.assertIn("> Good: ha, shunaqa", skill)
        self.assertIn("Real Uzbek texting", assistant.PUBLIC_SKILL_PROMPT)
        for phrase in ("tuzukman, ishlar ham joyida. ozingizchi?", "Javob qaytarish",
                       "Never answer \"yoq\" to \"avtomatmi\""):
            self.assertIn(phrase, skill)
        self.assertIn("men Shadow AI, egasining yordamchisiman", skill)

    def test_spoken_not_literary_uzbek(self):
        self.assertIn("Spoken, not literary", assistant.SKILL_PROMPT)
        self.assertIn("og‘zaki o‘zbekcha", assistant.SYSTEM_PROMPT)
        self.assertNotIn("ravon, tabiiy va zamonaviy", assistant.SYSTEM_PROMPT)
        for pair in ("ishlar qalay (not \"ishlaringiz qanday\")", "nega (not \"nima uchun\")", "lekin (not \"ammo/biroq\")"):
            self.assertIn(pair, assistant.SKILL_PROMPT)

    def test_spoken_is_the_main_style_in_every_prompt(self):
        for prompt in (assistant.SKILL_PROMPT, assistant.PUBLIC_SKILL_PROMPT, assistant.GREETING_STYLE):
            self.assertIn("Main style: spoken, never literary", prompt)

    def test_no_literary_or_official_style_exception_remains(self):
        text = assistant.SYSTEM_PROMPT + assistant.SKILL_PROMPT
        self.assertIn("qattiq rasmiy, kitobiy yoki botga o‘xshamasin", assistant.SYSTEM_PROMPT)
        self.assertIn("no exceptions", assistant.SKILL_PROMPT)
        self.assertNotIn("literary language only for", text)
        self.assertNotIn("exception, used only for a lecture", text)
        self.assertNotIn("an official letter stays formal", text)
        from shadow.agents import AGENTS
        for agent in AGENTS:
            self.assertNotIn("rasmiy uslubda yozing", agent.instructions)

    def test_bank_guides_are_only_loaded_when_the_chat_is_about_banking(self):
        small_talk = assistant.skill_prompt_for("Chat: Aziz\n\nAziz: salom, qalaysiz\n\nJavob: nima gap")
        self.assertNotIn("Davr Bank car loans and microloans", small_talk)
        self.assertNotIn("Davr Bank and payments guide", small_talk)
        self.assertIn("Real Uzbek texting", small_talk)
        self.assertIn("Natural, human-sounding conversation", small_talk)
        self.assertLess(len(small_talk), len(assistant.SKILL_PROMPT) * 0.7)
        for text in ("avtokredit olmoqchiman", "Kredit qancha foiz?", "O‘TKAZMA qilmoqchiman", "карта ишламаяпти", "davr bank"):
            full = assistant.skill_prompt_for(text)
            self.assertEqual(full, assistant.SKILL_PROMPT, text)
        self.assertEqual(assistant.skill_prompt_for("salom", "Rol: Davr Bank bo‘yicha maslahatchi."), assistant.SKILL_PROMPT)

    def test_replies_are_typed_like_a_phone_without_apostrophes(self):
        from shadow.humanize import phone_text, split_parts
        self.assertEqual(phone_text("O‘zingizning ish’laringiz ʻbo‘ladi"), "Ozingizning ishlaringiz boladi")
        self.assertEqual(split_parts("o‘zingiz-chi? || bo‘ladi"), ["ozingizchi?", "boladi"])

    def test_honesty_rules_survive_the_style_change(self):
        self.assertIn("rost ayting", assistant.SYSTEM_PROMPT)
        self.assertIn("inson yoki akkaunt egasining o‘zi deb da’vo qilmang", assistant.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
