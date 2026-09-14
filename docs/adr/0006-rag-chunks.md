# ADR-0006: RAG chunks — hybrid envelope renderer

- **Status:** Accepted
- **Date:** 2026-09-14
- **Supersedes:** the legacy `chunks` renderer contract (flat text chunks
  built from `page.markdown` alone; the pre-v0.7 placeholder, its TOML
  keys and its tests are deleted — clean break, no compat shim)
- **Decided via:** wayfinder map `docs/wayfinder/v0.7-rag-chunks.md`
  (tickets TKT-RC-01..08)

## Context

The v0.6 map delivered IR 0.5 — `TableBlock` (markdown, rows,
header_rows, source) and `HeadingBlock` on every OCR page — but the
chunks renderer never read blocks: it flattened `page.markdown` into
text chunks, so tables entered a RAG store twice (once as scrambled
pipe-text, once as nothing useful), naked of context. The user's RAG
brief quantifies the failure:

- **Embedding context loss**: a question like «какая чистая прибыль была
  у Вектора в 3 квартале?» cannot bind to a bare row `| Q3 2025 | 210 |
  45 | 165 |` — «Вектор» and «Чистая прибыль» live in the caption/header
  far away or only in the filename.
- **Chunking hazard**: a long table split mid-body loses its header
  band; the bottom rows become meaningless to an LLM.
- **Generation format**: pipe-tables (GitHub markdown) stay the best
  generation-time shape; merged cells must be flattened.

The destination contract of map v0.7: a working end-to-end chunks
renderer that emits hybrid chunks (context + table + deterministic
summary; key-value embed text), verified on the v0.6 golden anchors
(Рыбы p.14, Генетика p.34, the native journal). Standing constraints
carry over: deterministic default path, no cloud calls, minimum
entities (one file per concern), markdown stays authoritative (ADR-0001).

## Decision

**Rebuild the `chunks` renderer in place (clean break): every chunk is
one JSONL envelope (`chunk_version: 2`) discriminated by `type`; the
renderer owns embed-text assembly; tables get context, KV embed text and
a deterministic facts summary; text gets pipe-table subtraction and an
optional context preamble.**

Locked sub-decisions:

1. **Clean break, not a mode (TKT-RC-01).** The legacy contract, its
   tests and the placeholder-era config are deleted; no `legacy` mode,
   no dual contract. No external consumers exist. New TOML seam:
   `[renderers.chunks] max_tokens, overlap_tokens, summary =
   "facts"|"none", embed_context = true|false`. `summary = "glm"` is a
   reserved value the config rejects until implemented (deferred, see
   Alternatives).
2. **Envelope schema (TKT-RC-03).** One schema for all chunks:
   `chunk_id` (`{page}-t{n}` for tables, `{page}-{seq:03d}` for text;
   `-{k:02d}` suffix on split parts), `chunk_version: 2`, `type ∈
   {table, table_part, text}`, `embed` (the vectorization text the
   renderer assembles), `markdown` (the generation text), `metadata`
   (`doc_id, doc_title, source_format, page, section, src_tool, error`;
   table chunks add `table: {table_id, part, parts, n_rows, n_cols,
   header, source}`), `images` (page sidecars, unchanged seam).
3. **Renderer owns embed assembly (TKT-RC-03).** The pipeline embeds
   `chunk["embed"]` as-is. For tables the embed is the linear key-value
   form (`"Ключ: значение, ..."` per data row) — the shape embedding
   models bind to — prefixed by context and closed by the summary.
   Multi-vector retrievers can still slice the stored envelope; the
   zvec side stays out of scope.
4. **Context walk-back + breadcrumbs (TKT-RC-03).** HeadingBlocks are
   walked in document order (per page: OCR blocks sorted by raster-pixel
   `(y0, x0)` — geometry is consistent within a page —; native pages
   keep IR reading order). A chunk inherits the nearest previous heading
   across page boundaries via an open heading stack; `section` falls
   back to the first `##`–`####` heading of the page markdown, then
   `doc_title`. No LLM contextual-retrieval in the default path.
