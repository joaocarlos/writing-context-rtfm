import unittest
from unittest.mock import MagicMock, patch

import httpx

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.semantic_extractor import (
    MissingAPIKeyError,
    extract_semantic_metadata,
)


class TestSemanticExtractor(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root="."),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=True, path=".writing-context/cache.sqlite"),
            section_cards=SectionCardsConfig(path=".writing-context/section_cards.yaml"),
        )

    @patch("writing_context_rtfm.semantic_extractor.get_api_key")
    def test_missing_api_key_raises_error(self, mock_get_key):
        mock_get_key.return_value = None

        with self.assertRaises(MissingAPIKeyError) as context:
            extract_semantic_metadata("some content", self.config)

        self.assertIn("OpenAI API key not found", str(context.exception))

    @patch("writing_context_rtfm.semantic_extractor.get_api_key")
    @patch("httpx.post")
    def test_successful_semantic_extraction(self, mock_post, mock_get_key):
        mock_get_key.return_value = "fake-key"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"rhetorical_role": "results", "purpose": "Show results", "key_terms": [], "facts": [], "constraints": []}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        res = extract_semantic_metadata("dummy content", self.config)
        self.assertEqual(res["rhetorical_role"], "results")
        self.assertEqual(res["purpose"], "Show results")

    @patch("writing_context_rtfm.semantic_extractor.get_api_key")
    @patch("httpx.post")
    def test_unauthorized_api_key_raises_clean_error(self, mock_post, mock_get_key):
        mock_get_key.return_value = "invalid-key"

        mock_response = MagicMock()
        mock_response.status_code = 401
        # Set up a Mock response that raises HTTPStatusError when raise_for_status is called
        http_error = httpx.HTTPStatusError(
            message="401 Unauthorized", request=MagicMock(), response=mock_response
        )
        mock_response.raise_for_status.side_effect = http_error
        mock_post.return_value = mock_response

        with self.assertRaises(MissingAPIKeyError) as context:
            extract_semantic_metadata("dummy content", self.config)

        self.assertIn("OpenAI API key is invalid", str(context.exception))
