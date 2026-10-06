import json
import os
import unittest
from unittest import mock

from shadow import config
from shadow.config import AVAILABLE_MODELS, SUPPORTED_OPENAI_MODELS, Settings


class ModelCatalogTests(unittest.TestCase):
    def test_requested_models_are_selectable(self):
        for model_id in ("gpt-5-mini", "gpt-6-luna", "gpt-5.6-luna", "gpt-reserve"):
            self.assertIn(model_id, SUPPORTED_OPENAI_MODELS)
        labels = dict(AVAILABLE_MODELS)
        self.assertEqual(labels["gpt-5.6-luna"], "GPT-5.6 Luna")
        self.assertEqual(labels["gpt-reserve"], "GPT-Reserve")

    def test_extra_models_from_env_are_validated(self):
        with mock.patch.dict(os.environ, {"EXTRA_OPENAI_MODELS": "gpt-9-new, bad id!, gpt-reserve ,x" * 1}):
            extras = dict(config._extra_models())
        self.assertIn("gpt-9-new", extras)
        self.assertIn("x", extras)
        self.assertNotIn("bad id!", extras)
        self.assertNotIn("gpt-reserve", extras)  # already in the catalog

    def test_saved_selection_with_new_models_loads(self):
        selection = json.dumps({"openai_model": "gpt-reserve", "complex_openai_model": "gpt-5.6-luna"})
        with mock.patch.dict(os.environ, {"SHADOW_MODEL_SELECTION": selection, "SHADOW_STATE_FILE": ""}):
            settings = Settings.from_env()
        self.assertEqual((settings.openai_model, settings.complex_openai_model), ("gpt-reserve", "gpt-5.6-luna"))

    def test_unknown_selection_is_still_rejected(self):
        selection = json.dumps({"openai_model": "not-a-listed-model"})
        with mock.patch.dict(os.environ, {"SHADOW_MODEL_SELECTION": selection, "SHADOW_STATE_FILE": ""}):
            with self.assertRaises(ValueError):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
