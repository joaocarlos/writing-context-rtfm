"""Local semantic intent classifier using sentence embeddings.

Classifies task intent into:
- 'compress': condensing text, shortening prose, reducing word count to fit limits.
- 'adapt': cross-genre adaptation (thesis to paper, paper to dissertation).
- 'delete': structural removal of obsolete sections/tables (maps to rewrite).
- 'rewrite': polishing, revising, editing existing manuscript prose.
- 'write': drafting new sections from scratch.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger("mcp-server")

DEFAULT_INTENT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

INTENT_PROTOTYPES: dict[str, list[str]] = {
    "compress": [
        "condense text and shorten length",
        "comprimir texto e enxugar parágrafos",
        "reduce word count to fit page limit",
        "poda de texto para reduzir extensão",
        "shorten text length and cut words",
        "reduzir tamanho do texto mantendo estrutura",
        "eliminar pleonasmos e preenchimento para encurtar",
        "cut down text length and word count",
    ],
    "adapt": [
        "adapt thesis chapter into a conference paper",
        "adaptar tese para artigo científico",
        "convert paper section into dissertation chapter",
        "reframe narrative and tone for different audience",
        "adaptar conteúdo de dissertação para periódico",
        "transform academic chapter into journal article",
    ],
    "rewrite": [
        "revise prose and use active voice",
        "revise and polish existing section prose",
        "revisar e reescrever trechos da seção existente",
        "improve academic clarity and style",
        "reescrever parágrafos existentes mantendo estrutura",
        "refine arguments and narrative flow",
        "ajustar redação e fluxo de texto existente",
        "editar texto e executar plano de edição",
        "executar alterações e refatoração no capítulo",
        "edição e revisão geral da seção",
        "edit section content and rewrite active voice",
    ],
    "delete": [
        "remove obsolete section and tables",
        "remover integralmente seção antiga e tabela comparativa",
        "delete subsection and drop content",
        "excluir parágrafos e eliminar trechos obsoletos",
        "remover trechos antigos preservando cópia",
    ],
    "write": [
        "draft a brand new section from scratch",
        "escrever uma nova seção do zero",
        "create missing section and outline future work",
        "redigir conteúdo novo a partir de diretrizes",
    ],
}


@dataclass(frozen=True)
class IntentClassificationResult:
    intent: str
    score: float
    margin: float
    scores: dict[str, float]
    is_confident: bool


class LocalIntentClassifier:
    """Classifies task intent using cosine similarity against prototype embeddings."""

    def __init__(
        self,
        model_id: str = DEFAULT_INTENT_MODEL,
        *,
        model: Any | None = None,
        confidence_threshold: float = 0.55,
        margin_threshold: float = 0.05,
        local_files_only: bool = True,
    ):
        self.model_id = model_id
        self._model = model
        self.confidence_threshold = confidence_threshold
        self.margin_threshold = margin_threshold
        self.local_files_only = local_files_only
        self._load_failed = False
        self._prototype_embeddings: dict[str, Any] | None = None
        self._cache: dict[str, IntentClassificationResult] = {}

    def _get_model(self) -> Any | None:
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None
        try:
            st = importlib.import_module("sentence_transformers")
            try:
                self._model = st.SentenceTransformer(self.model_id, local_files_only=True)
            except Exception:
                if self.local_files_only:
                    self._load_failed = True
                    return None
                self._model = st.SentenceTransformer(self.model_id)
            return self._model
        except Exception as e:
            self._load_failed = True
            logger.debug("Local embedding model '%s' unavailable: %s", self.model_id, e)
            return None

    def _ensure_prototypes(self, model: Any) -> dict[str, Any]:
        if self._prototype_embeddings is not None:
            return self._prototype_embeddings

        proto_dict: dict[str, Any] = {}
        for intent, texts in INTENT_PROTOTYPES.items():
            encoded = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            proto_dict[intent] = np.asarray(encoded, dtype=np.float32)

        self._prototype_embeddings = proto_dict
        return self._prototype_embeddings

    def classify(self, task: str) -> IntentClassificationResult | None:
        """Classify task into an intent using local semantic embeddings."""
        task_clean = task.strip()
        if not task_clean:
            return None

        if task_clean in self._cache:
            return self._cache[task_clean]

        model = self._get_model()
        if model is None:
            return None

        try:
            prototypes = self._ensure_prototypes(model)
            task_vec = model.encode(
                [task_clean],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            vec = np.asarray(task_vec[0], dtype=np.float32)

            scores: dict[str, float] = {}
            for intent, matrix in prototypes.items():
                sims = np.dot(matrix, vec)
                scores[intent] = float(np.max(sims))

            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            best_intent, best_score = sorted_scores[0]
            second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0.0
            margin = best_score - second_score

            # Asymmetric thresholding: compress is a destructive pruning action and requires
            # high confidence, whereas delete/rewrite are conservative and prioritize structure.
            if best_intent == "compress":
                thresh = 0.65
                min_margin = 0.08
            elif best_intent == "adapt":
                thresh = 0.60
                min_margin = 0.08
            elif best_intent in ("delete", "rewrite"):
                thresh = 0.45
                min_margin = 0.03
            else:
                thresh = self.confidence_threshold
                min_margin = self.margin_threshold

            is_confident = (best_score >= thresh) and (margin >= min_margin)

            res = IntentClassificationResult(
                intent=best_intent,
                score=best_score,
                margin=margin,
                scores=scores,
                is_confident=is_confident,
            )
            self._cache[task_clean] = res
            return res
        except Exception as e:
            logger.debug("Failed semantic intent classification: %s", e)
            return None


# Module-level singleton for fast re-use across MCP tool calls
_GLOBAL_INTENT_CLASSIFIER: LocalIntentClassifier | None = None


def get_local_intent_classifier() -> LocalIntentClassifier:
    """Retrieve or create the module-level intent classifier singleton."""
    global _GLOBAL_INTENT_CLASSIFIER
    if _GLOBAL_INTENT_CLASSIFIER is None:
        _GLOBAL_INTENT_CLASSIFIER = LocalIntentClassifier()
    return _GLOBAL_INTENT_CLASSIFIER


def classify_task_intent(task: str) -> IntentClassificationResult | None:
    """Classify a task's intent using local sentence embeddings."""
    classifier = get_local_intent_classifier()
    return classifier.classify(task)
