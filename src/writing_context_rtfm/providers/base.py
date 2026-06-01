from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.config import AppConfig

class BaseContextProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Return the unique string identifier for this provider (e.g., 'zotero')."""
        pass

    @abstractmethod
    def is_available(self, config: AppConfig) -> bool:
        """Return True if the provider is enabled in config and its dependencies are met."""
        pass

    @abstractmethod
    def fetch_context(self, queries: List[str], target: Optional[str], limit: int, query_type_map: Optional[Dict[str, str]] = None, task_type: Optional[str] = None) -> List[SourceSpan]:
        """Query the provider source and return a list of normalized SourceSpan objects."""
        pass
