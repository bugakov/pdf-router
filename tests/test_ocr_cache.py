"""Tests for the OCR cache (ADR-0007).

Unit tests cover OcrCache CRUD + params_fingerprint + block round-trip.
Integration tests use the committed p27.pdf fixture (1 page, image-based)
and verify cache hits through _process_text_for_page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_router import OcrCache, params_fingerprint
from pdf_router import ir
from pdf_router import load_config
from pdf_router.core import _LazyDigest, _process_text_for_page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _heading(text: str = "Кэшированный заголовок") -> ir.HeadingBlock:
    return ir.HeadingBlock(
        level=1,
        text=text,
        bbox=ir.BBox(x0=10.0, y0=20.0, x1=100.0, y1=30.0),
        source="rapidocr",
    )


def _ocr_output(page: int = 5, engine: str = "rapidocr") -> tuple[str, list]:
    """Markdown + blocks exactly as a live ocr_page() returns them."""
    markdown = f"# {engine} text\n\nтекст со страницы {page}"
    return markdown, [_heading()]


def _fake_page_result(page: int = 5, engine: str = "rapidocr") -> ir.PageResult:
    """A PageResult imitating a successful OCR of a scanned page."""
    markdown, blocks = _ocr_output(page, engine)
    return ir.PageResult(
        page=page,
        markdown=markdown,
        blocks=blocks,
        images=[],
        needs_ocr=True,
        ocr_reason="low_text_quality",
        src_tool=engine,
        error=None,
    )


# ---------------------------------------------------------------------------
# Unit: OcrCache CRUD
# ---------------------------------------------------------------------------


class TestOcrCacheCrud:
    def test_put_then_get_roundtrip(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache.db")
        pr = _fake_page_result()

        cache.put("md5-1", 5, "rapidocr", "fp1", pr.markdown, pr.blocks)
        got = cache.get("md5-1", 5, "rapidocr", "fp1")

        assert got is not None
        markdown, blocks = got
        assert markdown == pr.markdown
        assert len(blocks) == 1
        assert isinstance(blocks[0], ir.HeadingBlock)
        assert blocks[0].text == pr.blocks[0].text

    def test_get_miss_returns_none(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache.db")
        assert cache.get("md5-x", 1, "rapidocr", "fp") is None

    def test_different_keys_isolated(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache.db")
        md, blocks = _ocr_output()
        cache.put("md5-1", 5, "rapidocr", "fp", md, blocks)

        # Different content md5 -> miss
        assert cache.get("md5-2", 5, "rapidocr", "fp") is None
        # Different page -> miss
        assert cache.get("md5-1", 6, "rapidocr", "fp") is None
        # Different engine -> miss
        assert cache.get("md5-1", 5, "glm_ocr", "fp") is None
        # Different fingerprint -> miss
        assert cache.get("md5-1", 5, "rapidocr", "fp-other") is None

    def test_put_overwrites_same_key(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache.db")
        old = _fake_page_result()

        cache.put("md5", 5, "rapidocr", "fp", old.markdown, old.blocks)
        cache.put("md5", 5, "rapidocr", "fp", "новый текст", [_heading("новый")])

        got = cache.get("md5", 5, "rapidocr", "fp")
        assert got is not None
        assert got[0] == "новый текст"

    def test_purge_document(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache.db")
        cache.put("md5-a", 1, "rapidocr", "fp", *_ocr_output(1))
        cache.put("md5-a", 2, "rapidocr", "fp", *_ocr_output(2))
        cache.put("md5-b", 1, "rapidocr", "fp", *_ocr_output(1))

        removed = cache.purge_document("md5-a")
        assert removed == 2

        assert cache.get("md5-a", 1, "rapidocr", "fp") is None
        assert cache.get("md5-a", 2, "rapidocr", "fp") is None
        # Other document untouched
        assert cache.get("md5-b", 1, "rapidocr", "fp") is not None

    def test_purge_absent_document_is_noop(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache.db")
        assert cache.purge_document("never-existed") == 0


# ---------------------------------------------------------------------------
# Unit: params_fingerprint
# ---------------------------------------------------------------------------


class TestParamsFingerprint:
    def test_stable_and_hex(self) -> None:
        cfg = load_config(None)
        fp1 = params_fingerprint(cfg, "rapidocr")
        fp2 = params_fingerprint(cfg, "rapidocr")
        assert fp1 == fp2
        assert len(fp1) == 16
        int(fp1, 16)  # hex

    def test_differs_across_engines(self) -> None:
        cfg = load_config(None)
        assert params_fingerprint(cfg, "rapidocr") != params_fingerprint(cfg, "glm_ocr")

    def test_differs_when_engine_params_change(self) -> None:
        cfg = load_config(None)
        fp_before = params_fingerprint(cfg, "rapidocr")

        import copy

        cfg2 = copy.deepcopy(cfg)
        params = cfg2.ocr_engines.get("rapidocr")
        if isinstance(params, dict):
            params = dict(params)
            params["_test_probe"] = "changed"
            cfg2.ocr_engines["rapidocr"] = params
            fp_after = params_fingerprint(cfg2, "rapidocr")
            assert fp_before != fp_after


# ---------------------------------------------------------------------------
# Unit: block round-trip against canonical ir helpers
# ---------------------------------------------------------------------------


class TestBlockRoundTrip:
    def test_all_block_kinds_roundtrip(self, tmp_path: Path) -> None:
        blocks = [
            ir.HeadingBlock(
                level=2,
                text="Заголовок",
                bbox=ir.BBox(0.0, 0.0, 10.0, 10.0),
                source="rapidocr",
            ),
            ir.TableBlock(
                markdown="| a | b |",
                rows=[["a", "b"]],
                header_rows=1,
                bbox=ir.BBox(0.0, 0.0, 20.0, 20.0),
                source="rapidocr",
            ),
            ir.FigureBlock(
                caption="Рис. 1",
                image_index=0,
                bbox=ir.BBox(0.0, 0.0, 30.0, 30.0),
            ),
            ir.FormulaBlock(
                latex="E=mc^2",
                bbox=ir.BBox(0.0, 0.0, 40.0, 40.0),
                source="rapidocr",
            ),
        ]
        pr = ir.PageResult(
            page=1,
            markdown="text",
            blocks=blocks,
            images=[],
            needs_ocr=True,
            ocr_reason="low_text_quality",
            src_tool="rapidocr",
            error=None,
        )

        cache = OcrCache(tmp_path / "cache.db")
        cache.put("m", 1, "rapidocr", "f", pr.markdown, pr.blocks)
        got = cache.get("m", 1, "rapidocr", "f")

        assert got is not None
        _, revived = got
        assert len(revived) == 4
        for original, back in zip(pr.blocks, revived, strict=True):
            assert type(back) is type(original)
            assert original == back

    def test_none_bbox_survives(self, tmp_path: Path) -> None:
        pr = ir.PageResult(
            page=1,
            markdown="t",
            blocks=[
                ir.HeadingBlock(level=1, text="x", bbox=None, source="rapidocr"),
            ],
            images=[],
            needs_ocr=True,
            ocr_reason="low_text_quality",
            src_tool="rapidocr",
            error=None,
        )

        cache = OcrCache(tmp_path / "cache.db")
        cache.put("m", 1, "rapidocr", "f", pr.markdown, pr.blocks)
        got = cache.get("m", 1, "rapidocr", "f")

        assert got is not None
        _, revived = got
        assert revived[0].bbox is None

    def test_db_schema_is_content_addressed(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache.db")
        cache.put("m", 7, "rapidocr", "f", *_ocr_output(7))

        import sqlite3

        conn = sqlite3.connect(tmp_path / "cache.db")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ocr_cache)")]
        conn.close()
        # The key parts live as columns per ADR-0007.
        for expected in ("content_md5", "page_idx", "engine", "params_hash"):
            assert expected in cols


# ---------------------------------------------------------------------------
# Integration: _process_text_for_page with cache
# ---------------------------------------------------------------------------


def _pipeline_page(page: int = 0, needs_ocr: bool = True) -> ir.PageResult:
    """The page object the text pass operates on (0-based page.page, as built by pdf_inspector)."""
    return ir.PageResult(
        page=page,
        markdown="",
        blocks=[],
        images=[],
        needs_ocr=needs_ocr,
        error=None,
    )


class TestProcessTextForPageCache:
    def test_cache_hit_avoids_ocr_and_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, p27_pdf: Path
    ) -> None:
        cfg = load_config(None)
        cache = OcrCache(tmp_path / "cache.db")
        digest = _LazyDigest(p27_pdf, None)
        md5 = digest.get()
        assert md5

        # Seed the cache through the public API exactly as a live run would:
        # page.page is 0-based in the pipeline and goes into the key verbatim.
        seed_md, seed_blocks = _ocr_output(0)
        cache.put(
            md5,
            0,
            "rapidocr",
            params_fingerprint(cfg, "rapidocr"),
            seed_md,
            seed_blocks,
        )

        ocr_calls: list[int] = []

        def _must_not_run(pdf_path, page_idx, config):  # pragma: no cover
            ocr_calls.append(page_idx)
            md, blocks = _ocr_output(page_idx)
            return md, "rapidocr", blocks

        monkeypatch.setattr("pdf_router.core.ocr_page", _must_not_run)

        page = _pipeline_page()
        hit = _process_text_for_page(
            page, str(p27_pdf), cfg, ocr_cache=cache, digest=digest
        )

        assert hit is True
        assert ocr_calls == []  # OCR was skipped entirely
        assert page.markdown == seed_md
        assert len(page.blocks) == 1
        # Restored page stays indistinguishable from a fresh OCR: needs_ocr
        # remains True, ocr_reason is whatever needs_ocr() computed — never
        # overwritten by the cache.
        assert page.needs_ocr is True
        assert page.ocr_reason != "cache_hit"
        assert page.src_tool == "rapidocr"
        assert page.error is None

    def test_cache_miss_runs_ocr_and_populates_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, p27_pdf: Path
    ) -> None:
        cfg = load_config(None)
        cache = OcrCache(tmp_path / "cache.db")
        digest = _LazyDigest(p27_pdf, None)

        ocr_calls: list[int] = []

        def _fake_ocr(pdf_path, page_idx, config):
            ocr_calls.append(page_idx)
            md, blocks = _ocr_output(page_idx)
            return md, "rapidocr", blocks

        monkeypatch.setattr("pdf_router.core.ocr_page", _fake_ocr)

        page = _pipeline_page()
        hit = _process_text_for_page(
            page, str(p27_pdf), cfg, ocr_cache=cache, digest=digest
        )

        assert hit is False
        assert ocr_calls == [0]
        assert page.markdown == _ocr_output(0)[0]

        # Second run must be served from cache.
        ocr_calls.clear()
        page2 = _pipeline_page()
        hit2 = _process_text_for_page(
            page2, str(p27_pdf), cfg, ocr_cache=cache, digest=digest
        )
        assert hit2 is True
        assert ocr_calls == []
        assert page2.markdown == page.markdown
        assert page2.blocks == page.blocks

    def test_digest_none_degrades_to_live_ocr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, p27_pdf: Path
    ) -> None:
        cfg = load_config(None)
        cache = OcrCache(tmp_path / "cache.db")

        class _BrokenDigest:
            def get(self):  # simulate hashing failure
                return None

        ocr_calls: list[int] = []

        def _fake_ocr(pdf_path, page_idx, config):
            ocr_calls.append(page_idx)
            md, blocks = _ocr_output(page_idx)
            return md, "rapidocr", blocks

        monkeypatch.setattr("pdf_router.core.ocr_page", _fake_ocr)

        page = _pipeline_page()
        hit = _process_text_for_page(
            page, str(p27_pdf), cfg, ocr_cache=cache, digest=_BrokenDigest()
        )

        assert hit is False
        assert ocr_calls == [0]  # live OCR ran despite cache being provided

    def test_cache_errors_degrade_to_live_ocr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, p27_pdf: Path
    ) -> None:
        cfg = load_config(None)
        digest = _LazyDigest(p27_pdf, None)

        class _BrokenCache:
            def get(self, *a, **k):
                raise RuntimeError("db locked")

            def put(self, *a, **k):
                raise RuntimeError("db locked")

        ocr_calls: list[int] = []

        def _fake_ocr(pdf_path, page_idx, config):
            ocr_calls.append(page_idx)
            md, blocks = _ocr_output(page_idx)
            return md, "rapidocr", blocks

        monkeypatch.setattr("pdf_router.core.ocr_page", _fake_ocr)

        page = _pipeline_page()
        hit = _process_text_for_page(
            page, str(p27_pdf), cfg, ocr_cache=_BrokenCache(), digest=digest
        )

        # Live path still completed and reported no cache hit.
        assert hit is False
        assert ocr_calls == [0]
        assert page.error is None

    def test_non_ocr_page_bypasses_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, p27_pdf: Path
    ) -> None:
        cfg = load_config(None)
        cache = OcrCache(tmp_path / "cache.db")
        digest = _LazyDigest(p27_pdf, None)

        def _must_not_run(pdf_path, page_idx, config):  # pragma: no cover
            raise AssertionError("OCR must not run for native pages")

        monkeypatch.setattr("pdf_router.core.ocr_page", _must_not_run)

        page = _pipeline_page(needs_ocr=False)
        page.markdown = "уже есть текст"
        hit = _process_text_for_page(
            page, str(p27_pdf), cfg, ocr_cache=cache, digest=digest
        )

        assert hit is False
