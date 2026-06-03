from dataclasses import dataclass

import yaml


@dataclass
class Rubric:
    test_case: str
    required_ideas: list[str]
    required_terms: list[str]
    forbidden_claims: list[str]
    style_constraints: list[str]


def load_rubric(path: str) -> Rubric:
    with open(path) as f:
        data = yaml.safe_load(f)
    return Rubric(
        test_case=data.get("test_case", ""),
        required_ideas=data.get("required_ideas", []),
        required_terms=data.get("required_terms", []),
        forbidden_claims=data.get("forbidden_claims", []),
        style_constraints=data.get("style_constraints", []),
    )
