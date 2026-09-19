import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from writing_context_rtfm.config import apply_profile, load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.local_models import LocalCrossEncoderReranker
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.storage import ExtensionStore

tmpdir = tempfile.mkdtemp()
store = ExtensionStore(str(Path(tmpdir) / "cache.sqlite"))
store.init_db()


class MockAdapter(RTFMAdapter):
    def __init__(self) -> None:
        self.project_root = "."

    def search(self, query: str, corpus: str = "default", limit: int = 10) -> list[RTFMResult]:
        if "acknowledgment" in query.lower():
            return [
                RTFMResult(
                    path="acknowledgments.tex",
                    line_start=1,
                    line_end=15,
                    snippet="We thank our funding sponsors and colleagues for invaluable feedback.",
                    score=0.92,
                    metadata={"chunk_id": "ack_1"},
                ),
                RTFMResult(
                    path="chapter_1.tex",
                    line_start=1,
                    line_end=15,
                    snippet="Introductory text without funding details.",
                    score=0.35,
                    metadata={"chunk_id": "intro_1"},
                ),
                RTFMResult(
                    path="chapter_2.tex",
                    line_start=1,
                    line_end=15,
                    snippet="General methodology overview.",
                    score=0.20,
                    metadata={"chunk_id": "meth_1"},
                ),
            ]

        # 16 candidates: only spans 5 and 11 contain the exact mathematical proof for convergence
        results: list[RTFMResult] = []
        for i in range(16):
            is_gold = i in (5, 11)
            snippet = (
                f"Theorem {i}: Under Lipschitz continuity, the sequence converges asymptotically to the optimal manifold. Proved in Lemma 4."
                if is_gold
                else f"Section {i}: General literature survey discussing broad optimization settings and related benchmarks."
            )
            results.append(
                RTFMResult(
                    path=f"chapter_{i % 3 + 1}.tex",
                    line_start=i * 15 + 1,
                    line_end=i * 15 + 12,
                    snippet=snippet,
                    # BM25 baseline score ranks some noise higher because of common keywords
                    score=0.45 + (0.02 * (i % 4)) if not is_gold else 0.42,
                    metadata={"chunk_id": f"chk_{i}"},
                )
            )
        return results

    def context(self, path: str, line_start: int, line_end: int) -> str:
        return "Context snippet"

    def expand(self, result_id: str) -> str:
        return "Expanded snippet"

    def status(self) -> dict[str, Any]:
        return {"status": "ok"}


adapter = MockAdapter()
base_cfg = load_config("nonexistent.yaml")

# FAST PROFILE (BM25 only)
cfg_fast = apply_profile(base_cfg, "fast")
gen_fast = ContextPackGenerator(cfg_fast, None, adapter, store)

t0 = time.perf_counter()
pack_fast = gen_fast.generate(
    task="Extract asymptotic convergence proof for the optimization manifold",
    target=None,
    token_budget=1200,
    mode="write",
)
t_fast = (time.perf_counter() - t0) * 1000

# THOROUGH PROFILE (BM25 + Cross-Encoder Reranker)
cfg_thorough = apply_profile(base_cfg, "thorough")


class SemanticCrossEncoder:
    def __init__(self) -> None:
        self.call_count = 0

    def predict(self, pairs: Sequence[tuple[str, str]], **kwargs: Any) -> list[float]:
        self.call_count += 1
        scores: list[float] = []
        for _query, snippet in pairs:
            s_low = snippet.lower()
            score = 0.15
            if "lipschitz" in s_low or "manifold" in s_low:
                score += 0.50
            if "converges asymptotically" in s_low:
                score += 0.30
            scores.append(min(0.99, score))
        return scores


model_mock = SemanticCrossEncoder()
reranker = LocalCrossEncoderReranker(
    "Alibaba-NLP/gte-reranker-modernbert-base", model=model_mock, blend_weight=0.75, store=store
)
gen_thorough = ContextPackGenerator(cfg_thorough, None, adapter, store, reranker=reranker)

