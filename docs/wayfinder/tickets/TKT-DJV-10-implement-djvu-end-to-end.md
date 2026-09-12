# TKT-DJV-10 — Implement the djvu path end-to-end + docs

## Question

Land the chosen strategy per ADR-0004:

- the djvu inspector/adapter + `_INSPECTORS` registration (one registry
  entry — the locked seam claim, finally exercised);
- `core.py` wiring for the strategy's passes (B: none needed downstream;
  C: the PageSource seam per its graduated ticket);
- CLI `--info` per TKT-DJV-07; IR bump per TKT-DJV-03/04 if any;
- tests: golden anchors per TKT-DJV-08; the fast suite stays fast;
- docs: README format paragraph, `docs/glossary.md`;
- e2e smoke on the anchors; destination reached (commit pending — the user
  commits via `gaa && forge commit && gp`).

Blocked by: TKT-DJV-09.

## Resolution

_(open)_
