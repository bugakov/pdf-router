"""Wire-protocol tests for the cloud OCR engines (flat functions in ocr.py).

Mocked HTTP only — no live network in CI. These tests were ported from the
retired adapter classes (tests/test_cloud_adapters.py, deleted with them per
TKT-RT-14); they pin the same wire contracts against ``_ocr_glm`` /
``_ocr_deepseek``:

- GLM: ``{base_url}/layout_parsing``, ``model=glm-ocr``, base64 data-URL
  ``file``, markdown from ``md_results``, empty → OcrError.
- deepseek: single POST to chat/completions with the page as a base64
  data-URL inline (P5 privacy rewrite — the retired uguu.se upload hop is
  gone); empty content → OcrError.
- retry/backoff: both engines POST through ``_post_with_retry`` —
  transient 429/5xx/network errors are retried with exponential backoff
  + jitter, ``Retry-After`` wins, deterministic 4xx never retry,
  ``max_retries=0`` restores the old single-attempt behavior.
"""

from __future__ import annotations

import importlib.util
from unittest import mock

import pytest

from pdf_router.ocr import (
    OcrError,
    _ocr_deepseek,
    _ocr_glm,
    _ocr_tesseract,
    _post_with_retry,
)

_REQUESTS_AVAILABLE = importlib.util.find_spec("requests") is not None
needs_requests = pytest.mark.skipif(
    not _REQUESTS_AVAILABLE, reason="requests not installed ([cloud] extra)"
)


def _resp(*, json_data: dict | None = None, text: str = "", json_error: bool = False):
    r = mock.Mock()
    r.raise_for_status = mock.Mock()
    if json_error:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = json_data or {}
    r.text = text
    return r


# --- GLM ------------------------------------------------------------------------


def test_glm_missing_api_key_raises() -> None:
    with pytest.raises(OcrError, match="api_key"):
        _ocr_glm("/tmp/x.pdf", 0, {"base_url": "https://x"})


@needs_requests
def test_glm_ocr_page_mocked(p27_pdf) -> None:
    resp = _resp(json_data={"md_results": "# Heading\n\nbody text"})

    with mock.patch("requests.post", return_value=resp) as post:
        md, layout = _ocr_glm(str(p27_pdf), 0, {"api_key": "sk-test", "base_url": "https://x"})

    assert "# Heading" in md
    assert "body text" in md
    assert layout is None  # stub response has no layout_details
    # standard endpoint layout_parsing with inline single-page PDF
    url = post.call_args.args[0]
    body = post.call_args.kwargs["json"]
    assert url.endswith("/layout_parsing")
    assert body["model"] == "glm-ocr"
    assert body["file"].startswith("data:application/pdf;base64,")


@needs_requests
def test_glm_ocr_page_empty_raises(p27_pdf) -> None:
    resp = _resp(json_data={"md_results": ""})

    with mock.patch("requests.post", return_value=resp):
        with pytest.raises(OcrError, match="empty md_results"):
            _ocr_glm(str(p27_pdf), 0, {"api_key": "sk-test"})


# --- deepseek -------------------------------------------------------------------


def test_deepseek_missing_api_key_raises() -> None:
    with pytest.raises(OcrError, match="api_key"):
        _ocr_deepseek("/tmp/x.pdf", 0, {"base_url": "https://x"})


@needs_requests
def test_deepseek_ocr_page_mocked(p27_pdf) -> None:
    chat_resp = _resp(json_data={"choices": [{"message": {"content": "# OCR via deepseek"}}]})

    with mock.patch("requests.post", return_value=chat_resp) as post:
        md = _ocr_deepseek(
            str(p27_pdf), 0, {"api_key": "sk-test", "base_url": "https://x"}
        )

    assert md == "# OCR via deepseek"
    # Single POST straight to chat/completions; page travels inline as a
    # PNG data-URL — no image-upload hop, no public file host (P5).
    url = post.call_args.args[0]
    body = post.call_args.kwargs["json"]
    assert url.endswith("/chat/completions")
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1] == {"type": "text", "text": "OCR this image."}


