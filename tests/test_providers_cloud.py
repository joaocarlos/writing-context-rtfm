import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from mcp.types import CallToolResult, TextContent, Tool, ListToolsResult

from writing_context_rtfm.config import AppConfig, ContextConfig, RTFMConfig, CacheConfig, SectionCardsConfig
from writing_context_rtfm.schemas import ProviderConfig, SourceSpan
from writing_context_rtfm.providers.cloud import SciteProvider, ConsensusProvider

class TestProvidersCloud(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(),
            context=ContextConfig(),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig(),
            providers={
                "scite": ProviderConfig(
                    enabled=True,
                    sse_url="https://api.scite.ai/mcp",
                    headers={"Authorization": "Bearer scite_test_token"}
                ),
                "consensus": ProviderConfig(
                    enabled=True,
                    sse_url="https://mcp.consensus.app/mcp",
                    headers={"X-API-Key": "consensus_key"}
                )
            }
        )

    @patch("writing_context_rtfm.providers.cloud.sse_client")
    @patch("writing_context_rtfm.providers.cloud.ClientSession")
    def test_consensus_provider_success(self, mock_client_session_cls, mock_sse_client):
        # 1. Setup SSE Client Mock
        mock_sse_ctx = MagicMock()
        mock_sse_client.return_value = mock_sse_ctx
        mock_sse_ctx.__aenter__.return_value = (MagicMock(), MagicMock())

        # 2. Setup ClientSession Mock
        mock_session = AsyncMock()
        mock_client_session_ctx = MagicMock()
        mock_client_session_cls.return_value = mock_client_session_ctx
        mock_client_session_ctx.__aenter__.return_value = mock_session

        # Mock list_tools response
        mock_session.list_tools.return_value = ListToolsResult(
            tools=[
                Tool(
                    name="search_consensus",
                    description="Search tool",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer"}
                        }
                    }
                )
            ]
        )

        # Mock call_tool response with JSON results
        mock_papers = [
            {
                "title": "Title A",
                "abstract": "Abstract text A",
                "doi": "10.1000/doi.a",
                "score": 0.95
            },
            {
                "title": "Title B",
                "abstract": "Abstract text B",
                "url": "https://example.com/paper_b",
                "score": 0.8
            }
        ]
        mock_session.call_tool.return_value = CallToolResult(
            content=[TextContent(type="text", text=json.dumps(mock_papers))]
        )

        # Initialize provider and fetch context
        provider = ConsensusProvider(self.config)
        self.assertTrue(provider.is_available(self.config))
        
        spans = provider.fetch_context(queries=["machine learning"], target=None, limit=5)
        
        # Assertions
        mock_sse_client.assert_called_once_with("https://mcp.consensus.app/mcp", headers={"X-API-Key": "consensus_key"})
        mock_session.initialize.assert_called_once()
        mock_session.list_tools.assert_called_once()
        
        # Verify call_tool arguments mapping (query -> query, limit -> limit)
        mock_session.call_tool.assert_called_once_with("search_consensus", {"query": "machine learning", "limit": 5})
        
        # Verify returned spans
        self.assertEqual(len(spans), 2)
        
        # First span
        self.assertEqual(spans[0].path, "doi:10.1000/doi.a")
        self.assertEqual(spans[0].score, 0.95)
        self.assertEqual(spans[0].metadata["title"], "Title A")
        self.assertEqual(spans[0].metadata["snippet"], "Abstract text A")
        
        # Second span
        self.assertEqual(spans[1].path, "https://example.com/paper_b")
        self.assertEqual(spans[1].score, 0.8)
        self.assertEqual(spans[1].metadata["title"], "Title B")

    @patch("writing_context_rtfm.providers.cloud.sse_client")
    @patch("writing_context_rtfm.providers.cloud.ClientSession")
    def test_scite_provider_plain_text_fallback(self, mock_client_session_cls, mock_sse_client):
        mock_sse_ctx = MagicMock()
        mock_sse_client.return_value = mock_sse_ctx
        mock_sse_ctx.__aenter__.return_value = (MagicMock(), MagicMock())

        mock_session = AsyncMock()
        mock_client_session_ctx = MagicMock()
        mock_client_session_cls.return_value = mock_client_session_ctx
        mock_client_session_ctx.__aenter__.return_value = mock_session

        mock_session.list_tools.return_value = ListToolsResult(
            tools=[
                Tool(
                    name="scite_search",
                    description="Scite search tool",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "q": {"type": "string"},
                            "count": {"type": "integer"}
                        }
                    }
                )
            ]
        )

        mock_session.call_tool.return_value = CallToolResult(
            content=[TextContent(type="text", text="Raw citation contexts and reference text list.")]
        )

        provider = SciteProvider(self.config)
        spans = provider.fetch_context(queries=["quantum physics"], target=None, limit=10)

        # Verify arguments mapping (query -> q, limit -> count)
        mock_session.call_tool.assert_called_once_with("scite_search", {"q": "quantum physics", "count": 10})
        mock_sse_client.assert_called_once_with("https://api.scite.ai/mcp", headers={"Authorization": "Bearer scite_test_token"})

        # Verify fallback span
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].path, "scite:quantum_physics")
        self.assertEqual(spans[0].metadata["snippet"], "Raw citation contexts and reference text list.")
        self.assertEqual(spans[0].score, 0.7)

    @patch("writing_context_rtfm.providers.cloud.sse_client")
    @patch("writing_context_rtfm.providers.cloud.ClientSession")
    def test_provider_disabled_returns_empty(self, mock_client_session_cls, mock_sse_client):
        self.config.providers["consensus"] = ProviderConfig(enabled=False, sse_url="https://mcp.consensus.app/mcp")
        provider = ConsensusProvider(self.config)
        self.assertFalse(provider.is_available(self.config))
        
        spans = provider.fetch_context(queries=["anything"], target=None, limit=5)
        self.assertEqual(len(spans), 0)
        mock_sse_client.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_get_valid_oauth_token_refreshes_when_expired(self, mock_urlopen):
        import time
        from writing_context_rtfm.storage import ExtensionStore
        from writing_context_rtfm.providers.cloud import get_valid_oauth_token
        
        store = ExtensionStore(self.config.cache.path)
        store.init_db()
        
        # 1. Insert expired token
        store.set_provider_oauth(
            "scite",
            client_id="client_abc",
            access_token="old_access",
            refresh_token="refresh_123",
            expires_at=time.time() - 100
        )
        
        # 2. Mock urlopen response for refreshing
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600
        }).encode("utf-8")
        mock_urlopen.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        # 3. Call get_valid_oauth_token
        token = get_valid_oauth_token(self.config, "scite")
        
        # Assertions
        self.assertEqual(token, "new_access_token")
        
        # Verify db updated
        oauth = store.get_provider_oauth("scite")
        self.assertEqual(oauth["access_token"], "new_access_token")
        self.assertEqual(oauth["refresh_token"], "new_refresh_token")
        self.assertTrue(oauth["expires_at"] > time.time() + 3500)

if __name__ == '__main__':
    unittest.main()
