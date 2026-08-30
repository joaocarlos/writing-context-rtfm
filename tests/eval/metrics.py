import re


def check_required_terms(text: str, terms: list[str]) -> float:
    if not terms:
        return 1.0
    text_lower = text.lower()
    matches = sum(1 for term in terms if term.lower() in text_lower)
    return matches / len(terms)


def check_forbidden_phrases(text: str, phrases: list[str]) -> int:
    text_lower = text.lower()
    violations = sum(1 for phrase in phrases if phrase.lower() in text_lower)
    return violations


def check_length_bounds(text: str, min_words: int = 50, max_words: int = 1500) -> bool:
    word_count = len(text.split())
    return min_words <= word_count <= max_words


def check_citation_preservation(generated_text: str, gold_text: str) -> float:
    gold_cites = set(re.findall(r"\\cite\{([^}]+)\}", gold_text))
    if not gold_cites:
        return 1.0
    gen_cites = set(re.findall(r"\\cite\{([^}]+)\}", generated_text))
    matches = len(gold_cites.intersection(gen_cites))
    return matches / len(gold_cites)


def check_latex_compile_success(text: str) -> bool:
    stack = []
    for char in text:
        if char == "{":
            stack.append(char)
        elif char == "}":
            if not stack:
                return False
            stack.pop()
    return len(stack) == 0


def check_section_heading_presence(text: str) -> bool:
    return bool(re.search(r"\\section\{[^}]+\}", text))


def lexical_similarity_v2(text_a: str, text_b: str, shingle_size: int = 2) -> float:
    """Compute character and word-shingle Jaccard similarity between two texts."""
    clean_a = re.sub(r"[^\w\s]", " ", text_a.lower()).split()
    clean_b = re.sub(r"[^\w\s]", " ", text_b.lower()).split()
    if not clean_a or not clean_b:
        return 0.0

    # Word unigrams
    words_a = set(clean_a)
    words_b = set(clean_b)
    word_jac = len(words_a & words_b) / max(1, len(words_a | words_b))

    # Word shingles (n-grams)
    if len(clean_a) >= shingle_size and len(clean_b) >= shingle_size:
        shingles_a = {tuple(clean_a[i : i + shingle_size]) for i in range(len(clean_a) - shingle_size + 1)}
        shingles_b = {tuple(clean_b[i : i + shingle_size]) for i in range(len(clean_b) - shingle_size + 1)}
        shingle_jac = len(shingles_a & shingles_b) / max(1, len(shingles_a | shingles_b))
    else:
        shingle_jac = word_jac

    return round(0.4 * word_jac + 0.6 * shingle_jac, 4)


def check_proxy_idea_coverage(
    generated_text: str,
    gold_text: str,
    ideas: list[str] | dict[str, list[str]],
    min_keyword_ratio: float = 0.5,
) -> float:
    """Deterministic proxy evaluation of idea coverage using anchor concept keywords and shingles.

    Note: This is a deterministic proxy metric for CI, not deep LLM semantic comprehension.
    """
    if not ideas:
        return 1.0

    gen_lower = generated_text.lower()
    stop_words = {
        "the", "a", "an", "and", "or", "in", "on", "of", "to", "is", "are", "was",
        "were", "for", "with", "by", "that", "this", "after", "before", "from", "include",
    }

    covered_count = 0

    if isinstance(ideas, dict):
        # Anchor groups: dictionary mapping concept_id -> list of synonymous anchor phrases
        for _concept_id, aliases in ideas.items():
            found = False
            for alias in aliases:
                alias_clean = alias.strip().lower()
                if alias_clean in gen_lower:
                    found = True
                    break
                # Check keyword overlap for multi-word alias
                kw = [w for w in re.sub(r"[^\w\s]", " ", alias_clean).split() if w not in stop_words]
                if kw and sum(1 for w in kw if w in gen_lower) / len(kw) >= min_keyword_ratio:
                    found = True
                    break
            if found:
                covered_count += 1
        return covered_count / len(ideas)

    # List of idea statements
    for idea in ideas:
        idea_clean = idea.strip().lower()
        # Direct substring match
        if idea_clean in gen_lower:
            covered_count += 1
            continue

        # Extract informative keywords from the idea statement
        tokens = re.sub(r"[^\w\s-]", " ", idea_clean).split()
        informative = [t for t in tokens if t not in stop_words and len(t) > 2]
        if not informative:
            covered_count += 1
            continue

        # Check if enough anchor keywords are present in generated text
        matched_tokens = sum(1 for t in informative if t in gen_lower)
        if matched_tokens / len(informative) >= min_keyword_ratio:
            covered_count += 1

    return covered_count / len(ideas)


# Backward compatibility alias
check_idea_coverage = check_proxy_idea_coverage
