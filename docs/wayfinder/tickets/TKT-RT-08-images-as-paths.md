# TKT-RT-08 — Images as PNG paths in sidecar folder, not base64

## Question

How are extracted images represented in the IR? Embedded base64, byte arrays, or paths to disk files?

## Resolution

Paths to PNG files in a sidecar folder: `cfg.renderers.output_dir / "images" / "page{N}_{kind}_{idx}.png"`. The `PageResult` gets a new field `images: list[str]` of relative paths (relative to `cfg.renderers.output_dir`, so the output directory remains movable).

`base64` was rejected because it bloats the IR JSON by ~3x and forces every downstream renderer to decode before display. Byte arrays in IR were rejected because they don't survive JSON serialization cleanly.

Sidecar layout is documented in the v0.3 routing ADR; exact path format (`page{N}_photo_{idx}.png` vs `page{N:04d}_{kind}_{idx:02d}.png`) is a small ticket of its own — see "Not yet specified" in the map.