# Providers package
from typing import List
from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.providers.local import ZoteroProvider
from writing_context_rtfm.providers.openai_semantic import OpenAISemanticSearchProvider

def get_active_providers(config: AppConfig) -> List[BaseContextProvider]:
    providers = []
    
    zotero = ZoteroProvider(config)
    if zotero.is_available(config):
        providers.append(zotero)
        
    openai = OpenAISemanticSearchProvider(config)
    if openai.is_available(config):
        providers.append(openai)
        
    return providers
