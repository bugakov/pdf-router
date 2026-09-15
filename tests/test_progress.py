"""Progress-callback contract: streaming events + the cancel protocol.

The synthetic tests run against the ``route()`` seam (a fake inspector
returning native-only pages): no OCR, no rasterization, not even a real
file — the whole file stays in the fast tier and is fully deterministic.
(A synthetic pymupdf PDF does NOT work here: the real Rust inspector
flags such pages needs_ocr=True and the test lands in live OCR.) Two
tests pin the event/report agreement on real golden fixtures: p5.pdf
(native page) and p27.pdf with a sabotaged OCR engine — the same
sabotage pattern as tests/test_failure_reporting.py.

Pinned here:
- one event per page, document order, ``pages_done`` monotonic 1..total;
- event ``status`` uses the report-aggregation rule (error → failed,
  needs_ocr → ocr, else native) — the stream never contradicts the
  report/IR the caller ends up with;
- a broken callback never kills the run: counted in
  ``report.progress_callback_errors``, every page still processed;
- ``PipelineCancelled`` is the sanctioned cancel: done pages keep their
  real results, the in-flight page finishes, never-started pages get
  ``error="cancelled"`` (counted in ``pages_cancelled`` and
  ``pages_failed``);
- ``convert()`` forwards ``progress=`` verbatim.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import pdf_router
import pdf_router.core as core_mod
from pdf_router import PipelineCancelled, RouterConfig
from pdf_router.ir import IR, Metadata, PageResult


class _FakeInspector:
    """``route()`` double: N native pages, zero IO, zero OCR.

    A default PageResult (needs_ocr=False, empty markdown) passes the
    quality gate untouched (quality.py trusts empty text), so the text
    pass returns without reading any file or engine — the input path can
    even be a name that does not exist.
    """

    def __init__(self, n: int) -> None:
        self._n = n

    def __call__(self, _pdf_path: str) -> IR:
        return IR(
            metadata=Metadata(pdf_type="text_based", page_count=self._n),
            pages=[PageResult(page=i) for i in range(self._n)],
        )


def _install(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    monkeypatch.setattr(core_mod, "route", lambda _path: _FakeInspector(n))


def test_events_order_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, 5)
    events: list[pdf_router.ProgressEvent] = []
    ir, report = pdf_router.process_pdf("fake.pdf", progress=events.append)

    assert [e.page for e in events] == list(range(5))  # document order, 0-based
    assert [e.pages_done for e in events] == [1, 2, 3, 4, 5]  # monotonic
    assert all(e.pages_total == 5 for e in events)
    assert events[-1].elapsed_s >= events[0].elapsed_s  # monotonic clock
    assert len(events) == len(ir.pages) == report.pages_total


def test_event_status_native_on_real_fixture(p5_pdf: Path) -> None:
    """A real native page streams 'native' — agreeing with the report."""

    events: list[pdf_router.ProgressEvent] = []
    _, report = pdf_router.process_pdf(str(p5_pdf), progress=events.append)

    assert [e.status for e in events] == ["native"]
    assert report.pages_native == 1
    assert report.pages_failed == 0


def test_failed_page_streams_failed_status(p27_pdf: Path, monkeypatch) -> None:
    """A page that ends with page.error streams 'failed' — the event must
    agree with what the final report will say about it."""

    def boom(pdf_path, page_idx, config):
        raise ValueError("engine exploded")

    monkeypatch.setattr(core_mod, "ocr_page", boom)
    events: list[pdf_router.ProgressEvent] = []
    ir, report = pdf_router.process_pdf(str(p27_pdf), progress=events.append)

    assert [e.status for e in events] == ["failed"]
    assert ir.pages[0].error is not None
    assert report.pages_failed == 1


def test_broken_callback_never_kills_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-PipelineCancelled exception is caller's-bug: logged, counted,
    survived — the document still processes end to end."""
    _install(monkeypatch, 4)

    def bad(_ev) -> None:
        raise ValueError("typo in caller's callback")

    ir, report = pdf_router.process_pdf("fake.pdf", progress=bad)

    assert report.progress_callback_errors == 4
    assert report.pages_failed == 0  # the pages themselves are fine
    assert report.pages_native == 4
    assert all(p.error is None for p in ir.pages)


def test_cancel_keeps_done_and_marks_never_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel after page 0 (workers=1): page 0 keeps its result, page 1 is
    provably in flight (handshake below) and finishes with a real result,
    pages 2..4 are still queued and get error='cancelled'.

    The handshake makes it deterministic: the callback does not raise until
    page 1 has STARTED (so cancel_futures cannot eat it); page 1 then
    blocks on a gate the test never opens, so the single worker cannot
    reach the queue while shutdown(cancel_futures=True) discards pages
    2..4. The gate times out on its own, letting page 1 complete during
    the pool exit.
    """
    _install(monkeypatch, 5)
    started = threading.Event()
    gate = threading.Event()

    def gated_text(page, pdf_path, config) -> None:
        if page.page > 0:
            if page.page == 1:
                started.set()
            gate.wait(timeout=0.5)  # never opened: expires, page finishes

    monkeypatch.setattr(core_mod, "_process_text_for_page", gated_text)

    seen: list[pdf_router.ProgressEvent] = []

    def cancel_after_first(ev) -> None:
        seen.append(ev)
        started.wait(timeout=5)  # page 1 in flight before we cancel
        raise PipelineCancelled()

    cfg = RouterConfig()
    cfg.routing.workers = 1
    ir, report = pdf_router.process_pdf(
        "fake.pdf", config=cfg, progress=cancel_after_first
    )

    assert [e.page for e in seen] == [0]  # stream stops at the cancel point
    assert ir.pages[0].error is None  # done before the cancel: kept
    assert ir.pages[1].error is None  # in flight: finished, real result wins
    for page in ir.pages[2:]:
        assert page.error == "cancelled"  # queued: never ran
    assert report.pages_cancelled == 3
    assert report.pages_failed == 3  # cancelled pages count as failed
    assert report.pages_native == 2


def test_convert_forwards_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, 2)
    events: list[pdf_router.ProgressEvent] = []
    markdown = pdf_router.convert("fake.pdf", progress=events.append)

    assert [e.page for e in events] == [0, 1]
    assert isinstance(markdown, str)
