# TKT-RT-02 — `looks_like_text` per-page, not per-block

## Question

Should `looks_like_text` aggregate over the whole page (per-page) or be applied per-block?

## Resolution

Per-page. Reason: short blocks (captions, single-line headings, table headers) don't accumulate enough alpha-characters to make the threshold meaningful, so per-block triggers false positives. Per-page aggregation across `"\n".join(_block_text(b) for b in page.blocks)` gives stable statistics.

Per-block is deferred to v0.4+.