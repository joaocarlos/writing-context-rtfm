import unittest
from unittest.mock import MagicMock, patch

import httpx

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    GeneratorConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.semantic_extractor import (
    MissingAPIKeyError,
    extract_semantic_metadata,
)


class TestSemanticExtractorFallback(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root="."),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=True, path=".writing-context/cache.sqlite"),
            section_cards=SectionCardsConfig(path=".writing-context/section_cards.yaml"),
            generator=GeneratorConfig(),  # defaults: model="gpt-4o-mini", api_base="https://api.openai.com/v1"
        )

    @patch("writing_context_rtfm.semantic_extractor.get_openai_api_key")
    @patch("httpx.post")
    def test_openai_fallback_used_first(self, mock_post, mock_get_openai):
        mock_get_openai.return_value = "fake-openai-key"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"rhetorical_role": "results", "purpose": "Show results"}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        res = extract_semantic_metadata("dummy content", self.config)
        self.assertEqual(res["rhetorical_role"], "results")

        # Verify call went to OpenAI
        self.assertIsNotNone(mock_post.call_args)
        call_args, call_kwargs = mock_post.call_args
        self.assertEqual(call_args[0], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer fake-openai-key")
        self.assertEqual(call_kwargs["json"]["model"], "gpt-4o-mini")

    @patch("writing_context_rtfm.semantic_extractor.get_openai_api_key")
    @patch("writing_context_rtfm.semantic_extractor.get_hf_api_token")
    @patch("httpx.post")
    def test_huggingface_fallback_used_second(self, mock_post, mock_get_hf, mock_get_openai):
        mock_get_openai.return_value = None
        mock_get_hf.return_value = "fake-hf-token"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"rhetorical_role": "methodology", "purpose": "Show method"}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        res = extract_semantic_metadata("dummy content", self.config)
        self.assertEqual(res["rhetorical_role"], "methodology")

        # Verify call went to Hugging Face
        self.assertIsNotNone(mock_post.call_args)
        call_args, call_kwargs = mock_post.call_args
        self.assertEqual(call_args[0], "https://api-inference.huggingface.co/v1/chat/completions")
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer fake-hf-token")
        self.assertEqual(call_kwargs["json"]["model"], "Qwen/Qwen2.5-Coder-7B-Instruct")

    @patch("writing_context_rtfm.semantic_extractor.get_openai_api_key")
    @patch("writing_context_rtfm.semantic_extractor.get_hf_api_token")
    @patch("httpx.get")
    @patch("httpx.post")
    def test_ollama_fallback_used_third(self, mock_post, mock_get, mock_get_hf, mock_get_openai):
        mock_get_openai.return_value = None
        mock_get_hf.return_value = None

        # Mock Ollama tags response
        mock_get_response = MagicMock()
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = {
            "models": [{"name": "phi3:latest"}, {"name": "qwen2.5-coder:latest"}]
        }
        mock_get.return_value = mock_get_response

        # Mock Ollama chat response
        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"rhetorical_role": "limitations", "purpose": "Show limits"}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_post_response

        res = extract_semantic_metadata("dummy content", self.config)
        self.assertEqual(res["rhetorical_role"], "limitations")

        # Verify Ollama check
        mock_get.assert_called_with("http://localhost:11434/api/tags", timeout=1.0)

        # Verify Ollama chat query was routed using preferred priority model
        self.assertIsNotNone(mock_post.call_args)
        call_args, call_kwargs = mock_post.call_args
        self.assertEqual(call_args[0], "http://localhost:11434/v1/chat/completions")
        self.assertEqual(call_kwargs["json"]["model"], "qwen2.5-coder:latest")

    @patch("writing_context_rtfm.semantic_extractor.get_openai_api_key")
    @patch("writing_context_rtfm.semantic_extractor.get_hf_api_token")
    @patch("httpx.get")
    def test_offline_fallback_last_resort(self, mock_get, mock_get_hf, mock_get_openai):
        mock_get_openai.return_value = None
        mock_get_hf.return_value = None
        mock_get.side_effect = httpx.RequestError("Connection refused")

        with self.assertRaises(MissingAPIKeyError) as context:
            extract_semantic_metadata("dummy content", self.config)

        self.assertIn("No OpenAI API key or Hugging Face token found", str(context.exception))

    @patch("writing_context_rtfm.semantic_extractor.get_openai_api_key")
    @patch("httpx.post")
    def test_custom_config_honored(self, mock_post, mock_get_openai):
        # Configure a custom Ollama/Local config
        custom_config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root="."),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=True, path=".writing-context/cache.sqlite"),
            section_cards=SectionCardsConfig(path=".writing-context/section_cards.yaml"),
            generator=GeneratorConfig(
                model="my-custom-phi-model",
                api_base="http://127.0.0.1:8080/v1",
                api_key="my-secret-token",
            ),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"rhetorical_role": "appendix", "purpose": "Show appendix"}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        res = extract_semantic_metadata("dummy content", custom_config)
        self.assertEqual(res["rhetorical_role"], "appendix")

        # Verify custom config settings used
        self.assertIsNotNone(mock_post.call_args)
        call_args, call_kwargs = mock_post.call_args
        self.assertEqual(call_args[0], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer my-secret-token")
        self.assertEqual(call_kwargs["json"]["model"], "my-custom-phi-model")
