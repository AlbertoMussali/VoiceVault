from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.openai_summary import generate_summary_sentences
from app.settings import get_settings


def _fake_chat_response(sentences: list[dict[str, object]]) -> bytes:
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"sentences": sentences}),
                }
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


class OpenAiSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_openai_api_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-openai-key"
        get_settings.cache_clear()

    def tearDown(self) -> None:
        if self.original_openai_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.original_openai_api_key
        get_settings.cache_clear()

    def test_summary_truncates_to_five_sentences(self) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = _fake_chat_response(
            [
                {"text": f"Sentence {index}", "snippet_ids": [f"s{index}"]}
                for index in range(1, 7)
            ]
        )
        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_response
        mock_context.__exit__.return_value = None

        with patch("app.openai_summary.request.urlopen", return_value=mock_context):
            result = generate_summary_sentences(
                query_text="wins this week",
                sources=[{"snippet_id": "s1", "snippet_text": "win one"}],
            )

        self.assertEqual(len(result), 5)

    def test_summary_requires_at_least_three_sentences(self) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = _fake_chat_response(
            [
                {"text": "One", "snippet_ids": ["s1"]},
                {"text": "Two", "snippet_ids": ["s2"]},
            ]
        )
        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_response
        mock_context.__exit__.return_value = None

        with patch("app.openai_summary.request.urlopen", return_value=mock_context):
            with self.assertRaises(RuntimeError) as context:
                generate_summary_sentences(
                    query_text="wins this week",
                    sources=[{"snippet_id": "s1", "snippet_text": "win one"}],
                )

        self.assertIn("at least 3 sentences", str(context.exception))


if __name__ == "__main__":
    unittest.main()
