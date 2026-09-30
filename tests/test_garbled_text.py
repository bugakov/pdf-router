"""Undecodable text layers: detection, and the policy that follows.

The failure these guard against is real: a subset font without /ToUnicode
leaves a text layer that passes every alpha-ratio check and carries no
readable characters at all. Committing it poisons the index; OCR-ing it on
a hunch burns the most expensive thing the router has.

So the detector has to be both sensitive (catch real code soup) and
boringly conservative (never touch readable text). The false-positive
cases below are all taken from the actual corpus, not invented.
"""

from __future__ import annotations

import pytest

from pdf_router.ir import Metadata, PageResult, SrcTool
from pdf_router.quality import garbled_runs, looks_garbled, looks_like_text


# ── The two pages that made this predicate necessary ─────────────────────

#: sources/24.pdf p.21 — subset Type1/CFF, /Encoding is /Differences over
#: arbitrary glyph names (GD0, GCE, …), no /ToUnicode anywhere.
GARBLED_PAGE = """
# МАТЕРИАЛЫ К ПОЗНАНИЮ РУССКОГО РЫБОЛОВСТВА

K@K@W@K@8637,-./,0 -(7+0:)]' ! %QKZ^R: 8637,-./,0-(7+0:) WOQQ_P]V">BC2/+1,=6%!$%9'@
0.0KNOPQROSONTUPVVWRX .YVNYZ +,,- 0^_________
"""

#: A real page of the same book: the layer is clean, keep every word of it.
CLEAN_PAGE = """
Систематика водных биологических объектов пресных вод России

Phylum Protozoa — Простейшие
Класс Rhizopoda (саркодовые) — Амебы и радиолярии
"""


# ── looks_garbled: the positives ─────────────────────────────────────────

def test_subset_encoded_page_is_garbled():
    assert looks_garbled(GARBLED_PAGE)


def test_garbled_page_still_looks_like_text_to_the_old_gate():
    """The whole point: the pre-existing gate cannot see this failure."""
    assert looks_like_text(GARBLED_PAGE)


# ── looks_garbled: corpus pages that must never be condemned ────────────

@pytest.mark.parametrize(
    "page, what",
    [
        (
            "в региональной литературе ..............................\n"
            "психологический роман; физиологизм; метод ............... 121\n"
            "Роль народных казачьих песен в прозе Ф. Д. Крюкова .... 121",
            "contents page with dot leaders",
        ),
        (
            "|1930|1931|1932|1933|1934|1935|1936|1937|1938|\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "|12,4|15,8|24,3|16,8|15,2|9,4|11,2|13,7|15,1|",
            "markdown table with a separator row",
        ),
        (
            "Салака 10,44 6,22 13,64 8,45 9,47 9,45 9,51 9,87 8,56 10,19\n"
            "Килька 2,55 2,69 4,30 1,74 2,33 1,73 2,01 0,89 1,34 1,44\n"
            "Стерлядь 0,5 0,1 1,9 0,4 0,4",
            "measurement rows: a label, then numbers",
        ),
        (
            "Контроль Б.Т. П.Т. Б.А. Карп 8,39+0,15 8,69+0,13 9,12+0,07 10,21+0,07\n"
            "2,06±0,02 1,83+0,02 1,97+0,02 2,28+0,02 4,54 5,74 5,65 6,64",
            "initials and error terms in a results table",
        ),
        (
            "0006B2CCAC 579,4 622,4 667,4 66,5 76,8 68,7 11,48 12,34 10,29 11,37\n"
            "0006B2B7D8 625,8 646,6 525,2 83,3 76,5 64,5 13,31 11,83 12,28 12,47",
            "herbarium accession numbers as row labels",
        ),
        (
            "1922 : XXII, 1—101, pis. 1—34; Esсheriсh 1932:178—258;\n"
            "Benander 815; Sоrauer, M953 : 326—341; Hering, 1931 : 210—381",
            "bibliography: punctuation only at token edges",
        ),
        ("Рис. 122—127.", "a figure caption, alone on the page"),
        (
            "**ИЗДАТЕЛЬСТВО «НАУКА»** **ЛЕНИНГРАДСКОЕ ОТДЕЛЕНИЕ**\n"
            "**СОЮЗ СОВЕТСКИХ СОЦИАЛИСТИЧЕСКИХ РЕСПУБЛИК**",
            "an all-caps title page",
        ),
        (CLEAN_PAGE, "an ordinary page of the very same book"),
    ],
)
def test_readable_pages_are_left_alone(page: str, what: str):
    assert not looks_garbled(page), what


def test_a_single_odd_line_never_condemns_a_page():
    """A caption or a formula among clean text must not cost an OCR pass."""
    page = CLEAN_PAGE + "\n\nРис. 122—127.\n"
    assert not looks_garbled(page)


def test_too_little_text_to_judge():
    assert not looks_garbled("")
    assert not looks_garbled("Рис. 1.")
    assert not looks_garbled("K@K@W@8637,-./,0]'##!")


def test_garbled_runs_reports_the_offending_lines():
    runs = garbled_runs(GARBLED_PAGE)
    assert runs
    assert any("QKZ" in r for r in runs)
    assert not any("Рыболовства" in r for r in runs)


# ── the policy in core ───────────────────────────────────────────────────

def _page(markdown: str) -> PageResult:
    return PageResult(page=0, markdown=markdown, src_tool=SrcTool.PDF_INSPECTOR)


def test_drop_policy_clears_the_soup_and_spends_nothing(monkeypatch):
    from pdf_router import core

    def _no_ocr(*a, **kw):  # pragma: no cover — must not be reached
        raise AssertionError("drop policy must not pay for OCR")

    monkeypatch.setattr(core, "ocr_page", _no_ocr)
    cfg = core.RouterConfig()
    cfg.routing.garbled_text_policy = "drop"
    page = _page(GARBLED_PAGE)

    assert core._process_text_for_page(page, "x.pdf", cfg, suspect_encoding=True) is False
    assert page.markdown == ""
    assert page.src_tool is SrcTool.TEXT_LAYER_UNUSABLE


def test_clean_page_is_untouched_by_the_policy(monkeypatch):
    from pdf_router import core

    def _no_ocr(*a, **kw):  # pragma: no cover — must not be reached
        raise AssertionError("a clean page must never reach OCR here")

    monkeypatch.setattr(core, "ocr_page", _no_ocr)
    page = _page(CLEAN_PAGE)
    before = page.markdown

    core._process_text_for_page(page, "x.pdf", core.RouterConfig(), suspect_encoding=True)
    assert page.markdown == before
    assert page.src_tool is SrcTool.PDF_INSPECTOR


def test_a_clean_document_is_never_judged(monkeypatch):
    """Without the document-level flag the predicate does not even run."""
    from pdf_router import core

    page = _page(GARBLED_PAGE)
    # suspect_encoding defaults to False — the historical call signature.
    core._process_text_for_page(page, "x.pdf", core.RouterConfig())
    assert page.src_tool is SrcTool.PDF_INSPECTOR
    assert page.markdown == GARBLED_PAGE


def test_metadata_roundtrip_carries_unusable_pages():
    from pdf_router.ir import IR, from_dict, to_dict

    md = Metadata(page_count=3, unusable_text_pages=[2])
    ir = IR(metadata=md, pages=[])
    assert from_dict(to_dict(ir)).metadata.unusable_text_pages == [2]
