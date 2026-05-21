import os
import json
import unittest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from writing_context_rtfm.latex import scan_latex_commands, build_reference_graph
from writing_context_rtfm.features import initialize_section_cards
from writing_context_rtfm.server import process_message
from writing_context_rtfm.cli import show_graph_command

class TestLatexParser(unittest.TestCase):
    def setUp(self):
        self.project_dir = Path("test_latex_project_temp")
        self.project_dir.mkdir(exist_ok=True)
        
    def tearDown(self):
        import shutil
        if self.project_dir.exists():
            shutil.rmtree(self.project_dir)

    def test_scan_latex_commands(self):
        # Basic scan test
        text = r"""
        This is a reference to \ref{sec:intro} and a citation \cite{smith2020}.
        % This is a comment containing \label{commented_label} and \ref{commented_ref}.
        And an environment:
        \begin{equation}
        y = x^2 \label{eq:quad}
        \end{equation}
        Inline math $a + b$ and display math $$c + d$$.
        """
        cmds = scan_latex_commands(text)
        
        # Verify macros found
        self.assertIn(r"\ref{sec:intro}", cmds)
        self.assertIn(r"\cite{smith2020}", cmds)
        self.assertIn(r"\label{eq:quad}", cmds)
        
        # Verify commented out macros are NOT found
        self.assertNotIn(r"\label{commented_label}", cmds)
        self.assertNotIn(r"\ref{commented_ref}", cmds)
        
        # Verify math and environments
        self.assertIn("$a + b$", cmds)
        self.assertIn("$$c + d$$", cmds)
        self.assertTrue(any(c.startswith(r"\begin{equation}") for c in cmds))

    def test_build_reference_graph(self):
        # Create temp files
        main_tex = self.project_dir / "main.tex"
        main_tex.write_text(r"""
        \documentclass{article}
        \begin{document}
        \include{intro}
        \input{methods.tex}
        \cite{doe2021}
        \end{document}
        """, encoding="utf-8")
        
        intro_tex = self.project_dir / "intro.tex"
        intro_tex.write_text(r"""
        \section{Introduction}\label{sec:intro}
        Intro text referencing \ref{sec:methods}.
        """, encoding="utf-8")
        
        methods_tex = self.project_dir / "methods.tex"
        methods_tex.write_text(r"""
        \section{Methods}\label{sec:methods}
        Methods text referencing \ref{sec:intro} and citing \cite{smith2020, jones2019}.
        """, encoding="utf-8")
        
        graph = build_reference_graph(str(self.project_dir))
        
        # Verify files listed
        self.assertIn("main.tex", graph["files"])
        self.assertIn("intro.tex", graph["files"])
        self.assertIn("methods.tex", graph["files"])
        
        # Verify labels
        self.assertIn("sec:intro", graph["labels"])
        self.assertEqual(graph["labels"]["sec:intro"]["file"], "intro.tex")
        self.assertIn("sec:methods", graph["labels"])
        self.assertEqual(graph["labels"]["sec:methods"]["file"], "methods.tex")
        
        # Verify references
        self.assertIn("sec:methods", graph["references"]["intro.tex"])
        self.assertIn("sec:intro", graph["references"]["methods.tex"])
        
        # Verify citations (including comma splitting)
        self.assertIn("doe2021", graph["citations"]["main.tex"])
        self.assertIn("smith2020", graph["citations"]["methods.tex"])
        self.assertIn("jones2019", graph["citations"]["methods.tex"])
        
        # Verify file dependencies
        self.assertIn("intro.tex", graph["file_dependencies"]["main.tex"])
        self.assertIn("methods.tex", graph["file_dependencies"]["main.tex"])

    def test_initialize_section_cards_dependency_resolution(self):
        # Create temp files that have cross-references
        intro_tex = self.project_dir / "intro.tex"
        intro_tex.write_text(r"\section{Intro}\label{sec:intro} \ref{sec:results}", encoding="utf-8")
        
        results_tex = self.project_dir / "results.tex"
        results_tex.write_text(r"\section{Results}\label{sec:results} \ref{sec:intro}", encoding="utf-8")
        
        # Initialize
        res = initialize_section_cards(str(self.project_dir))
        self.assertEqual(res["status"], "success")
        
        # Check generated card dependencies
        sc_path = self.project_dir / ".writing-context" / "section_cards.yaml"
        self.assertTrue(sc_path.exists())
        with open(sc_path, "r") as f:
            data = yaml.safe_load(f)
            
        sections = data["sections"]
        self.assertIn("section_intro", sections)
        self.assertIn("section_results", sections)
        
        # section_intro has \ref{sec:results}, and sec:results is in results.tex (section_results)
        # So section_intro must depend on section_results
        self.assertIn("section_results", sections["section_intro"]["depends_on"])
        
        # section_results has \ref{sec:intro}, and sec:intro is in intro.tex (section_intro)
        # So section_results must depend on section_intro
        self.assertIn("section_intro", sections["section_results"]["depends_on"])

    def test_mcp_tool_get_manuscript_reference_graph(self):
        # Create a single tex file
        main_tex = self.project_dir / "main.tex"
        main_tex.write_text(r"\label{lbl:main}", encoding="utf-8")
        
        # We need to mock load_config to point to our temp project directory
        mock_config = MagicMock()
        mock_config.rtfm.project_root = str(self.project_dir)
        
        with patch("writing_context_rtfm.server.load_config", return_value=mock_config):
            req = {
                "method": "tools/call",
                "params": {
                    "name": "get_manuscript_reference_graph",
                    "arguments": {
                        "project_root": str(self.project_dir)
                    }
                },
                "id": "1"
            }
            resp_str = process_message(json.dumps(req))
            resp = json.loads(resp_str)
            
            self.assertIn("result", resp)
            result = resp["result"]
            self.assertIn("content", result)
            content_text = result["content"][0]["text"]
            graph = json.loads(content_text)
            
            self.assertIn("main.tex", graph["files"])
            self.assertIn("lbl:main", graph["labels"])

    def test_cli_show_graph(self):
        main_tex = self.project_dir / "main.tex"
        main_tex.write_text(r"\label{lbl:main} \ref{lbl:main} \cite{cite:main}", encoding="utf-8")
        
        # Mock args
        args = MagicMock()
        args.project_root = str(self.project_dir)
        args.format = "json"
        
        # We call show_graph_command. It prints to stdout. Let's capture prints
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            show_graph_command(args)
        out = f.getvalue()
        
        # Since format is json, it should be valid json
        parsed = json.loads(out)
        self.assertIn("graph", parsed)
        self.assertIn("main.tex", parsed["graph"]["files"])
