"""Ranking logic for source spans."""
from typing import List, Optional
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.section_cards import SectionCard

def score_candidate(result: RTFMResult, target_card: Optional[SectionCard], dependency_cards: List[SectionCard], explicit_must_consider: List[str]) -> float:
    """Rank a candidate span based on its relevance."""
    score = 0.0
    
    # + 2.0 if result path equals target section path
    if target_card and result.path == target_card.path:
        score += 2.0
        
    # + 1.5 if result path equals dependency section path
    dep_paths = [dep.path for dep in dependency_cards if dep.path]
    if result.path in dep_paths:
        score += 1.5
        
    # + 1.0 for each key-term match
    all_key_terms = []
    if target_card and target_card.key_terms:
        all_key_terms.extend(target_card.key_terms)
    for dep in dependency_cards:
        if dep.key_terms:
            all_key_terms.extend(dep.key_terms)
            
    content = result.snippet or ""
    path_lower = result.path.lower()
    for term in all_key_terms:
        if term.lower() in content.lower() or term.lower() in path_lower:
            score += 1.0
            
    # + 1.0 if result path appears in must_consider
    if any(explicit.lower() in path_lower for explicit in explicit_must_consider):
        score += 1.0
        
    # + 0.5 if result is from outline/report/notes directory
    if any(x in path_lower for x in ["outline", "report", "notes"]):
        score += 0.5
        
    # + 0.1 × RTFM score, if available
    if result.score is not None:
        score += 0.1 * result.score
        
    # - 1.0 if result path matches ignored/generated patterns
    if any(x in path_lower for x in ["generated", "build", "ignored"]):
        score -= 1.0
        
    return max(0.0, score)