@needs_requests
def test_deepseek_dpi_param_controls_raster_size(p27_pdf) -> None:
    """The dpi param flows through to rasterize_page (rasterize seam)."""
    chat_resp = _resp(json_data={"choices": [{"message": {"content": "ok"}}]})

    with (
        mock.patch("requests.post", return_value=chat_resp),
        mock.patch(
            "pdf_router.ocr.rasterize_page", return_value=b"fakepng"
        ) as raster,
    ):
        _ocr_deepseek(str(p27_pdf), 0, {"api_key": "sk-test", "dpi": 300})

    assert raster.call_args.kwargs["dpi"] == 300


@needs_requests
def test_deepseek_empty_content_raises(p27_pdf) -> None:
    chat_resp = _resp(json_data={"choices": [{"message": {"content": ""}}]})

    with mock.patch("requests.post", return_value=chat_resp):
        with pytest.raises(OcrError, match="empty content"):
            _ocr_deepseek(str(p27_pdf), 0, {"api_key": "sk-test"})


# --- tesseract ------------------------------------------------------------------


def test_tesseract_missing_binary_raises() -> None:
    """Flat engine raises OcrError when the binary is absent (no slow OCR)."""
    with pytest.raises(OcrError, match="tesseract binary not found"):
        _ocr_tesseract("/tmp/x.pdf", 0, {"bin": "definitely-not-tesseract-xyz"})


def test_tesseract_timeout_param_reaches_subprocess(p27_pdf) -> None:
    """``timeout`` flows from params into subprocess.run; a hang surfaces
    as OcrError (→ page.error → exit 2), never a wedged worker thread."""
    import subprocess
    from unittest import mock

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    with (
        mock.patch("shutil.which", return_value="/usr/bin/tesseract"),
        mock.patch(
            "pdf_router.ocr.rasterize_page", return_value=b"fakepng"
        ),
        mock.patch("subprocess.run", side_effect=_fake_run),
    ):
        with pytest.raises(OcrError, match="timed out after 7"):
            _ocr_tesseract(str(p27_pdf), 0, {"timeout": 7})

    assert captured["timeout"] == 7


# --- retry/backoff (_post_with_retry) -------------------------------------------


def _status_resp(code: int, headers: dict | None = None):
    r = mock.Mock()
    r.status_code = code
    r.headers = headers or {}
    r.text = f"body {code}"
    return r


@needs_requests
def test_retry_recovers_from_transient_5xx() -> None:
    """500 → 502 → 200: the third attempt wins; pauses grow exponentially
    (base 1.0 * 2**attempt + jitter < 0.5)."""
    ok = _status_resp(200)
    sleeps: list[float] = []

    with (
        mock.patch("requests.post", side_effect=[_status_resp(500), _status_resp(502), ok]) as post,
        mock.patch("pdf_router.ocr.time.sleep", side_effect=sleeps.append),
    ):
        r = _post_with_retry("https://x/api", json={}, headers={}, timeout=1.0)

    assert r is ok
    assert post.call_count == 3
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 1.5  # 2**0 * 1.0 + jitter
    assert 2.0 <= sleeps[1] <= 2.5  # 2**1 * 1.0 + jitter


@needs_requests
def test_retry_never_retries_deterministic_4xx() -> None:
    """A 400 answer is final on the first attempt — no sleeps, no repeats."""
    bad = _status_resp(400)

    with (
        mock.patch("requests.post", return_value=bad) as post,
        mock.patch("pdf_router.ocr.time.sleep") as sleep,
    ):
        r = _post_with_retry("https://x/api", json={}, headers={}, timeout=1.0)

    assert r is bad
    assert post.call_count == 1
    sleep.assert_not_called()


@needs_requests
def test_retry_recovers_from_network_error() -> None:
    """ConnectionError, then success: retried like a transient status."""
    import requests

    ok = _status_resp(200)

    with (
        mock.patch("requests.post", side_effect=[requests.ConnectionError("boom"), ok]),
        mock.patch("pdf_router.ocr.time.sleep") as sleep,
    ):
        r = _post_with_retry("https://x/api", json={}, headers={}, timeout=1.0)

    assert r is ok
    sleep.assert_called_once()


