import shutil
import tempfile
import unittest
from pathlib import Path

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

        # We expect a preamble node, and two main section nodes:
        # section_introduction (spanning through subsection Details), section_conclusion
        self.assertIn("section_main_preamble", nodes)
        self.assertIn("section_introduction", nodes)
        self.assertIn("section_conclusion", nodes)
        self.assertNotIn("section_details", nodes)

        preamble = nodes["section_main_preamble"]
        self.assertIn("key1", preamble.citations)

        intro = nodes["section_introduction"]
        self.assertEqual(intro.title, "Introduction")
        self.assertEqual(intro.selector, "sec:intro")
        self.assertIn("eq:1", intro.equations)
        self.assertEqual(intro.level, 2)  # section

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

        # Expect main sections section_overview and section_summary
        self.assertIn("section_overview", nodes)
        self.assertIn("section_summary", nodes)
        self.assertNotIn("section_approach", nodes)

        overview = nodes["section_overview"]
        self.assertEqual(overview.source_path, "main.tex")
        self.assertIn("fig:flow", overview.figures)

    def test_parse_markdown(self):
        doc_md = self.test_dir / "doc.md"
        doc_md.write_text(
            """# Title

Some intro text with citation @ref1.

# Introduction {#sec-intro}

Intro body text.

## Methodology

Methodology body text.
""",
            encoding="utf-8",
        )

        parser = VirtualDocumentParser(str(self.test_dir))
        nodes = parser.parse("doc.md")

        self.assertIn("section_title", nodes)
        self.assertIn("section_introduction", nodes)
        self.assertNotIn("section_methodology", nodes)

        title = nodes["section_title"]
        self.assertEqual(title.level, 1)
        self.assertIn("ref1", title.citations)

        intro = nodes["section_introduction"]
        self.assertEqual(intro.level, 1)
        self.assertEqual(intro.selector, "sec-intro")

    def test_ast_caching(self):
        from writing_context_rtfm.virtual_doc import _LATEX_AST_CACHE

        main_tex = self.test_dir / "cached_main.tex"
        main_tex.write_text(
            r"""
            \documentclass{article}
            \begin{document}
            \section{Cached Section}\label{sec:cached}
            Body text.
            \end{document}
            """,
            encoding="utf-8",
        )

        parser = VirtualDocumentParser(str(self.test_dir))
        # First parse populates cache
        nodes1 = parser.parse("cached_main.tex")
        stat = main_tex.stat()
        cache_key = (str(main_tex.resolve()), stat.st_mtime, stat.st_size)
        self.assertIn(cache_key, _LATEX_AST_CACHE)

        # Second parse uses cache
        nodes2 = parser.parse("cached_main.tex")
        self.assertEqual(list(nodes1.keys()), list(nodes2.keys()))
        self.assertEqual(
            nodes1["section_cached_section"].content_hash,
            nodes2["section_cached_section"].content_hash,
        )