# 1st run: Cold reranking (neural predict invoked)
t0 = time.perf_counter()
pack_thorough_cold = gen_thorough.generate(
    task="Extract asymptotic convergence proof for the optimization manifold",
    target=None,
    token_budget=1200,
    mode="write",
)
t_thorough_cold = (time.perf_counter() - t0) * 1000
predict_calls_after_cold = model_mock.call_count

# 2nd run: Warm reranking (semantic invariance cache hit in SQLite)
t0 = time.perf_counter()
pack_thorough_warm = gen_thorough.generate(
    task="Extract asymptotic convergence proof for the optimization manifold",
    target=None,
    token_budget=1200,
    mode="write",
)
t_thorough_warm = (time.perf_counter() - t0) * 1000
predict_calls_after_warm = model_mock.call_count

# AUTO PROFILE (Dynamic escalation)
cfg_auto = apply_profile(base_cfg, "auto")
gen_auto = ContextPackGenerator(cfg_auto, None, adapter, store, reranker=reranker)

# Auto case 1: General task without mathematical cues -> stays on BM25
t0 = time.perf_counter()
pack_auto_simple = gen_auto.generate(
    task="General acknowledgment section and contributor overview",
    target=None,
    token_budget=1200,
    mode="write",
)
t_auto_simple = (time.perf_counter() - t0) * 1000

# Auto case 2: Mathematical task -> automatically escalates to Cross-Encoder
t0 = time.perf_counter()
pack_auto_math = gen_auto.generate(
    task="Extract asymptotic convergence proof for the optimization manifold",
    target=None,
    token_budget=1200,
    mode="write",
)
t_auto_math = (time.perf_counter() - t0) * 1000

p3_fast = (
    sum(
        1
        for s in pack_fast.source_spans[:3]
        if "lipschitz" in (s.metadata or {}).get("snippet", "").lower()
    )
    / 3.0
)
p3_thorough = (
    sum(
        1
        for s in pack_thorough_cold.source_spans[:3]
        if "lipschitz" in (s.metadata or {}).get("snippet", "").lower()
    )
    / 3.0
)
p3_auto = (
    sum(
        1
        for s in pack_auto_math.source_spans[:3]
        if "lipschitz" in (s.metadata or {}).get("snippet", "").lower()
    )
    / 3.0
)

print("========================================================================")
print("AUDITORIA SPRINT 5: AUTO PROFILE & CACHE DE INVARIÂNCIA SEMÂNTICA")
print("========================================================================")
print(f"1. Fast Profile (BM25 Puro):                       {t_fast:.2f} ms")
print(f"2. Thorough Profile - Cold (com Reranker Neural):  {t_thorough_cold:.2f} ms")
print(f"3. Thorough Profile - Warm (Cache Invariante SQLite):{t_thorough_warm:.2f} ms (Predict calls delta: {predict_calls_after_warm - predict_calls_after_cold})")
print(f"4. Auto Profile - Tarefa Simples (Mantém BM25):    {t_auto_simple:.2f} ms (Escalated={pack_auto_simple.quality.get('auto_escalation', {}).get('escalated')})")
print(f"5. Auto Profile - Tarefa Matemática (Auto-Escala): {t_auto_math:.2f} ms (Escalated={pack_auto_math.quality.get('auto_escalation', {}).get('escalated')})")
print("------------------------------------------------------------------------")
print("PRECISÃO@3 (Captura das Provas Matemáticas dos Teoremas 5 e 11):")
print(f"  Fast (BM25 Puro):             {p3_fast * 100:.1f}%")
print(f"  Thorough (Cross-Encoder):     {p3_thorough * 100:.1f}% (+{(p3_thorough - p3_fast) * 100:+.1f}%)")
print(f"  Auto (Escalação Dinâmica):   {p3_auto * 100:.1f}% (+{(p3_auto - p3_fast) * 100:+.1f}%)")
print("========================================================================")
