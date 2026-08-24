import importlib.util
from typing import Any

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.local_models import LocalCrossEncoderReranker, SpanReranker
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.providers.bibtex import BibTeXProvider
from writing_context_rtfm.providers.local import ZoteroProvider
from writing_context_rtfm.providers.local_semantic import LocalSemanticSearchProvider
from writing_context_rtfm.providers.openai_semantic import OpenAISemanticSearchProvider


def get_active_providers(config: AppConfig) -> list[BaseContextProvider]:
    providers: list[BaseContextProvider] = []

    bibtex = BibTeXProvider(config)
    if bibtex.is_available(config):
        providers.append(bibtex)

    zotero = ZoteroProvider(config)
    if zotero.is_available(config):
        providers.append(zotero)

    openai = OpenAISemanticSearchProvider(config)
    if openai.is_available(config):
        providers.append(openai)

    local_semantic = LocalSemanticSearchProvider(config)
    if local_semantic.is_available(config):
        providers.append(local_semantic)

    return providers


def _provider_extra(config: AppConfig, provider_id: str) -> dict[str, Any]:
    provider = config.providers.get(provider_id)
    if provider is None:
        return {}
    if isinstance(provider, dict):
        return dict(provider)
    return dict(provider.extra or {})


def get_active_reranker(config: AppConfig) -> SpanReranker | None:
    provider = config.providers.get("local_reranker")
    enabled = bool(
        provider.get("enabled", False)
        if isinstance(provider, dict)
        else getattr(provider, "enabled", False)
    )
    if not enabled or importlib.util.find_spec("sentence_transformers") is None:
        return None
    extra = _provider_extra(config, "local_reranker")
    return LocalCrossEncoderReranker(
        str(extra.get("model", "Alibaba-NLP/gte-reranker-modernbert-base")),
        device=str(extra.get("device", "auto")),
        batch_size=int(extra.get("batch_size", 8)),
        max_length=int(extra.get("max_length", 512)),
        candidate_limit=int(extra.get("candidate_limit", 40)),
        blend_weight=float(extra.get("blend_weight", 0.25)),
        revision=str(extra["revision"]) if extra.get("revision") else None,
        torch_threads=int(extra.get("torch_threads", 4)),
    )
