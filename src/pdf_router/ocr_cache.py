"""Content-addressed OCR cache (ADR-0007).

A tiny sqlite-backed cache around :func:`pdf_router.ocr.ocr_page` that makes
interrupted documents cheap to re-run: the cache key is
``(content_md5, page_idx, engine, params_hash)`` — fully determined by the
file content and the OCR configuration — so a re-run of the same document
with the same settings hits the cache for every page that already completed,
and only the remaining pages pay the OCR cost.

Design invariants (ADR-0007):

- The cache stores ``(markdown, blocks)``; blocks are serialized with the
  same ``kind``-tagged scheme the IR uses for ``ir.json`` (see
  :func:`pdf_router.ir.to_dict`), so a restored page is byte-equivalent to
  a freshly OCR'd one.
- Thread safety: pages are processed inside a ``ThreadPoolExecutor``
  (``core.py``), so every DB touch goes through a :class:`threading.Lock`
  and short-lived connections (sqlite default ``check_same_thread``
  semantics are bypassed per-connection, one connection per call).
- Lifecycle: the caller (embedding application) purges a document's rows
  after its successful commit — the cache never grows beyond the set of
  in-flight documents. No TTL/LRU: identical key ⇒ identical result.
- Failure mode: on any cache error the caller falls back to a live
  ``ocr_page`` call — the cache can only accelerate, never break the run.

Example::

    from pdf_router.ocr_cache import OcrCache, params_fingerprint

    cache = OcrCache("/path/to/ocr_cache.db")
    ir, report = process_pdf(
        "scan.pdf",
        config=cfg,
        ocr_cache=cache,
        content_md5=known_md5,
    )
    cache.purge_document(known_md5)   # after the document's successful commit
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from pdf_router.config import RouterConfig

__all__ = ["OcrCache", "params_fingerprint"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ocr_cache (
    content_md5  TEXT NOT NULL,
    page_idx     INTEGER NOT NULL,
    engine       TEXT NOT NULL,
    params_hash  TEXT NOT NULL,
    markdown     TEXT NOT NULL,
    blocks_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (content_md5, page_idx, engine, params_hash)
)
"""


def _md5_of(path: str | Path) -> str:
    """Compute the md5 digest of a file (streamed, 1 MiB chunks)."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def params_fingerprint(config: RouterConfig, engine: str) -> str:
    """Stable hash of the OCR parameters that affect a page's result.

    Covers everything :func:`pdf_router.ocr.ocr_page` reads from the
    config: the engine's ``[ocr.<engine>]`` params dict **and** the
    routing language (rapidocr derives its recognition language from it,
    see ocr.py:190). Any config change that could alter OCR output
    necessarily changes one of these two inputs.
    """
    payload = {
        "engine": engine,
        "language": config.routing.language,
        "params": config.ocr_engines.get(engine, {}),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class OcrCache:
    """sqlite-backed content-addressed OCR cache (thread-safe).

    All methods swallow nothing: callers treat exceptions as "cache
    unavailable" and fall back to a live OCR call. ``get``/``put`` are
    safe to call concurrently from worker threads.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        # Create parent dirs and schema up front so the first put/get
        # never races the schema creation between threads.
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # NB: sqlite3's connection context manager commits but does NOT
        # close — close explicitly so __init__ leaks no descriptor.
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    # -- key ----------------------------------------------------------------

    @staticmethod
    def key(content_md5: str, page_idx: int, engine: str, params_hash: str) -> tuple:
        return (content_md5, page_idx, engine, params_hash)

    # -- core operations ----------------------------------------------------

    def get(
        self,
        content_md5: str,
        page_idx: int,
        engine: str,
        params_hash: str,
    ) -> tuple[str, list] | None:
        """Return ``(markdown, blocks)`` on a hit, ``None`` on a miss.

        Blocks are revived into the IR 0.5 dataclasses via the same
        ``kind``-tagged scheme :func:`pdf_router.ir.from_dict` uses, so
        the restored page is indistinguishable from a fresh OCR result.
        A corrupted row (undecodable JSON, unknown block kind) is deleted
        and reported as a miss — self-healing, not fatal.
        """
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT markdown, blocks_json FROM ocr_cache"
                    " WHERE content_md5=? AND page_idx=? AND engine=? AND params_hash=?",
                    (content_md5, page_idx, engine, params_hash),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        markdown, blocks_json = row
        try:
            blocks = [_revive_block(d) for d in json.loads(blocks_json)]
            return markdown, blocks
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        "DELETE FROM ocr_cache"
                        " WHERE content_md5=? AND page_idx=? AND engine=? AND params_hash=?",
                        (content_md5, page_idx, engine, params_hash),
                    )
                    conn.commit()
                finally:
                    conn.close()
            return None

    def put(
        self,
        content_md5: str,
        page_idx: int,
        engine: str,
        params_hash: str,
        markdown: str,
        blocks: list,
    ) -> None:
        """Store one page's OCR result (INSERT OR REPLACE — idempotent)."""
        blocks_json = json.dumps(
            [_block_to_dict(b) for b in blocks], ensure_ascii=False
        )
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO ocr_cache"
                    " (content_md5, page_idx, engine, params_hash, markdown, blocks_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (content_md5, page_idx, engine, params_hash, markdown, blocks_json),
                )
                conn.commit()
            finally:
                conn.close()

    def purge_document(self, content_md5: str) -> int:
        """Delete every cached page of a document. Returns rows removed.

        Called by the embedding application after the document's
        successful commit — the cache then holds only in-flight documents.
        """
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM ocr_cache WHERE content_md5=?", (content_md5,)
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()


# -- block serialization (mirrors pdf_router.ir.to_dict/from_dict) ----------


def _block_to_dict(block: Any) -> dict[str, Any]:
    """``kind``-tagged plain-dict form of one IR block (as ``to_dict`` writes)."""
    from dataclasses import asdict

    d: dict[str, Any] = {"kind": type(block).__name__}
    d.update(asdict(block))
    return d


def _revive_block(d: dict[str, Any]) -> Any:
    """Rebuild one IR block from its ``kind``-tagged dict (as ``from_dict`` does)."""
    from pdf_router import ir as ir_mod

    kind = ir_mod._block_kind(d)
    bbox = ir_mod._bbox_from(d.get("bbox"))
    if kind == "HeadingBlock":
        return ir_mod.HeadingBlock(
            level=int(d["level"]),
            text=str(d.get("text", "")),
            bbox=bbox,
            source=str(d.get("source", "structure_tree")),
        )
    if kind == "TableBlock":
        return ir_mod.TableBlock(
            markdown=str(d.get("markdown", "")),
            rows=[list(r) for r in d.get("rows", [])],
            header_rows=int(d.get("header_rows", 0)),
            bbox=bbox,
            source=str(d.get("source", "markdown_pipe")),
        )
    if kind == "FigureBlock":
        return ir_mod.FigureBlock(
            bbox=bbox,
            caption=d.get("caption"),
            image_index=d.get("image_index"),
        )
    if kind == "FormulaBlock":
        return ir_mod.FormulaBlock(
            latex=d.get("latex"),
            bbox=bbox,
            source=str(d.get("source", "none")),
        )
    raise ValueError(f"unknown block kind: {kind!r}")
