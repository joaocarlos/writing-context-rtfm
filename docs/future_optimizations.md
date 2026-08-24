# Future Optimizations: Semantic Retrieval & Advanced Architecture

This document outlines future iteration plans for introducing transformer-based semantic retrieval to the `writing-context-rtfm` codebase, alongside other high-value architectural optimizations.

---

## 1. BERT-Based Semantic Retrieval & Hybrid Search

The current search mechanism relies entirely on RTFM's lexical query matcher. While fast and precise for exact keyword lookups, it misses semantic synonyms and paraphrase alignments (e.g., matching "performance bottleneck" with "database slowdown").

To address this, we propose a **Hybrid Retrieval (Sparse + Dense)** architecture using local transformer models.

### Proposed Architecture

```mermaid
graph TD
    Query[User Task Query] --> RTFM_Search[BM25 / Lexical Retrieval via RTFM]
    Query --> Embed_Query[SBERT Query Embedding]
    Embed_Query --> Vector_Search[Vector Search via SQLite-VSS]
    RTFM_Search --> RRF[Reciprocal Rank Fusion RRF]
    Vector_Search --> RRF
    RRF --> Candidates[Top-K Candidates]
    Candidates --> Cross_Encoder[Cross-Encoder Re-ranker]
    Cross_Encoder --> Final_Spans[Prioritized Source Spans]
```

### Component Details

#### Evaluation Candidates for v0.11.0

The first local evaluation should compare two embedding tiers and one optional reranker:

* **Quality-oriented embedding model**: `mixedbread-ai/mxbai-embed-large-v1`.
* **Lightweight embedding model**: `sentence-transformers/all-MiniLM-L6-v2`.
* **Optional deep reranker**: `Alibaba-NLP/gte-reranker-modernbert-base`, applied only to a bounded top candidate set.

These are evaluation candidates, not release defaults. Selection should be based on required-evidence coverage, irrelevant-source rate, warm-query latency, memory use, and the amount of manual context repair needed in personal writing tasks. Exact citation keys, labels, references, equations, and protected numeric values must continue to bypass semantic matching.

#### A. Dense Embedding Retrieval (Bi-Encoder)
*   **Pluggable Drivers**: Supports both local CPU/GPU execution and remote cloud APIs.
*   **Model Selection**:
    *   *Local (Quality Candidate)*: `mixedbread-ai/mxbai-embed-large-v1`.
    *   *Local (Lightweight Candidate)*: `sentence-transformers/all-MiniLM-L6-v2`.
*   **Storage**: Leverage `numpy` for blazing-fast, zero-dependency in-memory cosine similarity, storing vectors as raw BLOBs in the existing `.writing-context/context_cache.sqlite` database. This avoids complex C++ extension compilation (`sqlite-vss`) across different OS platforms.
*   **Index Construction**:
    *   Compute embeddings during an explicit foreground synchronization step. Do not launch detached or recursively restarting background workers.
    *   Store vector blobs alongside chunk metadata in standard SQLite tables, keyed by content hash, model identifier, model revision, and chunking-policy version.
    *   Load vectors in bounded batches or memory maps and guarantee model/process cleanup when synchronization finishes.

#### B. Reciprocal Rank Fusion (RRF)
To combine BM25 scores (lexical) and cosine similarities (dense) without calibrating distinct scale systems, apply Reciprocal Rank Fusion:
$$\text{RRF Score}(d \in D) = \sum_{m \in \{\text{lexical}, \text{dense}\}} \frac{1}{k + r_m(d)}$$
Where $r_m(d)$ is the rank of document/chunk $d$ in the retriever $m$, and $k$ is a constant (typically $60$) that mitigates the impact of low-ranked outliers.

#### C. Cross-Encoder Re-ranking (Deep Mode / Maximum Accuracy)
For the `"deep"` packing mode, pass the top-ranked hybrid results to a Cross-Encoder to compute full cross-attention:
*   **Model Selection**: Evaluate `Alibaba-NLP/gte-reranker-modernbert-base` as an optional deep-mode reranker.
*   **Filtering**: Rerank only a bounded lexical/dense candidate set. Calibrate any threshold on personal writing tasks; do not treat semantic relevance as proof that a factual, citation, numeric, or protected-content obligation is satisfied.

#### D. Configuration Schema (`.writing-context/config.yaml`)
To support switching between local lightweight configurations and maximum-accuracy environments, the following schema will be used:
```yaml
retrieval:
  hybrid_search:
    enabled: true
    dense_weight: 0.5
    sparse_weight: 0.5
    
  embedding:
    provider: local  # options: local, gemini, openai
    model_name: "mixedbread-ai/mxbai-embed-large-v1"
    
  reranker:
    enabled: true
    provider: local  # options: local, cohere, jina
    model_name: "Alibaba-NLP/gte-reranker-modernbert-base"
```

---

## 2. LaTeX AST Parsing & Reference Graph

Regex patterns inside `utils.py` are prone to false positives/negatives in complex LaTeX environments (e.g., nested brackets, macro-definitions). Replacing them with a dedicated AST parser will improve reliability.

### Implementation Plan
1.  **Introduce AST Dependency**: Use `TexSoup` (a lightweight pythonic parser for LaTeX) or implement a custom lightweight token-based parser if we want to minimize runtime dependencies.
2.  **Generate Dependency Mapping**:
    *   Build a complete graph of the manuscript by matching `\label{key}` to all corresponding `\ref{key}`, `\cref{key}`, or `\cite{key}` references.
    *   Map `\input{filename}` and `\include{filename}` to build the physical file hierarchy.
3.  **Automatic Section Card Scaffolding**:
    *   Update `initialize_section_cards` to automatically populate the `depends_on` metadata by mapping references across section boundaries. For instance, if `section_results.tex` contains a `\ref{fig:method}` defined in `section_method.tex`, establish a dependency relationship automatically.

---

## 3. Cache Compression

Currently, the SQLite database stores context pack payloads as raw JSON strings. As run histories accumulate, SQLite DB file sizes scale up rapidly.

### Implementation Plan
1.  **Surgical Compression**: Compress `payload_json` in `context_pack_payloads` using Python's standard `zlib` or `lzma` libraries.
2.  **Database Schemas Adaptation**:
    *   Store data as `BLOB` instead of `TEXT`.
    *   Wrap read/write operations in the `ExtensionStore` with transparent compression/decompression helpers:
        ```python
        def _compress(data: str) -> bytes:
            return zlib.compress(data.encode("utf-8"), level=6)


        def _decompress(blob: bytes) -> str:
            return zlib.decompress(blob).decode("utf-8")
        ```

---

## 4. Parallelized LLM Evaluation (A/B Testing Runner)

To evaluate context pack quality improvements dynamically, the extension needs a faster offline evaluation pipeline.

### Implementation Plan
1.  **Concurrent Execution**: Leverage `asyncio` combined with a connection pool to run evaluations concurrently against LLM inference APIs (e.g., Gemini or OpenAI endpoints).
2.  **A/B Test Design**:
    *   Generate a set of task runs.
    *   Run parallel evaluations: Group A (Lexical Context Pack) vs. Group B (Hybrid/Semantic Context Pack).
    *   Compute and store evaluation statistics (such as token savings, semantic coverage, hallucination rate) in the `evaluation_records` SQLite table.
