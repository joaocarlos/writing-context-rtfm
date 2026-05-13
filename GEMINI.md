# writing-context-rtfm

## Project Overview

`writing-context-rtfm` is a lightweight MCP extension designed to reduce token usage when an AI agent writes, rewrites, expands, or reviews a document that depends on prior project context. 

The core philosophy:
> **RTFM retrieves.**
> **writing-context-rtfm decides what is enough context to write.**

This extension does **not** replace or fork RTFM. Instead, it relies on RTFM as the backend indexing and retrieval engine, wrapping it with an adapter to generate compact, task-specific **writing context packs** for CLI agents.

## Core Principles

1. **Keep RTFM as a Dependency, Not a Fork:** Interact with RTFM via its CLI, official API, or MCP tools. Do not write to RTFM's database.
2. **Keep the First Version Narrow:** Focus on deciding *what previous context the agent needs for a task* rather than building a full manuscript knowledge graph.
3. **Hybrid Storage Strategy:**
   - `.rtfm/library.db`: Retrieval index managed by RTFM.
   - `.writing-context/config.yaml`: Human-maintained project configuration.
   - `.writing-context/section_cards.yaml`: Human-maintained writing guidance.
   - `.writing-context/context_cache.sqlite`: Extension-generated cache, run history, and evaluation records.

## Key Concepts

- **Context Packs:** Compact JSON structures returned to the agent that include prioritized source spans, token estimates, and task constraints.
- **Section Cards (`section_cards.yaml`):** Human-authored metadata describing the document thesis, sections, roles, dependencies, key terms, and constraints.
- **RTFM Adapter:** A thin wrapper to `search`, `context`, `expand`, and `sync` via RTFM without knowing its internal database schema.

## Development Stack

- **Language:** Python 3.11+
- **Package Manager:** `uv`
- **Dependencies:** `rtfm-ai`

## Project Layout (Target)

```text
writing-context-rtfm/
├── pyproject.toml
├── README.md
├── docs/
│   ├── writing-context-rtfm_architecture.md
│   └── writing-context-rtfm_detailed_design.md
├── src/
│   └── writing_context_rtfm/
│       ├── rtfm_adapter.py
│       ├── section_cards.py
│       ├── context_pack.py
│       ├── storage.py
│       └── server.py
└── tests/
```

## Development Flow

For implementation and development flow, you must use the following documents as your primary references:
- `docs/writing-context-rtfm_architecture.md`: Contains the high-level architectural decisions and step-by-step progress checklists.
- `docs/writing-context-rtfm_detailed_design.md`: Contains the detailed implementation design, component schemas, and algorithms.

## Agent Workflow Instructions

When an agent interacts with a manuscript using this extension:
1. **Always** call `get_writing_context_pack` before writing, rewriting, or expanding text.
2. **Do not** read the whole repository freely unless the user explicitly asks for full-document analysis.
3. Use the returned source spans as your primary context.
4. If the context is insufficient, request targeted expansion only for the missing span.

## Available Skills

The repository incorporates specific AI skills located in the `.gemini/skills/` directory.

*Note: Since the assistant automatically finds and incorporates these skills during development, no manual inclusion of these skill paths is necessary in regular prompts. You can just ask the agent to execute tasks (e.g., "Write tests for X" or "Document Y") and the relevant skills will be natively applied.*
