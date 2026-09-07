# ADR-0002: DocLang is not IR (yet)

- **Status:** Accepted
- **Date:** 2026-09-03
- **Supersedes:** the original 22-question grilling session's discussion of
  DocLang as a possible IR format

## Context

A question was raised early in the IR 0.2 design discussion: "Why hold a
second intermediate layer when DocLang positions itself as the standard for
AI-native document markup?"

DocLang (LF AI & Data Foundation, v0.7.x at the time of this writing,
sponsored by IBM / ABBYY / RedHat / NVIDIA / HumanSignal) is a strong
candidate for the **published-document format** of pdf-router. It is
*not* a candidate for the **in-process contract between Python adapters
and renderers**, for the reasons below.

## Decision

DocLang will be considered as a **renderer** in a future version, not as
the IR. The IR remains a Python dataclass.

## Reasons

1. **Layering.** IR lives between Python modules (adapters ↔ renderers
   within one process). DocLang is an XML document for **publication and
   exchange**. Replacing a Python dataclass with a serialise/parse-XML
   contract would force every adapter to emit well-formed XML on every
   call, and every renderer to parse it back. The reference toolkit
   (v0.7.x) is a validator + packer, **not a builder API**; generating
   `.dclg` requires `lxml.Element` calls in user code.
2. **Field coverage.** DocLang's `<head>` reserved metadata (title,
   `generated_by`, `page_size`, governance) does not include
   `pages_needing_ocr`, `error`, `src_tool`, `confidence`, or per-page
   timing. These would go into `<custom>` with our own namespaced prefix
   — at which point the IR is no longer "DocLang" but "DocLang +
   our extension", and we've added a layer instead of removing one.
3. **Markdown → DocLang is lossy without a semantic parser.** Adapters
   emit `markdown: str` (pdf-inspector's native markdown for text-path).
   Mapping that into DocLang's `<heading>`, OTSL `<table>`, `<formula>`,
   `<picture>` requires a **semantic parser we don't have** and that
   pdf-inspector 1.17 doesn't emit. The cheap alternative — wrap the
   markdown in `<text><content><![CDATA[…]]></content></text>` — is
   valid DocLang but loses the structural benefit and adds a wrapper.
4. **Stability.** DocLang is 0.x. Spec rule: "each minor version
   increment in the 0.x series represents a breaking change." Tying an
   internal contract to a 0.x format is a non-determinism risk.
5. **Cost.** Reference toolkit pulls `lxml>=4.8.0`, `typer>=0.15.1`,
   optional `saxonche>=12.9.0`. The current stack is intentionally
   minimal (`pdf-inspector` + `pymupdf`; see `pyproject.toml:7-10`).
   Adding a non-trivial XML toolchain for an internal layer outweighs
   the benefit until the public format is actually shipped.

## When to revisit

DocLang becomes a candidate for IR when **all** of the following are true:

- pdf-inspector (Rust) exposes a first-class Python binding for
  per-cell table extraction (today: only in the Rust crate, not the
  Python wheel).
- A semantic markdown → DocLang parser exists (or pdf-inspector emits
  DocLang-shaped data directly).
- We have a real consumer for the public document format (a downstream
  tool that ingests `.dclg` rather than markdown/JSONL).

Until then, DocLang as a **renderer** is the right shape: emit
`<base>.dclg` from the existing IR, with a round-trip test against
`doclang validate`.

## References

- DocLang spec v0.7, `<head>` semantics, OTSL table markup, `<custom>`
  reserved-by-organisation extension rule.
- pdf-inspector 1.17 Python bindings (audited 2026-09-03) — see
  [ADR-0001](0001-ir-structural-blocks.md) for the upstream audit.
