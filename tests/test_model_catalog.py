import json
import os
import unittest
from unittest import mock

from shadow import config
from shadow.config import AVAILABLE_MODELS, SUPPORTED_OPENAI_MODELS, Settings


class ModelCatalogTests(unittest.TestCase):
    def test_requested_models_are_selectable(self):
        for model_id in ("gpt-5-mini", "gpt-6-luna", "gpt-5.6-luna"):
            self.assertIn(model_id, SUPPORTED_OPENAI_MODELS)
        labels = dict(AVAILABLE_MODELS)
        self.assertEqual(labels["gpt-5.6-luna"], "GPT-5.6 Luna")
        # The API answers 404 model_not_found for "gpt-reserve", so it is not offered.
        self.assertNotIn("gpt-reserve", SUPPORTED_OPENAI_MODELS)

    def test_extra_models_from_env_are_validated(self):
        with mock.patch.dict(os.environ, {"EXTRA_OPENAI_MODELS": "gpt-9-new, bad id!, gpt-6-luna ,x"}):
            extras = dict(config._extra_models())
        self.assertIn("gpt-9-new", extras)
        self.assertIn("x", extras)
        self.assertNotIn("bad id!", extras)
        self.assertNotIn("gpt-6-luna", extras)  # already in the catalog

    def test_saved_selection_with_new_models_loads(self):
        selection = json.dumps({"openai_model": "gpt-6-luna", "complex_openai_model": "gpt-5.6-luna"})
        with mock.patch.dict(os.environ, {"SHADOW_MODEL_SELECTION": selection, "SHADOW_STATE_FILE": ""}):
            settings = Settings.from_env()
        self.assertEqual((settings.openai_model, settings.complex_openai_model), ("gpt-6-luna", "gpt-5.6-luna"))

    def test_a_saved_model_that_is_no_longer_offered_does_not_stop_startup(self):
        selection = json.dumps({"openai_model": "gpt-reserve", "complex_openai_model": "gpt-5.6-luna"})
        clean = {k: v for k, v in os.environ.items() if k not in ("OPENAI_MODEL", "OPENAI_COMPLEX_MODEL")}
        clean.update({"SHADOW_MODEL_SELECTION": selection, "SHADOW_STATE_FILE": ""})
        with mock.patch.dict(os.environ, clean, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.openai_model, "gpt-5-mini")  # default for the dropped slot
        self.assertEqual(settings.complex_openai_model, "gpt-5.6-luna")  # valid entry kept

    def test_corrupt_selection_is_still_rejected(self):
        with mock.patch.dict(os.environ, {"SHADOW_MODEL_SELECTION": "[1, 2]", "SHADOW_STATE_FILE": ""}):
            with self.assertRaises(ValueError):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
