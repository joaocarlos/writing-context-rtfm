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


def check_idea_coverage(generated_text: str, gold_text: str, ideas: list[str]) -> float:
    if generated_text.strip() == gold_text.strip():
        return 1.0
    return 0.0
