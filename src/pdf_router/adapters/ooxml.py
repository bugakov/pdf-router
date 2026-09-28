"""Shared OOXML package plumbing — docx and xlsx are the same container shape.

Both formats are a ZIP of XML parts wired together by ``.rels`` files and
keyed by ``[Content_Types].xml``; both carry a title in ``docProps/core.xml``.
The docx adapter (ADR-0003) grew this plumbing first; the xlsx adapter
(ADR-0008) would have copied it verbatim, so it lives here: one tolerant
reader, one rels resolver, one title extractor, one pipe-table renderer.
Nothing format-specific — the per-format semantics stay in the adapters.

Flat functions by design (RT-14): no classes, no state.
"""

from __future__ import annotations

import posixpath
import zipfile
from xml.etree import ElementTree as ET

_DC = "http://purl.org/dc/elements/1.1/"


def read_xml(zf: zipfile.ZipFile, part: str) -> ET.Element | None:
    """Parse one package part; None when absent or unreadable XML."""
    try:
        return ET.fromstring(zf.read(part))
    except (KeyError, ET.ParseError):
        return None


def load_rels(zf: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relationship map for one package part: rId -> internal target.

    ``part`` is like ``word/document.xml``; its rels live at
    ``word/_rels/document.xml.rels``. External targets (hyperlinks) and
    missing rels files are skipped.
    """
    base, name = part.rsplit("/", 1)
    rels_path = f"{base}/_rels/{name}.rels"
    root = read_xml(zf, rels_path)
    if root is None:
        return {}
    out: dict[str, str] = {}
    for rel in root:
        rid = rel.get("Id")
        target = rel.get("Target")
        if not rid or not target or rel.get("TargetMode") == "External":
            continue
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(f"{base}/{target}")
        out[rid] = resolved
    return out


def core_title(zf: zipfile.ZipFile) -> str | None:
    """``dc:title`` from docProps/core.xml (honest None when absent)."""
    root = read_xml(zf, "docProps/core.xml")
    if root is None:
        return None
    title = root.find(f"{{{_DC}}}title")
    if title is not None and title.text and title.text.strip():
        return title.text.strip()
    return None


def rows_to_markdown(rows: list[list[str]]) -> str:
    """Pipe-table markdown from exact rows (header row + separator)."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def esc(s: str) -> str:
        return s.replace("|", "\\|").strip()

    def line(cells: list[str]) -> str:
        padded = cells + [""] * (width - len(cells))
        return "| " + " | ".join(esc(c) for c in padded) + " |"

    lines = [line(rows[0]), "|" + "|".join([" --- "] * width) + "|"]
    lines.extend(line(r) for r in rows[1:])
    return "\n".join(lines)
