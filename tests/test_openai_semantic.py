import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from writing_context_rtfm.providers.openai_semantic import (
    OpenAISemanticSearchProvider,
    get_openai_embeddings,
)


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.providers = {
        "openai_semantic": {
            "enabled": True,
            "model": "test-model-3",
        }
    }
    config.cache = MagicMock()
    config.cache.path = "test_cache.sqlite"
    config.rtfm = MagicMock()
    config.rtfm.project_root = "/test/root"
    return config


def test_get_openai_embeddings_success():
    """Test successful API call returns sorted embeddings."""
    with patch("httpx.post") as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ]
        }
        mock_post.return_value = mock_response

        embeddings = get_openai_embeddings(["text1", "text2"], "test-model-3", "test_key")

        assert len(embeddings) == 2
        # Should be sorted by index
        assert embeddings[0] == [0.1, 0.2]
        assert embeddings[1] == [0.3, 0.4]

        # Verify POST request args
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer test_key"
        assert kwargs["json"]["input"] == ["text1", "text2"]


def test_get_openai_embeddings_no_key():
    with pytest.raises(ValueError, match="OpenAI API key not provided."):
        get_openai_embeddings(["text"], "model", "")


def test_is_available(mock_config):
    provider = OpenAISemanticSearchProvider(mock_config)

    # Disabled in config
    mock_config.providers["openai_semantic"]["enabled"] = False
    assert not provider.is_available(mock_config)
    mock_config.providers["openai_semantic"]["enabled"] = True

    with patch.dict(os.environ, {"OPENAI_API_KEY": "env_key"}):
        assert provider.is_available(mock_config)

    with patch.dict(os.environ, clear=True):
        with patch("writing_context_rtfm.providers.openai_semantic.ExtensionStore") as MockStore:
            store = MockStore.return_value
            store.__enter__.return_value = store
            store.get_provider_token.return_value = None
            assert not provider.is_available(mock_config)

            store.get_provider_token.return_value = "db_key"
            assert provider.is_available(mock_config)


@patch("writing_context_rtfm.providers.openai_semantic.get_openai_embeddings")
@patch("writing_context_rtfm.providers.openai_semantic.ExtensionStore")
def test_sync_chunks(mock_store_class, mock_get_embeddings, mock_config):
    provider = OpenAISemanticSearchProvider(mock_config)

    store = mock_store_class.return_value
    store.__enter__.return_value = store
    store.get_missing_openai_chunks.return_value = [
        {"chunk_id": "c1", "content": "text 1"},
        {"chunk_id": "c2", "content": "text 2"},
    ]

    # Mock api key check
    with patch.object(provider, "_get_api_key", return_value="test_key"):
        mock_get_embeddings.return_value = [[0.1, 0.1], [0.2, 0.2]]

        provider.sync_chunks(store, "dummy_db")

        mock_get_embeddings.assert_called_once_with(
            ["text 1", "text 2"], "test-model-3", "test_key"
        )
        assert store.store_openai_embeddings.called
        store_args = store.store_openai_embeddings.call_args[0][0]
        assert len(store_args) == 2
        assert store_args[0]["chunk_id"] == "c1"


@patch("writing_context_rtfm.providers.openai_semantic.sqlite3.connect")
@patch("writing_context_rtfm.providers.openai_semantic.ExtensionStore")
@patch("writing_context_rtfm.providers.openai_semantic.get_openai_embeddings")
@patch("writing_context_rtfm.utils.resolve_rtfm_db_path")
def test_fetch_context(
    mock_resolve, mock_get_embeddings, mock_store_class, mock_connect, mock_config
):
    provider = OpenAISemanticSearchProvider(mock_config)
    mock_resolve.return_value = "dummy.db"

    store = mock_store_class.return_value
    store.__enter__.return_value = store

    # Fake existing embeddings in DB
    # We create two fake chunks, c1 and c2.
    # Query is [1.0, 0.0]
    # c1 is [1.0, 0.0] -> dot product 1.0
    # c2 is [0.0, 1.0] -> dot product 0.0
    store.get_all_openai_embeddings.return_value = [
        {"chunk_id": "c1", "embedding": np.array([1.0, 0.0], dtype=np.float32).tobytes()},
        {"chunk_id": "c2", "embedding": np.array([0.0, 1.0], dtype=np.float32).tobytes()},
    ]

    with patch.object(provider, "_get_api_key", return_value="test_key"):
        mock_get_embeddings.return_value = [[1.0, 0.0]]

        # Mock SQLite fetchone
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            {"content": "Content C1", "file_path": "a.tex", "line_start": 1, "line_end": 5},
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        spans = provider.fetch_context(["query text"], None, 1)

        assert len(spans) == 1
        assert spans[0].path == "a.tex"
        assert spans[0].score > 0.99
        assert spans[0].metadata["snippet"] == "Content C1"

        # Verify DB fetch was for c1
        execute_args = mock_cursor.execute.call_args[0]
        assert "WHERE c.chunk_id = ?" in execute_args[0]
        assert execute_args[1] == ("c1",)
