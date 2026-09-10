"""Wire-protocol tests for the cloud OCR engines (flat functions in ocr.py).

Mocked HTTP only — no live network in CI. These tests were ported from the
retired adapter classes (tests/test_cloud_adapters.py, deleted with them per
TKT-RT-14); they pin the same wire contracts against ``_ocr_glm`` /
``_ocr_deepseek``:

- GLM: ``{base_url}/layout_parsing``, ``model=glm-ocr``, base64 data-URL
  ``file``, markdown from ``md_results``, empty → OcrError.
- deepseek: two POSTs (image upload, then chat/completions); uguu-style
  hosts take ``files[]`` with no form data, catbox-style hosts take
  ``fileToUpload`` + ``reqtype`` and answer with a plain-text URL;
  empty content → OcrError.
"""

from __future__ import annotations

import importlib.util
from unittest import mock

import pytest

from pdf_router.ocr import OcrError, _ocr_deepseek, _ocr_glm, _ocr_tesseract

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
        md = _ocr_glm(str(p27_pdf), 0, {"api_key": "sk-test", "base_url": "https://x"})

    assert "# Heading" in md
    assert "body text" in md
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
    # default host is uguu.se: JSON body {"files": [{"url": ...}]}
    upload_resp = _resp(json_data={"success": True, "files": [{"url": "https://h.uguu.se/abc123.png"}]})
    chat_resp = _resp(json_data={"choices": [{"message": {"content": "# OCR via deepseek"}}]})

    with mock.patch("requests.post", side_effect=[upload_resp, chat_resp]) as post:
        md = _ocr_deepseek(
            str(p27_pdf), 0, {"api_key": "sk-test", "base_url": "https://x"}
        )

    assert md == "# OCR via deepseek"
    # uguu.se requires the "files[]" field name and no extra form data
    up_files = post.call_args_list[0].kwargs["files"]
    assert "files[]" in up_files
    assert post.call_args_list[0].kwargs.get("data") is None


@needs_requests
def test_deepseek_upload_plain_text_host(p27_pdf) -> None:
    """catbox-style hosts answer with a bare URL in the body."""
    upload_resp = _resp(
        text="https://files.catbox.moe/abc123.png\n",
        json_error=True,
    )
    chat_resp = _resp(json_data={"choices": [{"message": {"content": "ok"}}]})

    with mock.patch("requests.post", side_effect=[upload_resp, chat_resp]) as post:
        md = _ocr_deepseek(
            str(p27_pdf),
            0,
            {"api_key": "sk-test", "upload_url": "https://catbox.moe/user/api.php"},
        )

    assert md == "ok"
    # catbox-style: fileToUpload field + reqtype form data
    up_files = post.call_args_list[0].kwargs["files"]
    assert "fileToUpload" in up_files
    assert post.call_args_list[0].kwargs["data"] == {"reqtype": "fileupload"}


@needs_requests
def test_deepseek_empty_content_raises(p27_pdf) -> None:
    upload_resp = _resp(json_data={"files": [{"url": "https://h.uguu.se/abc123.png"}]})
    chat_resp = _resp(json_data={"choices": [{"message": {"content": ""}}]})

    with mock.patch("requests.post", side_effect=[upload_resp, chat_resp]):
        with pytest.raises(OcrError, match="empty content"):
            _ocr_deepseek(str(p27_pdf), 0, {"api_key": "sk-test"})


# --- tesseract ------------------------------------------------------------------


def test_tesseract_missing_binary_raises() -> None:
    """Flat engine raises OcrError when the binary is absent (no slow OCR)."""
    with pytest.raises(OcrError, match="tesseract binary not found"):
        _ocr_tesseract("/tmp/x.pdf", 0, {"bin": "definitely-not-tesseract-xyz"})
