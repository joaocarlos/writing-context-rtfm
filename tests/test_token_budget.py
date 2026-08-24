import unittest
from unittest.mock import MagicMock, patch

from writing_context_rtfm import token_budget
from writing_context_rtfm.token_budget import estimate_span_tokens, estimate_tokens


class TestTokenBudget(unittest.TestCase):
    def test_tokenizer_initialization_error_uses_offline_fallback(self):
        with patch("tiktoken.get_encoding", side_effect=OSError("offline")):
            self.assertIsNone(token_budget._load_encoding())

    def test_estimate_span_tokens(self):
        # Default behavior: 15 tokens per line
        # 1 line (start=10, end=10)
        self.assertEqual(estimate_span_tokens(10, 10), 15)
        # 3 lines (start=10, end=12)
        self.assertEqual(estimate_span_tokens(10, 12), 45)
        # Empty/inverted bounds clamped to 1 line
        self.assertEqual(estimate_span_tokens(10, 5), 15)

    def test_estimate_tokens_fallback(self):
        # When _ENCODING is None, should use len(text) // 4
        with patch("writing_context_rtfm.token_budget._ENCODING", None):
            self.assertEqual(estimate_tokens("Hello World!"), 3)  # 12 // 4 = 3
            self.assertEqual(estimate_tokens(""), 1)  # Max(1, 0) = 1

    def test_estimate_tokens_with_tiktoken(self):
        # Create a mock encoder
        mock_encoding = MagicMock()
        mock_encoding.encode.return_value = [1, 2, 3, 4]

        with patch("writing_context_rtfm.token_budget._ENCODING", mock_encoding):
            res = estimate_tokens("Mock text")
            self.assertEqual(res, 4)
            mock_encoding.encode.assert_called_once_with("Mock text", disallowed_special=())

    def test_estimate_tokens_tiktoken_error_fallback(self):
        # If encoding raises an exception, should fallback to len(text) // 4
        mock_encoding = MagicMock()
        mock_encoding.encode.side_effect = Exception("Some encode error")

        with patch("writing_context_rtfm.token_budget._ENCODING", mock_encoding):
            # len("Some text here") is 14. 14 // 4 = 3.
            res = estimate_tokens("Some text here")
            self.assertEqual(res, 3)

    def test_strict_budget_generator(self):
        from writing_context_rtfm.config import (
            AppConfig,
            CacheConfig,
            ContextConfig,
            RTFMConfig,
            SectionCardsConfig,
        )
        from writing_context_rtfm.context_pack import ContextPackGenerator
        from writing_context_rtfm.schemas import RTFMResult

        config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root="."),
            context=ContextConfig(),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig(path="dummy.yaml"),
        )
        adapter = MagicMock()
        store = MagicMock()

        # Two 400-token candidate spans (approx 30 lines each = 450 tokens)
        mock_r1 = RTFMResult(
            path="file1.tex",
            line_start=1,
            line_end=30,
            snippet="word " * 300,
            score=0.9,
            metadata={},
        )
        mock_r2 = RTFMResult(
            path="file2.tex",
            line_start=1,
            line_end=30,
            snippet="word " * 300,
            score=0.8,
            metadata={},
        )
        adapter.search.return_value = [mock_r1, mock_r2]

        generator = ContextPackGenerator(config, None, adapter, store)

        # In strict_budget mode with 500 token budget, only 1 span fits
        pack_strict = generator.generate(
            task="write", target=None, token_budget=500, strict_budget=True
        )
        self.assertEqual(len(pack_strict.source_spans), 1)
        self.assertTrue(any("strictly respect the token budget" in w for w in pack_strict.warnings))
