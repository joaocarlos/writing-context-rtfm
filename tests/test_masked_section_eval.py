import unittest

from eval.metrics import (
    check_forbidden_phrases,
    check_latex_compile_success,
    check_length_bounds,
    check_proxy_idea_coverage,
    check_required_terms,
    check_section_heading_presence,
    lexical_similarity_v2,
)
from eval.rubric import load_rubric


class TestMaskedSectionEval(unittest.TestCase):
    def setUp(self):
        self.rubric = load_rubric("tests/fixtures/rubrics/methodology_missing.yaml")
        with open("tests/fixtures/gold/03_approach.tex") as f:
            self.gold_text = f.read()

        # Realistic paraphrased reconstruction covering required ideas and terms
        self.paraphrased_text = r"""\section{Methodology}\label{sec:methodology}
This section outlines the experimental approach for acoustic fault detection in water pumps.
The dataset undergoes a fixed train-test split that is reproducible across all trials.
Crucially, this partitioning split happens after source standardization and feature construction to avoid data leakage.
Raw audio event descriptions are mapped to harmonized event types.
The extracted feature set includes derived attributes such as temporal, spatial, resource-load attributes, and duration metrics.
A convolutional neural network is trained using categorical cross-entropy and the Adam optimizer.
Finally, the model is prepared for microcontroller deployment through post-training quantization.
"""

        # Incomplete reconstruction that misses required split and attribute ideas
        self.incomplete_text = r"""\section{Proposed Approach}
We train a neural network to detect anomalies in audio recordings.
The model uses standard convolution layers followed by dense layers.
Evaluation is performed using accuracy on held-out data.
"""

        # Adversarial reconstruction containing prohibited claims
        self.adversarial_text = r"""\section{System Implementation}
The model is integrated into a fully autonomous emergency management platform for real-time deployment.
We conducted operational validation on industrial pumps with a fixed train-test split.
"""

    def test_gold_reconstruction(self):
        coverage = check_proxy_idea_coverage(
            self.gold_text, self.gold_text, self.rubric.required_ideas
        )
        self.assertEqual(coverage, 1.0, "Gold text must achieve 100% idea coverage")

        term_score = check_required_terms(self.gold_text, self.rubric.required_terms)
        self.assertEqual(term_score, 1.0, "Gold text must contain all required terms")

        unsupported = check_forbidden_phrases(self.gold_text, self.rubric.forbidden_claims)
        self.assertEqual(unsupported, 0, "Gold text must not contain forbidden phrases")

        self.assertTrue(check_length_bounds(self.gold_text))
        self.assertTrue(check_latex_compile_success(self.gold_text))
        self.assertTrue(check_section_heading_presence(self.gold_text))

    def test_paraphrased_reconstruction_passes(self):
        coverage = check_proxy_idea_coverage(
            self.paraphrased_text, self.gold_text, self.rubric.required_ideas
        )
        self.assertGreaterEqual(coverage, 0.75, "Paraphrased text should cover core ideas")

        term_score = check_required_terms(self.paraphrased_text, self.rubric.required_terms)
        self.assertGreaterEqual(term_score, 0.8, "Paraphrased text should cover required terms")

        unsupported = check_forbidden_phrases(self.paraphrased_text, self.rubric.forbidden_claims)
        self.assertEqual(unsupported, 0, "Paraphrased text should not introduce forbidden claims")

        self.assertTrue(check_latex_compile_success(self.paraphrased_text))
        self.assertTrue(check_section_heading_presence(self.paraphrased_text))

    def test_incomplete_reconstruction_fails_threshold(self):
        coverage = check_proxy_idea_coverage(
            self.incomplete_text, self.gold_text, self.rubric.required_ideas
        )
        self.assertLess(coverage, 0.5, "Incomplete text should fail idea coverage threshold")

        term_score = check_required_terms(self.incomplete_text, self.rubric.required_terms)
        self.assertLess(term_score, 0.5, "Incomplete text should fail required terms threshold")

    def test_adversarial_reconstruction_detects_violations(self):
        unsupported = check_forbidden_phrases(self.adversarial_text, self.rubric.forbidden_claims)
        self.assertGreaterEqual(
            unsupported, 2, "Forbidden claims must be detected in adversarial text"
        )

    def test_lexical_similarity_v2(self):
        sim_gold = lexical_similarity_v2(self.gold_text, self.gold_text)
        self.assertEqual(sim_gold, 1.0)

        sim_para = lexical_similarity_v2(self.gold_text, self.paraphrased_text)
        self.assertGreater(sim_para, 0.1)
        self.assertLess(sim_para, 1.0)


if __name__ == "__main__":
    unittest.main()
