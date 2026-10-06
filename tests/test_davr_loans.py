import re
import unittest

from shadow import assistant

CAR_OFFERS = ("Smart Auto", "UzAuto", "Comfort avto", "Smart Roodell", "KIA tezkor", "ROODELL AUTO", "ADM Avto",
              "SPECIAL AUTO", "NAVBATSIZ AVTO", "GLOBAL AVTO", "IKKILAMCHI AVTO", "BYD", "ROODELL 15")
MICRO_OFFERS = ("Davr Online", "Davr Online 2", "Universal mikroqarz", "Mikroqarz 100", "Nuroniy",
                "To‘lov uchun mikroqarz", "Maxsus mikroqarz", "Mini kredit", "Mini kredit 2", "Mini kredit 3")


def annuity(principal: float, annual_percent: float, months: int) -> float:
    r = annual_percent / 100 / 12
    return principal / months if r == 0 else principal * r / (1 - (1 + r) ** -months)


def differential(principal: float, annual_percent: float, months: int) -> tuple[float, float, float]:
    r = annual_percent / 100 / 12
    step = principal / months
    total = principal + sum((principal - step * k) * r for k in range(months))
    return step + principal * r, step + step * r, total


def spaced(value: float) -> str:
    return f"{round(value):,}".replace(",", " ")


class DavrLoansSkillTests(unittest.TestCase):
    def setUp(self):
        self.skill = assistant.SKILL_PROMPT

    def test_every_offer_is_named_in_both_prompts(self):
        for prompt in (assistant.SKILL_PROMPT, assistant.PUBLIC_SKILL_PROMPT):
            for name in CAR_OFFERS + MICRO_OFFERS:
                self.assertIn(name, prompt, name)

    def test_key_rates_down_payments_and_conflicts_are_stated(self):
        for text in ("26.99%", "25.99%", "0–10.9%", "up to 72 months", "600 mln", "134%", "KATM at least 300",
                     "37%", "45%", "33%", "older sheet says 38%", "older sheet says 26%",
                     "Navoi and Almalyk", "30-day grace period"):
            self.assertIn(text, self.skill, text)

    def test_it_forbids_inventing_the_exact_rate_or_extra_costs(self):
        self.assertIn("never pick a number inside a range", self.skill)
        self.assertIn("no late-payment penalty, no commission and no insurance price", self.skill)
        self.assertIn("never mental arithmetic", self.skill)

    def test_worked_examples_match_the_formulas(self):
        m = annuity(212_500_000, 25.99, 72)
        self.assertIn(f"about {spaced(m)} so‘m a month", self.skill)
        self.assertIn(f"about {spaced(m * 72)} so‘m in total", self.skill)
        first, last, total = differential(280_000_000, 26, 60)
        for value in (first, last, total):
            self.assertIn(spaced(value), self.skill)
        self.assertIn(spaced(annuity(105_000_000, 0, 48)), self.skill)
        m = annuity(153_000_000, 26.99, 60)
        self.assertIn(spaced(m), self.skill)
        self.assertIn(spaced(m * 60), self.skill)
        m = annuity(20_000_000, 37, 24)
        self.assertIn(spaced(m), self.skill)
        self.assertIn(spaced(m * 24), self.skill)
        m = annuity(4_000_000, 26, 12)
        self.assertIn(spaced(m), self.skill)
        self.assertIn(spaced(m * 12), self.skill)

    def test_bank_only_and_honesty_rules_still_apply(self):
        self.assertIn("Davr Bank only", self.skill)
        self.assertNotIn("Kapitalbank", self.skill)
        self.assertIn("Never invent", self.skill)


if __name__ == "__main__":
    unittest.main()
