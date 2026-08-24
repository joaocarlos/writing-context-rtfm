"""Data schemas for the extension."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RTFMResult:
    path: str
    line_start: int | None
    line_end: int | None
    snippet: str | None
    score: float | None
    metadata: dict[str, Any]


@dataclass(frozen=True, eq=False)
class QuerySpec:
    text: str
    query_type: str  # task, intent, title, key_term, dep_intent, dep_title, dep_key_term, task_keyword, must_consider, thesis
    family: str = "task"  # task, intent, terms, deps, thesis
    weight: float = 1.0
    is_verified: bool = True
    metadata: dict[str, Any] | None = None

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.text == other
        if isinstance(other, QuerySpec):
            return (self.text, self.query_type, self.family, self.weight, self.is_verified) == (
                other.text,
                other.query_type,
                other.family,
                other.weight,
                other.is_verified,
            )
        return False

    def __hash__(self) -> int:
        return hash((self.text, self.query_type, self.family, self.weight, self.is_verified))



@dataclass(frozen=True)
class SourceSpan:
    path: str
    line_start: int | None
    line_end: int | None
    reason: str
    score: float
    priority: str = "background"  # "essential" | "supporting" | "background"
    query: str | None = None
    metadata: dict[str, Any] | None = None
    source_role: str = "reference"  # "target_text" | "local_context" | "dependency" | "reference"
    retrieval_score: float | None = None
    fusion_score: float | None = None
    structural_score: float | None = None


@dataclass(frozen=True)
class CacheDiagnostics:
    enabled: bool
    hit: bool
    task_hash: str | None = None
    config_hash: str | None = None
    section_cards_hash: str | None = None
    rtfm_index_fingerprint: str | None = None


@dataclass
class PackQuality:
    section_cards_loaded: bool = False
    section_cards_path: str | None = None
    config_loaded: bool = False
    project_root: str | None = None
    queries_issued: int = 0
    candidate_count: int = 0
    selected_count: int = 0
    discarded_low_score: int = 0
    discarded_excluded_path: int = 0
    discarded_avoid_match: int = 0
    dropped_for_budget: int = 0
    truncated: bool = False
    estimated_tokens: int = 0
    minimum_required_tokens: int | None = None
    card_uncertainties: dict[str, Any] | None = None
    atomic_coverage: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ContextPack:
    task: str
    target: str | None
    document_thesis: str | None
    prior_claims: list[str]
    terminology: dict[str, str]
    constraints: list[str]
    source_spans: list[SourceSpan]
    estimated_tokens: int
    status: str = "complete"  # "complete" | "degraded"
    warnings: list[str] = field(default_factory=list)
    quality: dict[str, Any] | None = None
    summary: str | None = None
    run_id: str | None = None
    cache: CacheDiagnostics | None = None
    task_type: str | None = None
    pack_mode: str | None = None


@dataclass(frozen=True)
class MCPServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = False
    mcp_server: MCPServerConfig | None = None
    sse_url: str | None = None
    headers: dict[str, str] | None = None
    extra: dict[str, Any] | None = None
