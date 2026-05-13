# Testing Strategy for writing-context-rtfm

The `writing-context-rtfm` extension evaluates retrieval optimization and content generation through a 4-layered, end-to-end testing strategy:

## 1. MCP Contract Tests
**Goal**: Verify that external agents can reliably communicate with the server.
- Verifies tool discovery (`tools/list`).
- Verifies input validation and JSON schema adherence for `tools/call`.

## 2. Context-Pack Retrieval Tests
**Goal**: Verify that context retrieved by the extension captures relevant content efficiently.
- Uses `tests/fixtures/mini_latex_project`.
- Measures **Expected Source Recall** (≥ 0.80) to ensure vital sections are retrieved.
- Measures **Irrelevant Source Rate** (≤ 0.30) to ensure the agent isn't distracted.

## 3. Masked-Section Writing Tests
**Goal**: Verify the returned context pack provides sufficient information for an LLM to accurately write a section.
- Provides a mock generator to simulate LLM operations without requiring an active API connection in CI.
- Applies rubrics (`methodology_missing.yaml`) testing idea coverage, term consistency, forbidden claims, and constraints.

## 4. Token Reduction Tests
**Goal**: Quantify exactly how much token context is saved by the tool versus reading the entire repository natively.
- Asserts a baseline `≥ 3x` token reduction ratio to demonstrate the architectural value of the RTFM wrapper.

By separating the tests into Contract, Retrieval, Semantic Evaluation, and Efficiency Layers, we can strictly guarantee that the extension limits context bloat while providing robust LLM writing context.
