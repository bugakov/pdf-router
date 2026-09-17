# ADR-0007: OCR cache — content-addressed page memoization

- **Status:** Accepted (implemented in 0.6.0, E2E-verified on real rapidocr;
  corpus-level verification pending — vbrdb wayfinder #23)
- **Date:** 2026-09-16
- **Context:** vbrdb wayfinder map `bugakov/vbrdb#24` (tickets #18–#21);
  consumer: vbrdb parse worker (`orchestrator/parse_worker.py`)
- **Decided via:** grilling session 2026-09-16 (vbrdb #18)

## Context

The batch consumer (vbrdb parse worker) processes a corpus where the
largest documents are 500+ page scanned journals. With
`DOC_TIMEOUT_S = 9h` a single document can occupy the worker for
hours; a cancellation (manual preemption, type B) or a graceful stop
(SIGTERM on `make stop-parser`) discards all completed page OCR work —
the partial result is deliberately not persisted (ADR-0001 keeps the
commit atomic: chunks in rag.db ⟺ artifacts fully in S3). Re-running
the document repeats the OCR from scratch: hours of compute lost.

The saving property: `ocr_page(pdf_path, page_idx, config)` is a pure,
deterministic function of (file content, page number, engine,
engine params). Its result can therefore be memoized by content —
"resumable download" becomes an ordinary retry whose already-processed
pages are cache hits costing milliseconds.

## Decision

Add an **optional, opt-in content-addressed OCR cache** to pdf-router.

### Cache identity

Key: `(content_md5, page_idx, engine, params_hash)` where

- `content_md5` — caller-supplied digest of the *file content* (the
  worker already has it in `parse_jobs.md5`; pdf-router does not
  currently compute any content digest, only a path-derived doc-id).
  If the caller does not pass a digest, the cache computes md5 of the
  file bytes itself, once per file, lazily;
- `page_idx` — 0-indexed page number;
- `engine` — `config.routing.ocr_fallback_engine`;
- `params_hash` — sha256 of `json.dumps(config.ocr_engines.get(engine),
  sort_keys=True)`; engine params changing invalidates their slice of
  the cache.

Value: `(markdown, blocks)` — exactly the parts of the `ocr_page`
return triple that are not deterministic trivia (engine name is
already part of the key).

### Storage and lifecycle

- **Separate SQLite file**, `ocr_cache.db`, next to the consumer's
  other state — NOT inside rag.db: the cache and the search index
  live different lives (vbrdb #16). The cache is always safe to
  delete wholesale; worst case is re-computation.
- WAL mode; single writer (the worker process) — no contention
  concerns.
- Schema:

```sql
CREATE TABLE IF NOT EXISTS ocr_cache (
    content_md5  TEXT NOT NULL,
    page_idx     INTEGER NOT NULL,
    engine       TEXT NOT NULL,
    params_hash  TEXT NOT NULL,
    markdown     TEXT NOT NULL,
    blocks_json  TEXT NOT NULL,   -- IR 0.5 structural blocks, JSON
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (content_md5, page_idx, engine, params_hash)
);
```

- **Eviction by consumption:** after a document commits successfully
  (chunks in rag.db, artifacts in S3), the worker deletes its cache
  rows. The cache therefore stays small — it holds only the unfinished
  tail, never a duplicate of the corpus. Rows lost on mid-page crash:
  exactly one page.
- No TTL, no LRU: content-addressed entries cannot go stale (same
  key ⇒ same result, deterministically).

### API (pdf-router)

New module `src/pdf_router/ocr_cache.py`:

```python
class OcrCache:
    def __init__(self, path: str | Path) -> None: ...
    def get(self, content_md5: str, page_idx: int,
            engine: str, params_hash: str) -> tuple[str, list] | None: ...
    def put(self, content_md5: str, page_idx: int,
            engine: str, params_hash: str,
            markdown: str, blocks: list) -> None: ...
    def purge_document(self, content_md5: str) -> None: ...
```

Plus `params_fingerprint(config: RouterConfig, engine: str) -> str`
(the `params_hash` helper, exported at package level).

### Injection point

`process_pdf(..., ocr_cache: OcrCache | None = None,
content_md5: str | None = None)` — two new keyword-only params,
both optional, default `None` ⇒ behaviour identical to today
(no cache, zero overhead, existing tests untouched).

The cache read/write happens in `_process_text_for_page` around the
existing `ocr_page` call (core.py): on OCR entry — `get()` first
(hit ⇒ skip rasterization+OCR entirely, reconstruct page state);
on success — `put()`. Native-text pages never touch the cache: they
are cheap and deterministic without it.

### Serialization

IR 0.5 structural blocks are plain data (dicts/lists of primitives);
`blocks_json = json.dumps(blocks)`. If a future block type grows
non-JSON-serializable state, the conversion helpers live next to the
cache module — the cache contract is JSON, not Python objects.

### What is deliberately NOT done

| Rejected | Why |
|---|---|
| Partial chunk commits to rag.db | Breaks ADR-0001 atomicity invariant and the S3 artifact home layout |
| Full per-page IR resume inside the pipeline | Deep surgery: partial-state format, versioning, tests — an order of magnitude harder for the same savings; the cache achieves the same with a clean seam |
| Splitting large documents into 100-page sub-jobs | Chunks/IR/artifacts are per-document; stitching sub-results is harder than the cache and changes the S3 layout |

## Consequences

- **Positive:** cancellation/preemption cost drops from hours of OCR
  to minutes (re-run walks cache hits); graceful stop loses at most
  one in-flight page; force kill after the grace period loses one
  unwritten page (SQLite commits per row);
- **Neutral:** no behaviour change unless the caller opts in;
  existing signature and tests unchanged;
- **Risks / trade-offs:**
  - cache grows with unfinished work only (purged after commit) —
    bounded by document size, ~MBs per document;
  - `content_md5` trust: the caller may pass a wrong digest — the
    cache then serves stale-looking results for that key; mitigated
    by the worker always taking md5 from the registry (verified at
    download);
  - engine params changes silently invalidate cache slices — this is
    correct behaviour (params are part of the key), but means a
    config tweak forfeits the tail of cached work; documented so it
    surprises no one.

## Verification

vbrdb wayfinder ticket #23: interrupt a large document mid-OCR,
restart, measure that re-completion takes minutes (cache hits) and
the numbers land in the ADR-0001 addendum.
