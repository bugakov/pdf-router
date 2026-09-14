# TKT-ST-06 — ADR-0005: structure for OCR pages

## Question

Write `docs/adr/0005-ocr-structure.md` capturing the accepted approach
(TKT-ST-03) with the locked sub-decisions (TKT-ST-04 IR contract): strategy
and rejected alternatives with reasons, dependency ruling (which third-party
models/packages enter the dependency path, on what terms — pinned, licensed,
CPU-only), IR bump, implementation checklist.

Precedent: ADR-0004 shape (strategy C write-up); ADR-0002 rules DocLang out
as IR/renderer — cite, don't reopen.

Blocked by: [TKT-ST-03](TKT-ST-03-structure-approach.md),
[TKT-ST-04](TKT-ST-04-ir-contract-ocr-blocks.md) (quality bar may still be
in flight — ADR references it, does not need it closed).

## Resolution

**Resolved (2026-09-14).** ADR-0005 written to
`docs/adr/0005-ocr-structure.md` (Accepted, dated 2026-09-14), modeled on
ADR-0004's shape:

- Context carries the fact base with verified file:line seams
  (`config.py:91`, `ocr.py:211-214`, `core.py:236-238`, `ir.py:119`) and
  the TKT-ST-03 live-probe numbers (wired 8 vs lineless 144 cells on
  Рыбы p. 14; 149 vs 94 on Генетические p. 34; +7.3–7.7 s/page).
- Decision records the hybrid pass (approach C) with all eight locked
  sub-decisions: pass shape; `lineless` default (wired/auto as TOML
  values); box-height heading heuristic with `level ∈ {1,2}` and TOML
  ratios; mandatory raster reuse (rasterize-once); IR 0.4 → 0.5 with
  five provenance sources and raster-pixel bbox semantics; cloud stays
  opt-in; dependency ruling (wired-table-rec==1.2.0 +
  lineless-table-rec==0.1.0 exact pins, Apache-2.0, scipy +
  scikit-image transitive, ModelScope auto-download, table_cls NOT yet
  added); quality bar (three tiers, fuzzy invariants, ≤ 15 s/page gate).
- Alternatives: seven recorded as rejected/carried with reasons (plain
  pass, per-engine glue, wired-only v1, local layout model, heavy
  parsers, per-cell confidence, point-scaling).
- Consequences honest: first-run network need, glued twin sub-columns
  weakness, heading jitter across scan provenance, IR bump breaking for
  `to_dict` consumers.
- Implementation checklist names every touched file (structure.py new;
  ocr.py, core.py, ir.py, config.py, pyproject.toml, tests, README).
- ADR-0002 cited (DocLang stays out), not reopened.

No blockers left; implementation = TKT-ST-07.
