"""Флаг «странице нужно распознавание» не должен теряться (тикет text-quality/02).

На `sources/4589.pdf` (Adobe InDesign, битая кодировка) 11 страниц из 18
возвращаются с пустым текстом. При этом `pdf_inspector` их ПОМЕЧАЕТ:

    extract_pages_markdown(...) → pages_needing_ocr = [3, 4, 7, …]
                                  ocr_reasons = «suspected_garbled_text»
    process_pdf(...)            → pages_needing_ocr = []   ← список пуст

Адаптер брал флаг из `process_pdf` (список уровня документа) и игнорировал
постраничный флаг из `extract_pages_markdown`, который у него уже был в
руках. Дальше `looks_like_text("")` в `core.py` возвращает True по
умыслу, страница объявлялась прочитанной, текст терялся молча.

Тест мокает `pdf_inspector` на границе адаптера: pdf_inspector — колесо без
исходников, поэтому единица проверки — сам адаптер.
"""

from __future__ import annotations

import sys
import types


sys.path.insert(0, "src")

from pdf_router.adapters import pdf_inspector as adapter


class _PM:
    """Страница из extract_pages_markdown."""

    def __init__(self, page: int, markdown: str, needs_ocr: bool, reason: str | None):
        self.page = page
        self.markdown = markdown
        self.needs_ocr = needs_ocr
        self.ocr_reason = reason


def _fake_inspector(*, pages, doc_needing_ocr, has_encoding_issues=True):
    """Подмена pdf_inspector: постраничные флаги есть, документный список пуст."""
    mod = types.SimpleNamespace(
        process_pdf=lambda path: types.SimpleNamespace(
            pdf_type="text_based", page_count=len(pages), confidence=1.0,
            processing_time_ms=1.0, title=None, is_complex_layout=False,
            has_encoding_issues=has_encoding_issues,
            pages_with_tables=[], pages_with_columns=[],
            pages_needing_ocr=list(doc_needing_ocr), ocr_reasons_by_page=[],
        ),
        extract_pages_markdown=lambda path: types.SimpleNamespace(
            pages=pages, pages_needing_ocr=[], ocr_reasons_by_page=[],
            pages_with_tables=[], pages_with_columns=[],
        ),
        extract_structure_elements=lambda path: [],
        extract_text_with_positions=lambda path: [],
    )
    return mod


def _install(monkeypatch, mod):
    monkeypatch.setitem(sys.modules, "pdf_inspector", mod)


def test_per_page_ocr_flag_survives_into_ir(monkeypatch, tmp_path):
    """Страница, помеченная постранично, обязана остаться помеченной в IR."""
    pages = [
        _PM(0, "обычный текст", False, None),
        _PM(1, "", True, "suspected_garbled_text"),
    ]
    _install(monkeypatch, _fake_inspector(pages=pages, doc_needing_ocr=[]))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    ir = adapter.extract_ir(str(pdf))

    assert ir.metadata.has_encoding_issues is True
    assert ir.pages[0].needs_ocr is False
    assert ir.pages[1].needs_ocr is True, (
        "постраничный флаг из extract_pages_markdown потерян — "
        "страница уйдёт в поиск без текста"
    )
    assert ir.pages[1].ocr_reason == "suspected_garbled_text"


def test_document_level_list_still_works(monkeypatch, tmp_path):
    """Прежний источник флага не ломается: список документа тоже учитывается."""
    pages = [_PM(0, "текст", False, None), _PM(1, "", False, None)]
    _install(monkeypatch, _fake_inspector(pages=pages, doc_needing_ocr=[2]))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    ir = adapter.extract_ir(str(pdf))

    assert ir.pages[1].needs_ocr is True
