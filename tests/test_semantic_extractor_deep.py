import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    GeneratorConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.semantic_extractor import (
    MissingAPIKeyError,
    extract_semantic_metadata,
    get_api_key,
    get_hf_api_token,
    prepare_section_text,
)
from writing_context_rtfm.storage import ExtensionStore


class TestSemanticExtractorDeep(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.db_path = str(self.root / "cache.sqlite")
        with ExtensionStore(self.db_path) as store:
            store.init_db()

        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=str(self.root)),
            context=ContextConfig(),
            cache=CacheConfig(path=self.db_path),
            section_cards=SectionCardsConfig(),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prepare_section_text_truncation(self):
        short_text = "Hello world"
        self.assertEqual(prepare_section_text(short_text), short_text)

        long_text = "A" * 10000
        truncated = prepare_section_text(long_text)
        self.assertIn("[TRUNCATED]", truncated)
        self.assertEqual(len(truncated), 4000 + len("\n\n... [TRUNCATED] ...\n\n") + 4000)

    def test_get_api_key_from_store_and_env(self):
        # 1. From store
        with ExtensionStore(self.db_path) as store:
            store.set_provider_token("openai_semantic", "store_token_123")

        with patch.dict(os.environ, {}, clear=True):
            token = get_api_key(self.config)
            self.assertEqual(token, "store_token_123")

        # 2. From env
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env_token_456"}):
            token = get_api_key(self.config)
            self.assertEqual(token, "env_token_456")

        # 3. From generator config
        cfg = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=str(self.root)),
            context=ContextConfig(),
            cache=CacheConfig(path=self.db_path),
            section_cards=SectionCardsConfig(),
            generator=GeneratorConfig(
                api_key="cfg_token_789", api_base="https://api.openai.com/v1"
            ),
        )
        self.assertEqual(get_api_key(cfg), "cfg_token_789")

    def test_get_hf_api_token(self):
        with patch.dict(os.environ, {"HF_TOKEN": "hf_token_abc"}):
            token = get_hf_api_token(self.config)
            self.assertEqual(token, "hf_token_abc")

    def test_extract_semantic_metadata_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = AppConfig(
                version=1,
                rtfm=RTFMConfig(project_root=str(self.root)),
                context=ContextConfig(),
                cache=CacheConfig(path=self.db_path),
                section_cards=SectionCardsConfig(),
                generator=GeneratorConfig(
                    api_key=None, api_base="https://custom.api.endpoint.com/v1"
                ),
            )
            with self.assertRaises(MissingAPIKeyError):
                extract_semantic_metadata("Section text", cfg)
