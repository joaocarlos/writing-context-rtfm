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


# --- Canonical Reason Taxonomy ---------------------------------------------
# 1. Filtering reasons (elimination during candidate preparation)
FILTER_LOW_SCORE = "FILTER_LOW_SCORE"
FILTER_AVOID_PATTERN = "FILTER_AVOID_PATTERN"
FILTER_UNALLOWED_PATH = "FILTER_UNALLOWED_PATH"

# 2. Exposure / Ownership reasons (exclusion by structured provider ownership)
EXCLUDE_PROVIDER_OWNERSHIP = "EXCLUDE_PROVIDER_OWNERSHIP"

# 3. Composition rejection reasons (budget & quota limits in composer)
REJECT_TOKEN_BUDGET = "REJECT_TOKEN_BUDGET"
REJECT_MAX_SOURCE_SPANS = "REJECT_MAX_SOURCE_SPANS"
REJECT_PROVIDER_REFERENCE_QUOTA = "REJECT_PROVIDER_REFERENCE_QUOTA"


@dataclass(frozen=True)
class CandidateTraceEvent:
    stage: str  # "retrieved", "normalized", "deduplicated", "provider_owned", "exposed", "filtered", "selected", "rejected"
    action: str  # "ingest", "snap_ast", "dedup_drop", "dedup_keep", "exclude_ownership", "filter_score", "filter_avoid", "filter_unallowed", "rank_mmr", "select_quota", "reject_quota"
    reason: str | None = None  # Canonical taxonomy string
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateTrace:
    candidate_id: str
    evidence_id: str | None
    path: str
    line_start: int | None
    line_end: int | None
    source_role: str
    events: tuple[CandidateTraceEvent, ...] = ()


@dataclass(frozen=True)
class OwnershipAuditRecord:
    candidate_id: str
    evidence_id: str
    path: str
    line_start: int | None
    line_end: int | None
    identities: list[str]
    replacement_found: bool
    replacement_candidate_id: str | None = None
    replacement_provider: str | None = None


@dataclass(frozen=True)
class CandidateFunnel:
    """Cardinality summary of candidate progression through the context selection funnel.

    Transition semantics:
    - retrieved: Total raw candidate instances returned across all search queries and active providers.
    - normalized: Candidates processed through AST boundary snapping (LaTeX / Markdown environments).
    - deduplicated: Unique non-overlapping candidates surviving deduplication (retrieved >= normalized >= deduplicated).
    - excluded: Candidates partitioned out due to structured provider ownership (e.g. raw .bib files owned by BibTeX provider).
    - exposed: Candidate pool forwarded to filtering and MMR ranking (exposed = deduplicated - excluded).
    - filtered: Candidates eliminated during score thresholding (FILTER_LOW_SCORE) or avoid pattern matching (FILTER_AVOID_PATTERN).
    - eligible: Candidates surviving filtering and available for composer quota allocation (eligible = exposed - filtered).
    - selected: Candidates chosen by the composer within role budgets, token budget ceiling, and max span limits (selected <= eligible).
    """

    retrieved: int = 0
    normalized: int = 0
    deduplicated: int = 0
    excluded: int = 0
    exposed: int = 0
    filtered: int = 0
    eligible: int = 0
    selected: int = 0


@dataclass(frozen=True)
class ContextPackDiagnostics:
    """Process explanation diagnostics describing why context candidates were retrieved, filtered, or selected.

    NOTE: Diagnostics expose pipeline decisions but do not constitute a quality score or guarantee that
    omitted evidence is irrelevant.
    """

    funnel: CandidateFunnel
    candidates: list[CandidateTrace]
    ownership_audit: list[OwnershipAuditRecord]
    rejections_by_reason: dict[str, int]


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
    diagnostics: ContextPackDiagnostics | None = None


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
