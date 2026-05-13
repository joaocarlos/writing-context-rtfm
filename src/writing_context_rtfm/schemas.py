"""Data schemas for the extension."""
from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass(frozen=True)
class RTFMResult:
    path: str
    line_start: Optional[int]
    line_end: Optional[int]
    snippet: Optional[str]
    score: Optional[float]
    metadata: Dict[str, Any]
