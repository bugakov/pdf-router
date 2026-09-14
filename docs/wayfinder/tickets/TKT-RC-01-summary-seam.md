# TKT-RC-01 — Summary seam + legacy contract removal

## Question

Two preparatory moves in one ticket:

1. **Summary seam.** `[renderers.chunks]` grows `summary = "facts" |
   "none"` (default `facts`) and `embed_context = true | false`
   (default `true`). Extend `ChunksConfig` + `_KNOWN_CHUNKS_KEYS` +
   `router.toml.example` + config tests. Only the config surface —
   the renderer consumes it in RC-04/05.
2. **Legacy sweep.** Delete the old flat-text chunks contract:
   `tests/test_renderers.py` chunks tests (keep md/json tests — move
   them to `test_renderers_md_json.py` or keep the file and prune),
   stale `section`-key assumptions anywhere else (grep `chunks`
   across tests/docs). Verify no consumer outside the repo assumes
   the old key-set (grep README/docs). The renderer itself may
   briefly emit the new shape before RC-04/05 land — mark the
   intermediate state with a `# WIP RC` note if the suite needs it,
   but prefer landing 01→08 without red intermediates (ticket order
   is the build order).

Blocked by: nothing — frontier.

## Resolution

Done 2026-09-14. Both moves landed clean, no red intermediates:

- **Seam**: `ChunksConfig` grows `summary: Literal["facts", "none"]`
  (default `facts`; `"glm"` parses but `ConfigError`s — reserved until
  implemented) and `embed_context: bool` (default `true`);
  `_KNOWN_CHUNKS_KEYS` extended; `router.toml.example` documents the
  section; config tests pin defaults + glm rejection + unknown-key
  rejection.
- **Legacy sweep**: old flat-text chunks tests deleted from
  `tests/test_renderers.py` (md/json tests stay); contract pins in
  `test_p5_e2e.py`, `test_cli_smoke.py`, `test_api.py`, `test_docx.py`
  re-pinned to the envelope v2 (`chunk_version`, `type`, `embed`,
  metadata keys). Grep confirmed no other consumer of the old key-set.
