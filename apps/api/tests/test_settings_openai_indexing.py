from __future__ import annotations

import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import get_settings


class OpenAIIndexingSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_value = os.environ.get("OPENAI_INDEXING_MODEL")
        get_settings.cache_clear()

    def tearDown(self) -> None:
        if self.original_value is None:
            os.environ.pop("OPENAI_INDEXING_MODEL", None)
        else:
            os.environ["OPENAI_INDEXING_MODEL"] = self.original_value
        get_settings.cache_clear()

    def test_openai_indexing_model_uses_default(self) -> None:
        os.environ.pop("OPENAI_INDEXING_MODEL", None)
        get_settings.cache_clear()
        self.assertEqual(get_settings().openai_indexing_model, "gpt-4o-mini")

    def test_openai_indexing_model_respects_env_override(self) -> None:
        os.environ["OPENAI_INDEXING_MODEL"] = "custom-model"
        get_settings.cache_clear()
        self.assertEqual(get_settings().openai_indexing_model, "custom-model")


if __name__ == "__main__":
    unittest.main()
