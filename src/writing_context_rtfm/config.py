"""Configuration schema and loading."""
import os
from pathlib import Path
import yaml
from dataclasses import dataclass

@dataclass(frozen=True)
class RTFMConfig:
    corpus: str = "manuscript"
    project_root: str = "."
    sync_before_pack: bool = False

@dataclass(frozen=True)
class ContextConfig:
    default_token_budget: int = 6000
    reserved_generation_margin: float = 0.10
    max_search_results_per_query: int = 10
    max_source_spans: int = 20
    include_source_excerpts: bool = False
    min_score: float = 0.01
    min_relative_score: float = 0.05

@dataclass(frozen=True)
class CacheConfig:
    enabled: bool = True
    path: str = ".writing-context/context_cache.sqlite"
    invalidate_on_refresh: bool = True

@dataclass(frozen=True)
class SectionCardsConfig:
    path: str = ".writing-context/section_cards.yaml"
    required: bool = False

@dataclass(frozen=True)
class AppConfig:
    version: int
    rtfm: RTFMConfig
    context: ContextConfig
    cache: CacheConfig
    section_cards: SectionCardsConfig

def load_config(project_root: str = ".") -> AppConfig:
    root = Path(project_root).resolve()
    config_path = root / ".writing-context" / "config.yaml"

    defaults = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(root)),
        context=ContextConfig(),
        cache=CacheConfig(path=str(root / ".writing-context" / "context_cache.sqlite")),
        section_cards=SectionCardsConfig(path=str(root / ".writing-context" / "section_cards.yaml"))
    )

    if not config_path.exists():
        return defaults

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Resolve relative paths to project_root
    cache_data = dict(data.get("cache", {}))
    if "path" in cache_data and not Path(cache_data["path"]).is_absolute():
        cache_data["path"] = str(root / cache_data["path"])

    sc_data = dict(data.get("section_cards", {}))
    if "path" in sc_data and not Path(sc_data["path"]).is_absolute():
        sc_data["path"] = str(root / sc_data["path"])

    rtfm_data = dict(data.get("rtfm", {}))
    rtfm_data.setdefault("project_root", str(root))

    return AppConfig(
        version=data.get("version", 1),
        rtfm=RTFMConfig(**rtfm_data),
        context=ContextConfig(**data.get("context", {})),
        cache=CacheConfig(**cache_data) if cache_data else defaults.cache,
        section_cards=SectionCardsConfig(**sc_data) if sc_data else defaults.section_cards
    )
