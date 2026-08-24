import os
import unittest
from unittest.mock import MagicMock, patch

import yaml

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import RTFMResult, SourceSpan
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore


class FakeLiteratureProvider(BaseContextProvider):
    @property
    def provider_id(self) -> str:
        return "fake_lit"

    def is_available(self, config: AppConfig) -> bool:
        return True

    def fetch_context(
        self,
        queries: list[str],
        target: str | None,
        limit: int,
        query_type_map: dict[str, str] | None = None,
        task_type: str | None = None,
    ) -> list[SourceSpan]:
        # Return supplementary references for target
        if target == "section_results":
            return [
                SourceSpan(
                    path="references.bib",
                    line_start=1,
                    line_end=15,
                    reason="Benchmark baseline citation for acoustic fault detection",
                    score=0.88,
                    priority="supporting",
                    source_role="reference",
                    metadata={"snippet": "@article{warden2020tinyml, ...}"},
                )
            ]
        return []


def dynamic_rtfm_search_mock(query: str, limit: int = 10, **kwargs):
    q_lower = query.lower()
    results = []

    # Approach related chunks
    if any(k in q_lower for k in ("approach", "methodology", "dataset", "cnn", "quantization", "mimii")):
        results.append(
            RTFMResult(
                path="sections/03_approach.tex",
                line_start=1,
                line_end=20,
                score=0.92,
                snippet="The MIMII dataset was partitioned into training (70%) and testing (30%) sets.",
                metadata={"file_path": "sections/03_approach.tex"},
            )
        )

    # Intro / background chunks
    if any(k in q_lower for k in ("intro", "tinyml", "acoustic", "water pump", "motivation", "background")):
        results.append(
            RTFMResult(
                path="sections/01_intro.tex",
                line_start=1,
                line_end=25,
                score=0.86,
                snippet="TinyML enables fault detection and monitoring directly on water pumps using acoustic analysis.",
                metadata={"file_path": "sections/01_intro.tex"},
            )
        )

    # Results chunks
    if any(k in q_lower for k in ("results", "accuracy", "latency", "f1", "memory", "footprint")):
        results.append(
            RTFMResult(
                path="sections/04_results.tex",
                line_start=1,
                line_end=20,
                score=0.94,
                snippet="The quantized CNN achieved 96.4% F1-score with 12ms inference latency.",
                metadata={"file_path": "sections/04_results.tex"},
            )
        )

    # Related work chunks
    if any(k in q_lower for k in ("related", "survey", "prior", "vibration", "edge")):
        results.append(
            RTFMResult(
                path="sections/02_related.tex",
                line_start=1,
                line_end=20,
                score=0.89,
                snippet="Prior edge ML surveys focus on vibration signals rather than acoustic analysis.",
                metadata={"file_path": "sections/02_related.tex"},
            )
        )

    # Distractor chunk with low relevance score
    results.append(
        RTFMResult(
            path="notes/unrelated_scratchpad.txt",
            line_start=1,
            line_end=10,
            score=0.08,
            snippet="Random draft notes about coffee breaks and server setup.",
            metadata={"file_path": "notes/unrelated_scratchpad.txt"},
        )
    )

    return results


class TestContextPackRetrieval(unittest.TestCase):
    def setUp(self):
        self.project_root = "tests/fixtures/mini_latex_project"
        self.expected_yaml = "tests/fixtures/expected_sources.yaml"
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=self.project_root, corpus="test_corpus"),
            context=ContextConfig(default_token_budget=2000, min_score=0.15),
            cache=CacheConfig(enabled=False, path="test_cache.sqlite"),
            section_cards=SectionCardsConfig(
                path=os.path.join(self.project_root, ".writing-context", "section_cards.yaml")
            ),
        )
        self.cards = load_section_cards(self.config.section_cards.path)

    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.sync")
    def test_retrieval_metrics(self, mock_sync, mock_search):
        mock_sync.return_value = True
        mock_search.side_effect = dynamic_rtfm_search_mock

        adapter = RTFMAdapter()
        adapter.sync(self.project_root, "test_corpus")

        store = MagicMock(spec=ExtensionStore)
        store.get_cached_pack.return_value = None

        fake_provider = FakeLiteratureProvider()
        generator = ContextPackGenerator(
            self.config, self.cards, adapter, store, providers=[fake_provider]
        )

        with open(self.expected_yaml) as f:
            tasks = yaml.safe_load(f)["tasks"]

        for task_def in tasks:
            pack = generator.generate(
                task=task_def["task"], target=task_def["target_section"], token_budget=2000
            )

            retrieved_paths = {span.path for span in pack.source_spans}
            expected_paths = set(task_def["expected_sources"])

            # True Positives
            tp = len(retrieved_paths.intersection(expected_paths))
            # False Negatives
            fn = len(expected_paths - retrieved_paths)
            # Distractors that should have been filtered out by min_score
            distractors = {"notes/unrelated_scratchpad.txt"}.intersection(retrieved_paths)

            recall = tp / (tp + fn) if (tp + fn) > 0 else 0

            self.assertGreaterEqual(
                recall,
                0.80,
                f"Recall below 80% for task {task_def['id']}: retrieved {retrieved_paths}, expected {expected_paths}",
            )
            self.assertEqual(
                len(distractors),
                0,
                f"Distractor note found in retrieved spans for task {task_def['id']}",
            )
            self.assertLessEqual(pack.estimated_tokens, 2500, "Pack exceeds token budget")


if __name__ == "__main__":
    unittest.main()
