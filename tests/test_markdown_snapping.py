import tempfile
import unittest
from pathlib import Path

from writing_context_rtfm.virtual_doc import VirtualDocumentParser


class TestMarkdownSnapping(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # Create markdown file with display math ($$), code blocks (```), and tables
        self.md_path = self.root / "chapter.md"
        self.md_path.write_text(
            "# Chapter 1\n\n"
            "Here is some regular text.\n\n"
            "$$\n"
            "\\int_{0}^{\\infty} e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}\n"
            "$$\n\n"
            "Some text between math and code.\n\n"
            "```python\n"
            "def calculate_loss(y_true, y_pred):\n"
            "    return np.mean((y_true - y_pred) ** 2)\n"
            "```\n\n"
            "Text before table.\n\n"
            "| Model | Accuracy | F1 |\n"
            "| --- | --- | --- |\n"
            "| Baseline | 85.2 | 84.8 |\n"
            "| Ours | 91.4 | 90.9 |\n",
            encoding="utf-8",
        )

        self.parser = VirtualDocumentParser(str(self.root))
        self.parser.parse("chapter.md")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_snap_to_display_math(self):
        # Line 6 is inside the $$ block (lines 5-7)
        s_start, s_end = self.parser.snap_to_environment("chapter.md", 6, 6)
        self.assertEqual(s_start, 5)
        self.assertEqual(s_end, 7)

    def test_snap_to_code_block(self):
        # Line 12 is inside python code block (lines 11-14)
        s_start, s_end = self.parser.snap_to_environment("chapter.md", 12, 13)
        self.assertEqual(s_start, 11)
        self.assertEqual(s_end, 14)

    def test_snap_to_markdown_table(self):
        # Line 19 is inside the table (lines 18-21)
        s_start, s_end = self.parser.snap_to_environment("chapter.md", 19, 20)
        self.assertEqual(s_start, 18)
        self.assertEqual(s_end, 21)
