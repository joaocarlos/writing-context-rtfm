from writing_context_rtfm.context_pack import (
    apply_mmr_diversity,
    apply_reciprocal_rank_fusion,
    compute_jaccard_similarity,
)
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.virtual_doc import VirtualDocumentParser


def test_compute_jaccard_similarity():
    text1 = "Quantum computing and error correction algorithms"
    text2 = "Algorithms for quantum error correction in NISQ devices"
    text3 = "Classical deep learning with convolutional neural networks"

    sim_high = compute_jaccard_similarity(text1, text2)
    sim_low = compute_jaccard_similarity(text1, text3)

    assert sim_high > 0.4
    assert sim_low < 0.15


def test_reciprocal_rank_fusion():
    span_a = SourceSpan(
        path="doc1.tex",
        line_start=1,
        line_end=10,
        reason="Stream 1 match",
        score=0.9,
        priority="supporting",
        source_role="target_text",
    )
    span_b = SourceSpan(
        path="doc2.tex",
        line_start=1,
        line_end=10,
        reason="Stream 1 second",
        score=0.7,
        priority="supporting",
        source_role="dependency",
    )
    span_c = SourceSpan(
        path="doc3.tex",
        line_start=1,
        line_end=10,
        reason="Stream 2 match",
        score=0.8,
        priority="supporting",
        source_role="reference",
    )

    # span_a is rank 1 in stream 1, rank 1 in stream 2
    # span_b is rank 2 in stream 1
    # span_c is rank 2 in stream 2
    stream1 = [span_a, span_b]
    stream2 = [span_a, span_c]

    fused = apply_reciprocal_rank_fusion(
        {"stream1": stream1, "stream2": stream2},
        weights={"stream1": 1.0, "stream2": 1.0},
        k=60,
    )

    assert len(fused) == 3
    # span_a was in both streams -> should be ranked #1
    assert fused[0].path == "doc1.tex"
    assert fused[0].score > fused[1].score


def test_mmr_diversity():
    # 3 spans: 2 nearly identical, 1 completely different
    s1 = SourceSpan(
        path="bib1",
        line_start=None,
        line_end=None,
        reason="r1",
        score=0.9,
        priority="supporting",
        source_role="reference",
        metadata={
            "snippet": "Transformer architecture for natural language processing and translation"
        },
    )
    s2 = SourceSpan(
        path="bib2",
        line_start=None,
        line_end=None,
        reason="r2",
        score=0.88,
        priority="supporting",
        source_role="reference",
        metadata={"snippet": "Transformer architecture for natural language processing models"},
    )
    s3 = SourceSpan(
        path="bib3",
        line_start=None,
        line_end=None,
        reason="r3",
        score=0.85,
        priority="supporting",
        source_role="reference",
        metadata={
            "snippet": "Hardware acceleration of RISC-V microcontrollers using FPGA synthesis"
        },
    )

    spans = [s1, s2, s3]
    diversified = apply_mmr_diversity(spans, lambda_param=0.6)

    # s1 is selected first (highest initial score).
    # s3 should be ranked ahead of s2 because s2 is almost identical to s1!
    assert diversified[0].path == "bib1"
    assert diversified[1].path == "bib3"
    assert diversified[2].path == "bib2"


def test_ast_environment_snapping(tmp_path):
    main_tex = tmp_path / "paper.tex"
    main_tex.write_text(
        r"""\documentclass{article}
\begin{document}
\section{Methods}
Line 4
Line 5
\begin{equation}\label{eq:loss}
L = \frac{1}{2} (y - \hat{y})^2
\end{equation}
Line 10
\end{document}
""",
        encoding="utf-8",
    )

    parser = VirtualDocumentParser(str(tmp_path))
    parser.parse("paper.tex")

    # If retrieval requests lines 7-8 (inside the equation lines 6-8)
    snapped_start, snapped_end = parser.snap_to_environment("paper.tex", 7, 8)
    assert snapped_start == 6
    assert snapped_end == 8
