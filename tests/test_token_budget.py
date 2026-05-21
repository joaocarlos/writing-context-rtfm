import unittest
from unittest.mock import patch, MagicMock
from writing_context_rtfm.token_budget import estimate_tokens, estimate_span_tokens

class TestTokenBudget(unittest.TestCase):
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
            self.assertEqual(estimate_tokens("Hello World!"), 3) # 12 // 4 = 3
            self.assertEqual(estimate_tokens(""), 1) # Max(1, 0) = 1

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
