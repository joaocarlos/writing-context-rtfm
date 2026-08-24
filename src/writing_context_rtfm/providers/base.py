from abc import ABC, abstractmethod

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.schemas import SourceSpan


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
    def fetch_context(
        self,
        queries: list[str],
        target: str | None,
        limit: int,
        query_type_map: dict[str, str] | None = None,
        task_type: str | None = None,
    ) -> list[SourceSpan]:
        """Query the provider source and return a list of normalized SourceSpan objects."""
        pass

    def get_fingerprint(self, config: AppConfig) -> str | None:
        """Return a cache invalidation fingerprint for this provider's data source, if applicable."""
        return None
