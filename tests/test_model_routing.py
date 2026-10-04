import unittest

from shadow.model_routing import needs_reasoning_model


class ModelRoutingTests(unittest.TestCase):
    def test_everyday_messages_stay_on_default_model(self):
        for message in [
            "Salom", "Bugun ob-havo yaxshi ekan", "How was your weekend?",
            "Bugun ishda juda charchadim, dam olsam kerak shekilli. Sen nima deysan?",
            "What is 2 + 2?",
        ]:
            with self.subTest(message=message):
                self.assertFalse(needs_reasoning_model(message))

    def test_explicit_complex_work_uses_reasoning_model(self):
        for message in [
            "Bu tenglamani qadam-baqadam yechib, javobni isbotla",
            "Iltimos, ushbu loyihani chuqur tahlil qil va strategiya taklif et",
            "Сравни эти варианты и подробно объясни, почему",
            "Can you debug this error and compare alternative designs?",
        ]:
            with self.subTest(message=message):
                self.assertTrue(needs_reasoning_model(message))

    def test_documents_and_long_inputs_are_complex(self):
        self.assertTrue(needs_reasoning_model("Faylni ko‘rib chiq", has_document=True))
        self.assertTrue(needs_reasoning_model("Izoh " + "uzun matn " * 100))
        self.assertFalse(needs_reasoning_model("Qalesan?", has_document=False))


if __name__ == "__main__":
    unittest.main()
