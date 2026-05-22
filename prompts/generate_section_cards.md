# Prompt: Generate `section_cards.yaml`

Use this prompt with any LLM to generate a `.writing-context/section_cards.yaml` file
for a writing project that uses `writing-context-rtfm`.

Paste your manuscript structure, abstract, and any notes below the `---` separator.

---

## The Prompt

```
You are a writing-context analyst. Your task is to generate a valid
`.writing-context/section_cards.yaml` file for a manuscript project that uses
the `writing-context-rtfm` MCP extension.

## What the file is used for

`section_cards.yaml` is the human-maintained metadata file that tells the context
packer what each section of the document is about, what terms it relies on, what it
must preserve verbatim, and what it must avoid. The packer uses this file to:

- Expand search queries with relevant key terms and section titles
- Apply a +0.8 score boost to chunks from the target section's file
- Apply a 75% penalty to off-target key-term matches
- Inject `must_preserve` strings directly into the final context pack as hard constraints (Writing & Proofreading)
- Discard any retrieved chunk whose content matches a phrase in `avoid`
- Provide surgical constraints for `get_proofreading_context_pack`

Getting this file right is the single highest-leverage action a writer can take to
improve context pack quality.

## Schema

Produce YAML that exactly matches this structure. All fields except `version`,
`document.title`, the section key, and `section.title` are optional — only include
them when you have real content to put there.

```yaml
version: 1                          # always 1

document:
  title: "<manuscript title>"
  thesis: "<1–2 sentence central argument or purpose of the entire manuscript>"
  writing_style:
    tone: "<e.g. academic, formal, concise | technical, instructional | narrative>"
    avoid:
      - "<global phrase or claim type to avoid across all sections>"
      # add more as needed
  terminology:                     # Document-level glossary to standardize key terms
    <term>: "<definition string>"
    # OR:
    <another_term>:
      definition: "<definition string>"
      variants:
        - "<alternate phrase>"
      avoid:
        - "<incorrect/deprecated term to avoid>"

sections:
  <section_id>:                     # snake_case identifier, used as --target in CLI
    title: "<human-readable section title>"
    role: "<one sentence: what this section must accomplish in the manuscript>"
    path: "<relative path to the section file, e.g. sections/02_methodology.tex>"
    key_terms:
      - "<domain-specific term central to this section>"
      # 3–8 terms recommended; more = broader retrieval
    depends_on:
      - <other_section_id>          # sections whose output this section references
    must_preserve:
      - "<exact sentence or claim that must appear verbatim or nearly verbatim>"
      # use for reproducibility statements, key definitions, core claims
    avoid:
      - "<phrase or topic that must not appear in this section's context chunks>"
      # use for things retrieved by key terms but not relevant to this section
    constraints:
      - "<free-form writing constraint, e.g. 'Do not introduce new datasets here'>"
      # constraints end up in the context pack's `constraints` list
      # for proofreading, these are injected as section-specific rules
```

## Field guidance

| Field | When to populate | Effect on the packer |
|---|---|---|
| `thesis` | Always | Injected as `document_thesis` in the pack — the LLM sees it as the overarching goal |
| `writing_style.tone` | Always | Informational only in v0.1; used by future style-enforcement pass |
| `writing_style.avoid` | When global prohibitions exist | Not yet consumed by packer in v0.1; acts as human reminder |
| `terminology` | For key technical terms and concepts | Defines a global glossary. Used by the `audit` command to verify consistency/drift and injected into the proofreading context pack. |
| `key_terms` | Always for technical sections | Each term becomes its own RTFM search query |
| `depends_on` | When a section cites work from another section | Adds +0.4 score boost to dependency file chunks |
| `must_preserve` | For fixed claims, definitions, caveats | Injected verbatim into `constraints` in the context pack. In proofreading, ensures claims are not altered. |
| `avoid` | When a key term appears in many wrong sections | Discards retrieved chunks that match these phrases. Filters noisy terminology examples. |
| `constraints` | For structural or rhetorical rules | Injected into `constraints` in the context pack for writing or proofreading. |
| `path` | Always | Without a path, the packer cannot apply Target Boost or Key-Term Scope Penalty |

## Rules you must follow

1. Every section must have a `path`. Use the actual relative path from the project root
   (e.g. `sections/03_approach.tex`, `chapters/methodology.md`).
2. The `section_id` key (e.g. `section_approach`) must be a valid snake_case identifier
   with no spaces — it is passed directly to `--target` on the CLI.
3. `depends_on` must reference other `section_id` keys that exist in the same file.
4. Write `key_terms` as the exact phrases the writer would use in the text, not
   generalised synonyms. Wrong: `"machine learning"`. Right: `"quantization-aware training"`.
5. `must_preserve` items should be sentences or short phrases that must survive rewrites
   intact. Do not put instructions here — only content.
6. `avoid` items should be short phrases (2–5 words) that would appear in retrieved
   snippets you want filtered out.
7. Use double-quoted strings for all YAML values that contain colons, commas, or
   special characters.
8. Order sections in logical reading order.
9. Define critical technical terms under `document.terminology` so that they can be audited for semantic drift and checked during proofreading. Use the dictionary format when you want to enforce specific avoids or variants.

## What I will provide

I will now give you:

[A] The manuscript's abstract or summary (required)
[B] The list of section files and their titles (required)
[C] Any constraints, caveats, or fixed claims you already know must appear (optional)
[D] The writing tone and any global avoidance rules (optional)
[E] Key technical terms and definitions/glossary (optional)

Generate the complete `section_cards.yaml` in a single fenced YAML code block.
After the block, provide a brief bullet list explaining any assumptions you made
for fields you could not derive directly from my input.

---

[Paste your manuscript context below this line]

[A] Abstract / summary:
<your abstract here>

[B] Section files and titles:
<list your sections here, e.g.:
- sections/01_introduction.tex — Introduction
- sections/02_related_work.tex — Related Work
- sections/03_methodology.tex — Methodology
- sections/04_results.tex — Results and Discussion
- sections/05_conclusion.tex — Conclusion
>

[C] Fixed claims / must-preserve sentences (optional):
<list any verbatim sentences that must survive rewrites>

[D] Writing tone and global avoidance rules (optional):
<tone description and any phrases to globally avoid>

[E] Key technical terms and definitions/glossary (optional):
<list any domain-specific terms, their definitions, variants, and deprecated variants to avoid>
```

