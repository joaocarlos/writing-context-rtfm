"""Configuration schema and loading."""
from pathlib import Path
from typing import Dict
import yaml
from dataclasses import dataclass, field

@dataclass(frozen=True)
class RTFMConfig:
    corpus: str = "manuscript"
    project_root: str = "."
    sync_before_pack: bool = True

@dataclass(frozen=True)
class ContextConfig:
    default_token_budget: int = 6000
    reserved_generation_margin: float = 0.10
    max_search_results_per_query: int = 10
    max_source_spans: int = 20
    include_source_excerpts: bool = False
    min_score: float = 0.01
    min_relative_score: float = 0.05
    role_budgets: Dict[str, float] = field(default_factory=lambda: {
        "target_text": 0.35,
        "local_context": 0.15,
        "dependency": 0.30,
        "reference": 0.20
    })

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

    # Ensure sections are dictionaries if present
    for key in ("cache", "section_cards", "rtfm", "context"):
        val = data.get(key)
        if val is not None and not isinstance(val, dict):
            raise TypeError(f"'{key}' section in config must be a dictionary, got {type(val).__name__}")

    # Resolve relative paths to project_root
    cache_data = dict(data.get("cache") or {})
    if "path" in cache_data and not Path(cache_data["path"]).is_absolute():
        cache_data["path"] = str(root / cache_data["path"])

    sc_data = dict(data.get("section_cards") or {})
    if "path" in sc_data and not Path(sc_data["path"]).is_absolute():
        sc_data["path"] = str(root / sc_data["path"])

    rtfm_data = dict(data.get("rtfm") or {})
    rtfm_data.setdefault("project_root", str(root))

    context_data = dict(data.get("context") or {})
    if "role_budgets" in context_data:
        defaults_budgets = {
            "target_text": 0.35,
            "local_context": 0.15,
            "dependency": 0.30,
            "reference": 0.20
        }
        user_budgets = context_data["role_budgets"]
        if isinstance(user_budgets, dict):
            merged_budgets = {**defaults_budgets, **user_budgets}
            context_data["role_budgets"] = merged_budgets

    return AppConfig(
        version=data.get("version", 1),
        rtfm=RTFMConfig(**rtfm_data),
        context=ContextConfig(**context_data),
        cache=CacheConfig(**cache_data) if cache_data else defaults.cache,
        section_cards=SectionCardsConfig(**sc_data) if sc_data else defaults.section_cards
    )
