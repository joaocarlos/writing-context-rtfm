import unittest
from unittest.mock import MagicMock
from writing_context_rtfm.config import AppConfig, ContextConfig, RTFMConfig, CacheConfig, SectionCardsConfig
from writing_context_rtfm.schemas import SourceSpan, ProviderConfig
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.storage import ExtensionStore

class MockContextProvider(BaseContextProvider):
    def __init__(self, provider_id: str, enabled: bool = True, raise_error: bool = False, spans = None):
        self._provider_id = provider_id
        self._enabled = enabled
        self._raise_error = raise_error
        self.spans = spans or []
        self.fetch_context_called = False

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def is_available(self, config: AppConfig) -> bool:
        provider_cfg = config.providers.get(self._provider_id)
        return provider_cfg is not None and provider_cfg.enabled and self._enabled

    def fetch_context(self, queries, target, limit):
        self.fetch_context_called = True
        if self._raise_error:
            raise RuntimeError("Mock provider failure")
        return self.spans

class TestProvidersBase(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(),
            context=ContextConfig(default_token_budget=10000),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig(),
            providers={
                "mock_prov": ProviderConfig(enabled=True, sse_url="http://mock"),
                "disabled_prov": ProviderConfig(enabled=False, sse_url="http://mock"),
                "failing_prov": ProviderConfig(enabled=True, sse_url="http://mock")
            }
        )
        self.adapter = MagicMock()
        self.adapter.search.return_value = []
        self.store = MagicMock(spec=ExtensionStore)
        self.store.get_cached_pack.return_value = None

    def test_provider_called_when_enabled(self):
        span = SourceSpan(
            path="external/paper.pdf",
            line_start=1,
            line_end=10,
            reason="External reference",
            score=0.95,
            priority="essential",
            source_role="reference",
            metadata={"snippet": "Some external paper data"}
        )
        mock_prov = MockContextProvider("mock_prov", spans=[span])
        disabled_prov = MockContextProvider("disabled_prov", spans=[])
        
        generator = ContextPackGenerator(self.config, None, self.adapter, self.store, providers=[mock_prov, disabled_prov])
        pack = generator.generate(task="write outline", target=None, token_budget=10000)
        
        self.assertTrue(mock_prov.fetch_context_called)
        self.assertFalse(disabled_prov.fetch_context_called)
        
        # Verify the span is returned in the pack
        self.assertEqual(len(pack.source_spans), 1)
        self.assertEqual(pack.source_spans[0].path, "external/paper.pdf")
        self.assertEqual(pack.source_spans[0].score, 0.95)
        self.assertEqual(pack.status, "degraded") # Degraded because no section cards are loaded

    def test_provider_error_resilience(self):
        failing_prov = MockContextProvider("failing_prov", raise_error=True)
        span = SourceSpan(
            path="external/paper.pdf",
            line_start=1,
            line_end=10,
            reason="External reference",
            score=0.95,
            priority="essential",
            source_role="reference",
            metadata={"snippet": "Some external paper data"}
        )
        mock_prov = MockContextProvider("mock_prov", spans=[span])
        
        generator = ContextPackGenerator(self.config, None, self.adapter, self.store, providers=[failing_prov, mock_prov])
        pack = generator.generate(task="write outline", target=None, token_budget=10000)
        
        # Generator should continue, status should be degraded, and have warning
        self.assertEqual(pack.status, "degraded")
        self.assertTrue(any("Provider 'failing_prov' failed:" in w for w in pack.warnings))
        
        # The successful provider span should still be there
        self.assertEqual(len(pack.source_spans), 1)
        self.assertEqual(pack.source_spans[0].path, "external/paper.pdf")

    def test_provider_spans_merged_ranked_deduplicated_budgeted(self):
        # Let's create multiple providers returning overlapping/competing spans
        span_external = SourceSpan(
            path="external/paper.pdf",
            line_start=1,
            line_end=10,
            reason="External reference",
            score=0.95,
            priority="essential",
            source_role="reference",
            metadata={"snippet": "word " * 1200}  # ~1200 tokens
        )
        span_local = SourceSpan(
            path="external/paper.pdf",  # Same path and lines -> duplicate!
            line_start=1,
            line_end=10,
            reason="Duplicate from other provider",
            score=0.85,
            priority="supporting",
            source_role="reference",
            metadata={"snippet": "word " * 1200}
        )
        span_low_score = SourceSpan(
            path="external/low.pdf",
            line_start=1,
            line_end=10,
            reason="Low relevance",
            score=0.0001,  # Should be filtered out by score filtering
            priority="background",
            source_role="reference",
            metadata={"snippet": "low score data"}
        )
        
        mock_prov1 = MockContextProvider("mock_prov", spans=[span_external])
        mock_prov2 = MockContextProvider("failing_prov", spans=[span_local, span_low_score])
        
        generator = ContextPackGenerator(self.config, None, self.adapter, self.store, providers=[mock_prov1, mock_prov2])
        
        # Deduplication (duplicate external/paper.pdf should be merged, keeping the higher score 0.95)
        # Also, low score should be filtered out by config.context.min_score (default 0.01)
        pack = generator.generate(task="write outline", target=None, token_budget=10000)
        self.assertEqual(len(pack.source_spans), 1)
        self.assertEqual(pack.source_spans[0].path, "external/paper.pdf")
        self.assertEqual(pack.source_spans[0].score, 0.95)
        
        # Token budgeting (if budget is very low, e.g. 500, the 1200-token span should be dropped)
        pack_small_budget = generator.generate(task="write outline", target=None, token_budget=500)
        self.assertEqual(len(pack_small_budget.source_spans), 0)
        self.assertEqual(pack_small_budget.status, "degraded")

if __name__ == '__main__':
    unittest.main()
