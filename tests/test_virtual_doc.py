import unittest
from pathlib import Path
import shutil
import tempfile
from writing_context_rtfm.virtual_doc import VirtualDocumentParser


class TestVirtualDocumentParser(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_parse_monolithic_latex(self):
        main_tex = self.test_dir / "main.tex"
        main_tex.write_text(
            r"""
            \documentclass{article}
            \begin{document}
            This is pre-heading text with a citation \cite{key1}.
            \section{Introduction}\label{sec:intro}
            Intro text with an equation:
            \begin{equation}\label{eq:1}
            e = mc^2
            \end{equation}
            \subsection{Details}
            Details text.
            \section{Conclusion}
            End text.
            \end{document}
            """,
            encoding="utf-8",
        )

        parser = VirtualDocumentParser(str(self.test_dir))
        nodes = parser.parse("main.tex")

        # We expect a preamble node, and three section nodes:
        # section_introduction, section_details, section_conclusion
        self.assertIn("section_main_preamble", nodes)
        self.assertIn("section_introduction", nodes)
        self.assertIn("section_details", nodes)
        self.assertIn("section_conclusion", nodes)

        preamble = nodes["section_main_preamble"]
        self.assertIn("key1", preamble.citations)

        intro = nodes["section_introduction"]
        self.assertEqual(intro.title, "Introduction")
        self.assertEqual(intro.selector, "sec:intro")
        self.assertIn("eq:1", intro.equations)
        self.assertEqual(intro.level, 2)  # section

        details = nodes["section_details"]
        self.assertEqual(details.title, "Details")
        self.assertEqual(details.level, 3)  # subsection
        self.assertEqual(details.parent, "section_introduction")
        self.assertIn("section_details", intro.children)

        conclusion = nodes["section_conclusion"]
        self.assertEqual(conclusion.title, "Conclusion")
        self.assertEqual(conclusion.parent, "document_main")

    def test_parse_modular_latex(self):
        main_tex = self.test_dir / "main.tex"
        main_tex.write_text(
            r"""
            \documentclass{article}
            \begin{document}
            \section{Overview}
            \input{sub.tex}
            \section{Summary}
            \end{document}
            """,
            encoding="utf-8",
        )

        sub_tex = self.test_dir / "sub.tex"
        sub_tex.write_text(
            r"""
            Text at the start of sub.tex.
            \subsection{Approach}\label{sec:approach}
            Approach text with figure:
            \begin{figure}\label{fig:flow}
            \end{figure}
            """,
            encoding="utf-8",
        )

        parser = VirtualDocumentParser(str(self.test_dir))
        nodes = parser.parse("main.tex")

        # Expect section_overview, section_approach, section_summary
        self.assertIn("section_overview", nodes)
        self.assertIn("section_approach", nodes)
        self.assertIn("section_summary", nodes)

        overview = nodes["section_overview"]
        self.assertEqual(overview.source_path, "main.tex")

        approach = nodes["section_approach"]
        self.assertEqual(approach.title, "Approach")
        self.assertEqual(approach.source_path, "sub.tex")
        self.assertEqual(approach.selector, "sec:approach")
        self.assertIn("fig:flow", approach.figures)
        self.assertEqual(approach.parent, "section_overview")
        self.assertIn("section_approach", overview.children)

    def test_parse_markdown(self):
        doc_md = self.test_dir / "doc.md"
        doc_md.write_text(
            """# Title

Some intro text with citation @ref1.

## Introduction {#sec-intro}

Intro body text.

### Methodology

Methodology body text.
""",
            encoding="utf-8",
        )

        parser = VirtualDocumentParser(str(self.test_dir))
        nodes = parser.parse("doc.md")

        self.assertIn("section_title", nodes)
        self.assertIn("section_introduction", nodes)
        self.assertIn("section_methodology", nodes)

        title = nodes["section_title"]
        self.assertEqual(title.level, 1)
        self.assertIn("ref1", title.citations)

        intro = nodes["section_introduction"]
        self.assertEqual(intro.level, 2)
        self.assertEqual(intro.selector, "sec-intro")
        self.assertEqual(intro.parent, "section_title")

        methodology = nodes["section_methodology"]
        self.assertEqual(methodology.level, 3)
        self.assertEqual(methodology.parent, "section_introduction")
