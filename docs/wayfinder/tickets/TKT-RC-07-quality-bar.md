# TKT-RC-07 — Quality bar: tiers + anchor verification

## Question

Lock and run the quality bar (decided): fast tier always-on;
middle gate on the committed Рыбы p.14 crop; slow tier on the v0.6
anchors. Delete the legacy chunks tests in the same change
(TKT-RC-01's sweep may already have — coordinate; this ticket owns the
final state: no legacy chunks tests anywhere).

- **Fast (synthetic IR, deterministic):** envelope key-set/type checks;
  caption demotion; walk-back across a page boundary; split parts
  carry duplicated header + `table_id` + part numbering; KV form of
  table embed; pipe subtraction (exact-match native, any-pipe OCR,
  no-block degradation); `summary=facts|none`; `embed_context` on/off;
  doc_id from source_path; empty-markdown table page → no empty text
  chunk; determinism (same IR → byte-identical output).
- **Middle (live lineless on the crop fixture):** header present as KV
  pairs in embed; caption in context; `markdown` is a valid pipe
  table; parts ≤ budget; provenance tags ride through.
- **Slow (marked slow; full pages Рыбы p.14 + Генетические p.34 via
  the real OCR+structure path; native journal pair without OCR):**
  manual spot-check list in the ticket + a smoke assertion that
  rendering completes and every chunk passes the fast-tier invariants
  (a shared validator function over the emitted JSONL).

Blocked by: [TKT-RC-06](TKT-RC-06-adr-0006-rag-chunks.md).

## Resolution

Done 2026-09-14 — `tests/test_chunks_rag.py`, three tiers, shared
validator (`_validate`) gating every emitted line in every tier:
envelope/metadata/table key-sets, unique chunk ids, part/parts
consistency, embed non-empty, budget respected.

- **Fast (20 total in the file incl. middle/slow, all deterministic
  ones in the default suite):** envelope keys; text + table basics;
  caption demotion (single-cell AND prefix-in-cell variants, «№»
  non-demotion); walk-back across a page boundary + section fallback
  chain; row-split (header band duplicated, table_id shared,
  part/parts numbering, rows atomic); KV form (body cell reaches
  embed); subtraction native-exact / OCR-any-pipe / no-block
  degradation; summary facts/none + glm rejected; embed_context
  on/off (budget interplay); doc_id from source_path; per-page table
  ids; pure-table page → no empty text chunk; byte-identical
  determinism.
- **Middle (slow):** the committed crop
  `tests/fixtures/structure/ryby_p14_table.png` through the live
  lineless engine — header as KV keys, caption in context, valid pipe
  markdown, budget held, provenance through.
- **Slow:** Рыбы p.14 and Генетика p.34 through real OCR+structure
  (fuzzy asserts per the ST-05 bar: caption line, header word, first
  meaningful body cell found verbatim in the embed — the body-cell pin
  survives the live grid's spacer row); native journal through
  `process_pdf` (native pages ≥ total−1 — one photo page may route to
  OCR — and validator-clean output).
- Full run: 20 passed (`uv run pytest tests/test_chunks_rag.py`,
  fast+middle+slow in one suite).
