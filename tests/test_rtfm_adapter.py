import unittest
from unittest.mock import patch, MagicMock
import subprocess
import json

from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.rtfm_adapter import RTFMAdapter, RTFMAdapterError

class TestRTFMAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = RTFMAdapter()
        self.adapter.resolved_rtfm = "rtfm"

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_success(self, mock_run):
        mock_output = {
            "results": [
                {
                    "rank": 1,
                    "score": 4.5,
                    "chunk": {
                        "book_file": "docs/architecture.md",
                        "line_start": 10,
                        "line_end": 20,
                        "content": "This is a test snippet.",
                        "chapter_title": "Architecture",
                        "book_title": "Test Book"
                    }
                }
            ]
        }

        mock_process = MagicMock()
        mock_process.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_process

        results = self.adapter.search("test query", corpus="test_corpus", limit=1)

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], RTFMResult)
        self.assertEqual(results[0].path, "docs/architecture.md")
        self.assertEqual(results[0].score, 4.5)
        self.assertEqual(results[0].snippet, "This is a test snippet.")

        mock_run.assert_called_once_with(
            ["rtfm", "search", "test query", "--corpus", "test_corpus", "--limit", "1", "--format", "json"],
            capture_output=True, text=True, check=True
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        
        with self.assertRaises(RTFMAdapterError) as context:
            self.adapter.search("test", corpus="corpus", limit=5)
            
        self.assertIn("RTFM CLI not found", str(context.exception))

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_called_process_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd=["rtfm"], stderr="Some error")
        
        with self.assertRaises(RTFMAdapterError) as context:
            self.adapter.search("test", corpus="corpus", limit=5)
            
        self.assertIn("Some error", str(context.exception))

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_invalid_json(self, mock_run):
        mock_process = MagicMock()
        mock_process.stdout = "Invalid JSON output"
        mock_run.return_value = mock_process
        
        with self.assertRaises(RTFMAdapterError) as context:
            self.adapter.search("test", corpus="corpus", limit=5)
            
        self.assertIn("Failed to parse JSON output", str(context.exception))

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_sync_success(self, mock_run):
        mock_run.return_value = MagicMock()
        
        result = self.adapter.sync("/project", corpus="test_corpus")
        self.assertIsNone(result)
        
        mock_run.assert_called_once_with(
            ["rtfm", "sync", "/project", "--corpus", "test_corpus"],
            capture_output=True, text=True, check=True
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_sync_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd=["rtfm"], stderr="Sync failed")
        
        with self.assertRaises(RTFMAdapterError):
            self.adapter.sync("/project", corpus="test_corpus")

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_context_retrieval(self, mock_run):
        mock_process = MagicMock()
        mock_process.stdout = "Retrieved context lines."
        mock_run.return_value = mock_process
        
        result = self.adapter.context("path/to/file.md", 1, 10)
        self.assertEqual(result, "Retrieved context lines.")
        
        mock_run.assert_called_once_with(
            ["rtfm", "context", "path/to/file.md", "--line-start", "1", "--line-end", "10"],
            capture_output=True, text=True, check=True
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_expand(self, mock_run):
        mock_process = MagicMock()
        mock_process.stdout = "Expanded result content."
        mock_run.return_value = mock_process
        
        result = self.adapter.expand("res_123")
        self.assertEqual(result, "Expanded result content.")
        
        mock_run.assert_called_once_with(
            ["rtfm", "expand", "res_123"],
            capture_output=True, text=True, check=True
        )

if __name__ == "__main__":
    unittest.main()
