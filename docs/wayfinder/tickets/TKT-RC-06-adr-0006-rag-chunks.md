# TKT-RC-06 — ADR-0006: RAG chunks renderer

## Question

Write `docs/adr/0006-rag-chunks.md` (Accepted) capturing the whole
ruling: clean-break rebuild of `chunks` (legacy contract deleted, no
compat), the envelope schema, embed assembly ownership (renderer),
summary seam (`facts|none`, glm deferred), walk-back context +
breadcrumbs, source-path IR delta (no bump), caption demotion,
row-split with header duplication, subtraction policy, quality bar.
Alternatives to record: keep-flat-chunks, separate `rag` renderer,
LLM-summary-by-default, whole-table-never-split, row-per-chunk always,
context-in-pipeline-only. Reference ADR-0005 for block provenance and
ADR-0001 for the advisory-blocks doctrine (state explicitly that
chunks now READS blocks for tables while markdown stays authoritative
for text).

Blocked by: [TKT-RC-04](TKT-RC-04-table-chunks.md),
[TKT-RC-05](TKT-RC-05-text-chunks-subtraction.md).

## Resolution

Done 2026-09-14 — `docs/adr/0006-rag-chunks.md` (Accepted), in the
house ADR style: Context (the naked-table failure, quantified from the
user brief + anchors), Decision (clean-break rebuild; 11 locked
sub-decisions: seam, envelope, embed ownership, walk-back,
source_path identity, demotion, row-split, facts summary,
subtraction, embed_context, quality bar), Alternatives (all eight the
ticket named — flat-chunks kept, separate `rag` renderer, LLM summary
by default, never-split, row-per-chunk, pipeline-owned context — plus
paragraph-surrounding-context deferred and the no-IR-change
counterfactual), Consequences (honest: legacy contract gone, noisy
OCR keys, stats-not-prose, local paths in payloads), Implementation
map, References (ADR-0001 doctrine: chunks now READ blocks for
tables, markdown stays authoritative for text; ADR-0005 provenance
consumed).