5. **Identity: `Metadata.source_path` (TKT-RC-02).** One additive IR
   field (default `None` — old payloads load unchanged, **no IR_VERSION
   bump**), filled by `core.process_pdf`. `doc_id` = the TKT-RT-12
   12-hex SHA-256 of the resolved path (chunks and sidecar image
   folders share one id); `doc_title` = `Metadata.title` → basename
   fallback.
6. **Caption demotion (TKT-RC-04).** `rows[0]` demotes to caption when
   it holds a single content cell, or any content cell starts with a
   strong caption prefix (Таблица/Табл./Table) — the live Рыбы p.14
   anchor glues «Таблица 2» into a header-band cell. Keys then come
   from `rows[1]`. «№» is deliberately not a demotion prefix (it fronts
   real «№ п/п» headers). `block.markdown` still rides verbatim in the
   `markdown` field — nothing is lost.
7. **Row-split with header duplication (TKT-RC-04).** Whole table =
   one chunk while its embed fits `max_tokens`; over budget it splits
   by rows (rows atomic, never cut mid-row), every part repeating
   context + header band + a **local** facts summary, sharing
   `table_id` with `part`/`parts` numbering. Budget metric is embed
   text length (4 chars ≈ 1 token — dependency-free, deterministic).
8. **Facts summary (TKT-RC-01).** `summary = "facts"`: deterministic
   grid stats — numeric column detection (spaces as thousand separators,
   comma decimals, trailing %), min/max/sum per numeric column,
   first/last row mention. `"none"` emits nothing. Same summary rides
   inside `markdown`.
9. **Subtraction policy (TKT-RC-05).** Table chunks always come from
   `TableBlock`; text chunks come from `page.markdown` (IR doctrine
   intact — markdown stays authoritative for text). Pipe-table regions
   are removed from the text where a block covers them: exact
   whitespace-normalized match on native pages, ANY pipe region on OCR
   pages (the OCR markdown twin is always the scrambled flattening of a
   block). Pipe tables with no block stay in the text verbatim — honest
   degradation for old IRs or failed structure passes. A page whose
   markdown empties out yields table chunks only.
10. **`embed_context` preamble (TKT-RC-05).** When on, every chunk's
    embed opens with `doc_title › heading` (tables: + caption line);
    the preamble counts against `max_tokens` (floor `64` chars of body
    text), never on top of the budget.
11. **Quality bar (TKT-RC-07).** Three tiers, mirroring ADR-0005 §8:
    fast = synthetic IR with a shared contract validator (envelope keys,
    demotion, cross-page walk-back, split invariants, KV form,
    subtraction, toggles, doc_id, determinism); middle (slow marker) =
    the committed Рыбы p.14 crop through the live lineless engine;
    slow = corpus anchors — Рыбы p.14 + Генетика p.34 through real
    OCR+structure, the native journal through `process_pdf` (fuzzy
    content asserts, scan noise per the ST-05 bar).

## Alternatives considered

1. **Keep the flat-chunks contract.** Rejected — the flat renderer is
   the problem statement: tables without context, doubled or lost,
   unembeddable.
2. **A separate `rag` renderer next to `chunks`.** Rejected — two
   renderers emitting near-identical text output invites "which one is
   real"; the consumer is singular, ethos is minimum entities.
3. **LLM-generated summaries by default.** Rejected for the default
   path — non-determinism and a new cloud call class; deterministic
   facts carry the embedding-binding load. glm summary stays a reserved
   seam (`summary = "glm"`), opt-in later, same policy as engines
   (ADR-0005 §6).
4. **Whole table always one chunk (never split).** Rejected — the
   42×6 Рыбы grid blows any reasonable budget and smears the embedding;
   split-with-duplicated-header keeps both shape and budget.
