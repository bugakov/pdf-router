# TKT-RC-08 — Implement: rebuild chunks renderer end-to-end

## Question

Execute the rebuild in `src/pdf_router/renderers/chunks.py` (single
file; split internally only if it outgrows readability — prefer flat
functions per ethos). Wire everything RC-03/04/05 contract: envelope
emitter, walk-back context, table pipeline (demotion → facts → KV
embed → split), text pipeline (subtraction → preamble embed), config
keys (`summary`, `embed_context`) consumed from RC-01's seam.
Update `router.toml.example`, README (chunks section + IR contract
note), glossary (Chunk / Chunks renderer entries — replace the old
description). API surface unchanged: `render_chunks` still returns
JSONL text; `write_outputs` untouched. Run the full suite: fast tier
green, middle gate green, slow tier green (RC-07's bar).

Blocked by: [TKT-RC-07](TKT-RC-07-quality-bar.md).

## Resolution

Done 2026-09-14. Rebuild landed as one file
(`src/pdf_router/renderers/chunks.py`, 563 lines, flat functions —
no split needed); API surface unchanged (`render_chunks` still returns
JSONL; `write_outputs` untouched).

- **Renderer**: envelope v2 emitter, geometry walk-back
  (`_reading_order` + `_HeadingStack`), table pipeline
  (`_looks_like_caption_row` → `_column_keys`/`_kv_lines` →
  `_facts_summary` → greedy `_table_parts` split), text pipeline
  (`_pipe_regions`/`_strip_tables` subtraction → `_split_blocks` →
  preamble embed with budget), `summary`/`embed_context` consumed
  from the RC-01 seam. Token metric stays `len//4`.
- **Docs**: README «RAG chunks (envelope v2)» section + project
  layout; glossary gained «Chunk (RAG envelope)», Renderer entry
  re-pointed, Markdown entry states the text-half doctrine;
  `router.toml.example` `[renderers.chunks]` documented (RC-01).
- **Full suite**: quality bar green (20/20, see RC-07); full default
  run `-m "not slow"` green — 216 passed, 26 deselected (the slow
  tier of other maps' suites); chunks slow tier run within
  `tests/test_chunks_rag.py` per the repo discipline.
