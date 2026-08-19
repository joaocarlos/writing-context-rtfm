from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.providers.bibtex import BibTeXProvider
from writing_context_rtfm.providers.local import ZoteroProvider
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

    return providers
