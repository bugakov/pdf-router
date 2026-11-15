# ADR-0012: Rust Workspace Topology, Rayon Pipeline, and Idempotent Streaming

- **Status:** Accepted
- **Date:** 2026-11-15
- **Context:** Implementing Phase 2 of ADR-0011 (the Rust core migration). Defining repository workspace layout, concurrency model, memory bounds, and determinism invariants.
- **Related:**
  - [ADR-0001: IR structural blocks](0001-ir-structural-blocks.md)
  - [ADR-0010: Native zvec target](0010-zvec-renderer.md)
  - [ADR-0011: The Lean Router](0011-lean-router-and-rust-roadmap.md)

---

## 1. Context and Problem Statement

Following [ADR-0011](0011-lean-router-and-rust-roadmap.md), `pdf-router` transitions its core engine to pure Rust. To ensure maintainability, zero runtime overhead, and seamless backward compatibility, three foundational architectural decisions must be pinned:
1. **Packaging & Consumer Topology:** How should the Rust code be organized so that both standalone CLI users and existing Python integrations (`fileholder`, etc.) receive zero-overhead artifacts without unwanted dependencies?
2. **Concurrency & Execution Model:** Should the core be asynchronous (`tokio`) or synchronous (`rayon`)?
3. **Memory Model & Idempotence:** How to support massive multi-thousand-page technical volumes without memory spikes, while guaranteeing bit-for-bit reproducible outputs?

---

## 2. Decisions

### 2.1. Multi-Crate Cargo Workspace
The Rust implementation lives in a multi-crate workspace under `crates/`:

```
pdf-router/
├── Cargo.toml                     # Workspace root
├── crates/
│   ├── pdf-router-core/           # Pure Rust domain logic (no PyO3, no CLI parsing)
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── formats/           # Magic byte detection, OOXML, Office, PDF
│   │       ├── ir/                # IR 0.6 data structures & serialization
│   │       ├── ocr/               # OcrEngine trait & lightweight adapters
│   │       └── renderers/         # md, chunks, zvec, json
│   ├── pdf-router-cli/            # Thin standalone CLI binary (`clap`)
│   │   ├── Cargo.toml
│   │   └── src/main.rs
│   └── pdf-router-py/             # Thin PyO3 extension wheel (maturin)
│       ├── Cargo.toml
│       └── src/lib.rs
└── src/pdf_router/                # Existing Python codebase (retained during migration)
```

**Rationale:**
- Standalone CLI binaries compiled from `pdf-router-cli` do not touch Python headers or runtimes.
- `pdf-router-core` can be embedded into any Rust service directly as a pure library.
- `pdf-router-py` compiles into a drop-in C-extension wheel matching the existing Python `pdf_router` facade.

### 2.2. Synchronous `rayon` Pipeline
`pdf-router-core` is strictly synchronous. Page extraction, OOXML tree walks, and image conversions run over CPU-bound `rayon` parallel iterators:
- No infectious `async/await` state machines in the core IR and adapter APIs.
- External HTTP Cloud OCR (GLM/Novita) uses blocking I/O (`reqwest::blocking`) within worker threads, maintaining clean determinism and simple stack traces.

### 2.3. Idempotent Streaming Pipeline
Processing documents must be **strictly idempotent** and memory-bounded:
1. **Streaming Iterators:** Alongside the full in-memory `Ir`, the core exposes `Document::iter_pages()` and `Document::iter_zvec_docs()`. Pages and records are emitted on-the-fly, keeping memory bounded to O(1) (typically 20–30 MB RSS even for 10,000-page volumes).
2. **Deterministic Identifiers:** Chunk IDs (`{page}-t{n}`, `{page}-{seq:03d}`), doc IDs (12-hex SHA-256), and image paths are deterministically derived from stable content/path hashes with fixed sort order.
3. **Reproducibility:** Two runs over identical input bytes produce bit-for-bit identical Markdown, JSONL chunks, and zvec vectors.

### 2.4. Incremental Coexistence
`crates/` is developed alongside the existing Python code. Rust unit and integration tests validate parity against the committed golden test fixtures (`tests/fixtures/`). The Python CLI and entry points remain functional until 100% parity is verified.

### 2.5. Fault Tolerance & Per-Page Isolation
Technical documents often contain isolated corrupt scan layers or damaged page objects. The Rust core preserves the per-page error isolation model:
- When a page encounters an error (rasterization failure, table parse error, OCR timeout), the page is marked with `error = Some(message)` and counted in `ProcessingReport.pages_failed`.
- The document run completes successfully with partial results intact, unless `strict: true` (or `--strict`) is specified, in which case the pipeline returns `Err(ProcessingError(report))`.

### 2.6. Zero-Alloc Streaming OOXML Parsers (`quick-xml`)
DOCX and XLSX ingest are implemented with `quick-xml`'s pull-based streaming parser (`Reader::from_reader`) directly over `zip::ZipArchive` streams:
- Large spreadsheet sheets (`sheet1.xml` reaching 50–100 MB in estimate smetas) are processed without loading DOM XML trees into RAM.
- Cell rows and table blocks stream directly into the pipeline with bounded memory.

### 2.7. Pure-by-Default Sidecars & Aspect Ratio Gating
The pipeline is strictly pure in-memory by default:
- No sidecar image files or directories are created on disk unless `output_dir` is explicitly supplied.
- When enabled, sidecar images (`<out>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.png`) follow the calibrated size and aspect-ratio gates (filtering out thumbnails, separator lines, and background scans).

### 2.8. Configuration Parity (CLI > TOML > Defaults)
Configuration is deserialized via `serde` + `toml` with exact schema parity with `router.toml` (`[router]`, `[routing]`, `[structure]`, `[ocr.*]`, `[renderers.*]`).
- Precedence is strictly enforced: `CLI flags > router.toml > built-in defaults`.

---

## 3. Consequences

- **Positive:**
  - Standalone binary size: ~15–20 MB.
  - Startup latency: <2 ms.
  - Constant memory consumption for arbitrary document sizes.
  - Guaranteed reproducibility for downstream RAG vector stores.
  - Zero disruption to production consumers during the development phase.
- **Negative:**
  - Maintaining workspace build configurations (`Cargo.toml` + `pyproject.toml` via `maturin`).