@needs_requests
def test_retry_exhaustion_on_status_returns_last_response() -> None:
    """All attempts transient: the LAST response is returned so the caller's
    raise_for_status() reports the final attempt truthfully."""
    last = _status_resp(429)

    with (
        mock.patch("requests.post", side_effect=[_status_resp(500), _status_resp(503), last]),
        mock.patch("pdf_router.ocr.time.sleep"),
    ):
        r = _post_with_retry("https://x/api", json={}, headers={}, timeout=1.0, max_retries=2)

    assert r is last


@needs_requests
def test_retry_exhaustion_on_network_raises_last_exception() -> None:
    """Network flavor of exhaustion: the last exception propagates so the
    engine's OcrError wrap keeps the final attempt's message."""
    import requests

    boom = requests.ConnectionError("final failure")

    with (
        mock.patch("requests.post", side_effect=boom) as post,
        mock.patch("pdf_router.ocr.time.sleep"),
    ):
        with pytest.raises(requests.ConnectionError) as exc_info:
            _post_with_retry("https://x/api", json={}, headers={}, timeout=1.0, max_retries=1)

    assert exc_info.value is boom
    assert post.call_count == 2


@needs_requests
def test_retry_after_header_wins_over_backoff() -> None:
    """Integer-seconds Retry-After overrides our own backoff; the HTTP-date
    form is ignored (float() fails → exponential fallback)."""
    sleeps: list[float] = []

    with (
        mock.patch(
            "requests.post",
            side_effect=[
                _status_resp(429, headers={"Retry-After": "7"}),
                _status_resp(503, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
                _status_resp(200),
            ],
        ),
        mock.patch("pdf_router.ocr.time.sleep", side_effect=sleeps.append),
    ):
        _post_with_retry("https://x/api", json={}, headers={}, timeout=1.0)

    assert sleeps[0] == 7.0
    assert 2.0 <= sleeps[1] <= 2.5  # date form ignored → own backoff (2**1)


@needs_requests
def test_retry_disabled_with_max_retries_zero() -> None:
    """max_retries=0 restores the old behavior: exactly one attempt."""
    bad = _status_resp(500)

    with (
        mock.patch("requests.post", return_value=bad) as post,
        mock.patch("pdf_router.ocr.time.sleep") as sleep,
    ):
        r = _post_with_retry("https://x/api", json={}, headers={}, timeout=1.0, max_retries=0)

    assert r is bad
    assert post.call_count == 1
    sleep.assert_not_called()


@needs_requests
def test_engine_retry_params_flow_into_post_with_retry(p27_pdf) -> None:
    """[ocr.*] max_retries / retry_backoff_s reach the helper for both cloud
    engines; absent params keep the documented defaults (2 retries, 1.0 s)."""
    resp = _resp(json_data={"md_results": "# ok", "choices": [{"message": {"content": "ok"}}]})

    with mock.patch("pdf_router.ocr._post_with_retry", return_value=resp) as post:
        _ocr_glm(str(p27_pdf), 0, {"api_key": "k", "max_retries": 5, "retry_backoff_s": 0.5})
        _ocr_deepseek(str(p27_pdf), 0, {"api_key": "k", "max_retries": 1, "retry_backoff_s": 2.0})
        _ocr_glm(str(p27_pdf), 0, {"api_key": "k"})  # no retry keys → defaults

    glm_kwargs = post.call_args_list[0].kwargs
    assert glm_kwargs["max_retries"] == 5
    assert glm_kwargs["backoff_s"] == 0.5
    ds_kwargs = post.call_args_list[1].kwargs
    assert ds_kwargs["max_retries"] == 1
    assert ds_kwargs["backoff_s"] == 2.0
    default_kwargs = post.call_args_list[2].kwargs
    assert default_kwargs["max_retries"] == 2
    assert default_kwargs["backoff_s"] == 1.0