5. **Row-per-chunk always (multi-vector in the renderer).** Rejected —
   ~40× chunk inflation and reassembly duty pushed onto the pipeline;
   the renderer emits one embed per chunk, multi-vector slicing stays a
   consumer-side read of the stored envelope.
6. **Context assembled by the pipeline (`embed: null`).** Rejected —
   embed assembly is the point of this work; leaving key-value gluing
   to every consumer duplicates the knowledge this renderer exists to
   own.
7. **Paragraph-level surrounding context (abstract above/below the
   table).** Deferred — heading + caption + facts already bind the
   query; paragraph extraction on scans needs "what is a paragraph"
   heuristics v1 does not have.
8. **Bump IR for source identity or keep chunks document-blind.**
   Rejected both ways — an additive optional field (no bump) buys
   self-sufficient chunks that name their own document; the id reuses
   the sidecar scheme so all artifacts share one identifier.

## Consequences

Positive:

- Tables enter the RAG store once, hybrid: context for the embedding,
  pipe grid for generation, facts for keyword-ish binding — the map's
  destination, verified on the golden anchors.
- The contract is one JSONL schema; consumers embed `embed`, show
  `markdown`, filter on `metadata` — no renderer knowledge leaks
  downstream.
- Deterministic end-to-end: same IR in, byte-identical JSONL out; all
  quality gates runnable offline (fast tier) or on corpus paths (slow
  tier), per ADR-0005's tier discipline.

Negative:

- The legacy chunk contract is gone outright — anything that consumed
  pre-v0.7 `.chunks.jsonl` files breaks (no external consumers;
  recorded for honesty).
- The KV embed keys are only as good as the recovered header band: a
  scrambled OCR header yields noisy keys (the anchors show this is
  tolerable; fuzzy gates hold the bar).
- Facts summaries are stats, not prose — worse reading than an LLM
  summary by design; the glm seam remains unimplemented until a map
  pulls it in.
- `Metadata.source_path` flows absolute local paths into IR payloads;
  consumers should treat it as a machine-local identity hint, not a
  portable URI.

## Implementation

- `src/pdf_router/renderers/chunks.py` — full rebuild: envelope v2,
  walk-back (`_reading_order`, `_HeadingStack`), caption demotion +
  KV lines + facts summary + pipe rebuild, row-split policy, pipe-table
  subtraction, embed preamble; module docstring is the contract
  reference.
- `src/pdf_router/ir.py` — `Metadata.source_path` (additive, no bump);
  `src/pdf_router/core.py` — fills it in `process_pdf`.
- `src/pdf_router/config.py` — `ChunksConfig` (`max_tokens`,
  `overlap_tokens`, `summary`, `embed_context`); `summary = "glm"`
  rejected at config-parse time until implemented;
  `router.toml.example` shows the section.
- Tests: `tests/test_chunks_rag.py` (three tiers + shared validator);
  legacy chunks tests deleted; `test_renderers.py` / `test_docx.py` /
  `test_p5_e2e.py` / `test_cli_smoke.py` / `test_api.py` re-pinned to
  the envelope.
- Docs: README chunks section, glossary entries (envelope, KV embed,
  subtraction), map ticket resolutions.

## References

- Wayfinder map `docs/wayfinder/v0.7-rag-chunks.md`; tickets
  TKT-RC-01..08.
- ADR-0001 (markdown authoritative, blocks advisory — chunks now READ
  blocks for tables while text stays markdown-driven), ADR-0005 (block
  provenance sources this renderer consumes: `ocr_table_lineless` et
  al.), ADR-0004 §7 (the raster seam precedent for one-file seams).
- User RAG brief (map v0.7 source): hybrid chunk anatomy, KV-linear
  form for embeddings, markdown for generation, caption/header
  duplication on split, multi-vector as consumer-side concern
  (zvec stays out of scope).
