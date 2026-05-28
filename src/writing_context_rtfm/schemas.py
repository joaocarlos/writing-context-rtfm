"""Data schemas for the extension."""
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

@dataclass(frozen=True)
class RTFMResult:
    path: str
    line_start: Optional[int]
    line_end: Optional[int]
    snippet: Optional[str]
    score: Optional[float]
    metadata: Dict[str, Any]

@dataclass(frozen=True)
class SourceSpan:
    path: str
    line_start: Optional[int]
    line_end: Optional[int]
    reason: str
    score: float
    priority: str = "background"     # "essential" | "supporting" | "background"
    query: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    source_role: str = "reference"   # "target_text" | "local_context" | "dependency" | "reference"

@dataclass(frozen=True)
class CacheDiagnostics:
    enabled: bool
    hit: bool
    task_hash: Optional[str] = None
    config_hash: Optional[str] = None
    section_cards_hash: Optional[str] = None
    rtfm_index_fingerprint: Optional[str] = None

@dataclass
class PackQuality:
    section_cards_loaded: bool = False
    section_cards_path: Optional[str] = None
    config_loaded: bool = False
    project_root: Optional[str] = None
    queries_issued: int = 0
    candidate_count: int = 0
    selected_count: int = 0
    discarded_low_score: int = 0
    discarded_excluded_path: int = 0
    discarded_avoid_match: int = 0
    dropped_for_budget: int = 0
    truncated: bool = False
    estimated_tokens: int = 0

@dataclass(frozen=True)
class ContextPack:
    task: str
    target: Optional[str]
    document_thesis: Optional[str]
    prior_claims: List[str]
    terminology: Dict[str, str]
    constraints: List[str]
    source_spans: List[SourceSpan]
    estimated_tokens: int
    status: str = "complete"           # "complete" | "degraded"
    warnings: List[str] = field(default_factory=list)
    quality: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    run_id: Optional[str] = None
    cache: Optional[CacheDiagnostics] = None
    task_type: Optional[str] = None
    pack_mode: Optional[str] = None

@dataclass(frozen=True)
class MCPServerConfig:
    command: str
    args: List[str] = field(default_factory=list)

@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = False
    mcp_server: Optional[MCPServerConfig] = None
    sse_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None


