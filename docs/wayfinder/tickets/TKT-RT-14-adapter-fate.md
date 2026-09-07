# TKT-RT-14 — Fate of `AdapterPdfInspector` after `extract_ir()`

## Question

TKT-RT-05 replaces `AdapterPdfInspector().process(path)` with a top-level `extract_ir(path)`. Once that lands, the class is empty shell. Should we delete it entirely, keep a thin compat shim, or repurpose the class?

## Resolution

**Variant B: keep `AdapterPdfInspector` as a thin compat shim until RT-05 closes, then delete it in the same commit as RT-05 lands.**

Grep result (`grep -rn "AdapterPdfInspector" --include="*.py" .`): 7 references across 4 files.
- `tests/test_ir.py:78` — 1 ref
- `tests/test_p5.py:3,8,12` — 3 refs
- `tests/test_ir_structural_blocks.py:21,29,69` — 3 refs
- `src/pdf_router/core.py:74` — 1 ref (hot path, rewritten in RT-05)

Test refs are real and worth updating; `core.py:74` is hot path that gets rewritten in RT-05 anyway. Doing the deletion as a separate ticket would mean a transient "the class still exists but tests pass" state with no value. Merging into RT-05 is cheaper.

**Action plan:**
1. **Today:** leave `AdapterPdfInspector` as-is. No code changes for this ticket alone.
2. **In RT-05 commit:** replace `AdapterPdfInspector().process(path)` with `extract_ir(path)` in all 7 call sites; delete the class; remove `ADAPTER_CLASSES["pdf_inspector"]` entry.

**RT-14 is now resolved (decision recorded).** Implementation is folded into RT-05.

## Status: DONE (2026-09-06)

Variant **A (delete)** chosen after grep showed only test refs. Compat shim was removed; tests updated to use `extract_ir()` directly.

**Changes:**
- `src/pdf_router/adapters/pdf_inspector.py` — `class AdapterPdfInspector` deleted. Module now has only top-level `extract_ir()`. `ClassVar` import removed.
- `src/pdf_router/core.py:15` — comment updated (shim removed).
- `tests/test_p5.py` — 3 refs updated: `extract_ir()` + `try: import pdf_inspector` for availability.
- `tests/test_ir.py:76-78` — `extract_ir()`.
- `tests/test_ir_structural_blocks.py:21,29,69` — `extract_ir()`.

**Tests:** 58/58 unit green (3 slow deselected — require .runtime binaries).

## Approach

Three candidates, each with concrete trade-offs.

### A. Delete entirely

- Remove `class AdapterPdfInspector` from `adapters/pdf_inspector.py`.
- Delete `ADAPTER_CLASSES["pdf_inspector"]` entry in `core.py:41-46` (already going away under TKT-RT-05 anyway).
- Search the codebase for any imports/references; if found, update them.

- **Pro:** no dead code; one less thing to maintain.
- **Con:** if downstream code or tests import `AdapterPdfInspector` directly, they break. Mitigation: search first.

### B. Keep as compat shim

```python
class AdapterPdfInspector:
    name = "pdf_inspector"
    def available(self): return True
    def process(self, path): return extract_ir(path)
```

- **Pro:** no external breakage; transition period for downstream consumers.
- **Con:** dead weight; future maintainers wonder why it exists.

### C. Repurpose as `pdf_inspector` namespace

Convert the file from "one class + helpers" to "module with top-level functions only". Drop the class. Same as A but framed as a module-level cleanup rather than class removal.

- **Pro:** same end state as A; explanation is "modules, not classes" which matches TKT-RT-05's framing.
- **Con:** same external-breakage risk as A.

## Recommendation

**A (delete)**, conditional on a grep confirming no external references.

The codebase is small (29 .py files per `extensions` in system info). Search for:

- `AdapterPdfInspector` (class name)
- `from pdf_router.adapters.pdf_inspector import` (import sites)
- `ADAPTER_CLASSES` (the dict that references the class)

If grep is clean: delete. If something imports the class directly, switch to **B** (compat shim) and add a deprecation comment.

## Notes

- This is a **task** ticket (HITL or AFK). The decision is mechanical once the recommendation is confirmed.
- All four OCR adapter classes (`TesseractAdapter`, `GlmOcrAdapter`, `DeepseekOcrAdapter`, `AdapterPdfInspectorOCR`) follow the same fate under TKT-RT-05 — but RT-14 only covers `AdapterPdfInspector` since the others are being replaced by top-level functions, not just emptied.
- Test files are the most likely place to find a reference; check `tests/` first.

## Blocking

None — takeable now. Search-and-decide can run today.