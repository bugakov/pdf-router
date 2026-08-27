"""Ticket 07: cloud OCR adapters — mocked HTTP only, no live network in CI."""

from unittest import mock

import pytest

from pdf_router.adapters import deepseek_ocr, glm_ocr


# --- GLM -----------------------------------------------------------------------


def test_glm_available_requires_api_key() -> None:
    adapter = glm_ocr.AdapterGlmOcr({"api_key": "", "base_url": "https://x"})
    assert adapter.available() is False


def test_glm_available_rejects_missing_key() -> None:
    adapter = glm_ocr.AdapterGlmOcr({"base_url": "https://x"})
    assert adapter.available() is False


def test_glm_ocr_page_mocked(p27_pdf) -> None:
    if glm_ocr._requests is None:
        pytest.skip("requests not installed ([cloud] extra)")

    resp = mock.Mock()
    resp.status_code = 200
    resp.raise_for_status = mock.Mock()
    resp.json.return_value = {"md_results": "# Heading\n\nbody text"}

    with mock.patch.object(glm_ocr._requests, "post", return_value=resp) as post:
        adapter = glm_ocr.AdapterGlmOcr({"api_key": "sk-test", "base_url": "https://x"})
        md = adapter.ocr_page(str(p27_pdf), 0)

    assert "# Heading" in md
    assert "body text" in md
    # standard endpoint layout_parsing with inline single-page PDF
    url = post.call_args.args[0]
    body = post.call_args.kwargs["json"]
    assert url.endswith("/layout_parsing")
    assert body["model"] == "glm-ocr"
    assert body["file"].startswith("data:application/pdf;base64,")


def test_glm_ocr_page_empty_raises(p27_pdf) -> None:
    if glm_ocr._requests is None:
        pytest.skip("requests not installed ([cloud] extra)")

    resp = mock.Mock()
    resp.raise_for_status = mock.Mock()
    resp.json.return_value = {"md_results": ""}

    with mock.patch.object(glm_ocr._requests, "post", return_value=resp):
        adapter = glm_ocr.AdapterGlmOcr({"api_key": "sk-test"})
        with pytest.raises(glm_ocr.GlmOcrError):
            adapter.ocr_page(str(p27_pdf), 0)


# --- deepseek ------------------------------------------------------------------


def test_deepseek_available_requires_api_key() -> None:
    adapter = deepseek_ocr.AdapterDeepseekOcr({"api_key": "", "base_url": "https://x"})
    assert adapter.available() is False


def test_deepseek_ocr_page_mocked(p27_pdf) -> None:
    if deepseek_ocr._requests is None:
        pytest.skip("requests not installed ([cloud] extra)")

    # default host is uguu.se: JSON body {"files": [{"url": ...}]}
    upload_resp = mock.Mock()
    upload_resp.status_code = 200
    upload_resp.json.return_value = {"success": True, "files": [{"url": "https://h.uguu.se/abc123.png"}]}
    upload_resp.text = '{"success": true}'
    upload_resp.raise_for_status = mock.Mock()

    chat_resp = mock.Mock()
    chat_resp.status_code = 200
    chat_resp.raise_for_status = mock.Mock()
    chat_resp.json.return_value = {"choices": [{"message": {"content": "# OCR via deepseek"}}]}

    with mock.patch.object(deepseek_ocr._requests, "post", side_effect=[upload_resp, chat_resp]) as post:
        adapter = deepseek_ocr.AdapterDeepseekOcr({"api_key": "sk-test", "base_url": "https://x"})
        md = adapter.ocr_page(str(p27_pdf), 0)

    assert md == "# OCR via deepseek"
    # uguu.se requires the "files[]" field name and no extra form data
    up_files = post.call_args_list[0].kwargs["files"]
    assert "files[]" in up_files
    assert post.call_args_list[0].kwargs.get("data") is None


def test_deepseek_upload_plain_text_host(p27_pdf) -> None:
    """catbox-style hosts answer with a bare URL in the body."""
    if deepseek_ocr._requests is None:
        pytest.skip("requests not installed ([cloud] extra)")

    upload_resp = mock.Mock()
    upload_resp.status_code = 200
    upload_resp.text = "https://files.catbox.moe/abc123.png\n"
    upload_resp.json.side_effect = ValueError("no json")
    upload_resp.raise_for_status = mock.Mock()

    chat_resp = mock.Mock()
    chat_resp.raise_for_status = mock.Mock()
    chat_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

    with mock.patch.object(deepseek_ocr._requests, "post", side_effect=[upload_resp, chat_resp]) as post:
        adapter = deepseek_ocr.AdapterDeepseekOcr(
            {"api_key": "sk-test", "upload_url": "https://catbox.moe/user/api.php"}
        )
        md = adapter.ocr_page(str(p27_pdf), 0)

    assert md == "ok"
    # catbox-style: fileToUpload field + reqtype form data
    up_files = post.call_args_list[0].kwargs["files"]
    assert "fileToUpload" in up_files
    assert post.call_args_list[0].kwargs["data"] == {"reqtype": "fileupload"}


def test_deepseek_empty_content_raises(p27_pdf) -> None:
    if deepseek_ocr._requests is None:
        pytest.skip("requests not installed ([cloud] extra)")

    upload_resp = mock.Mock()
    upload_resp.json.return_value = {"files": [{"url": "https://h.uguu.se/abc123.png"}]}
    upload_resp.raise_for_status = mock.Mock()

    chat_resp = mock.Mock()
    chat_resp.raise_for_status = mock.Mock()
    chat_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}

    with mock.patch.object(deepseek_ocr._requests, "post", side_effect=[upload_resp, chat_resp]):
        adapter = deepseek_ocr.AdapterDeepseekOcr({"api_key": "sk-test"})
        with pytest.raises(deepseek_ocr.DeepseekOcrError):
            adapter.ocr_page(str(p27_pdf), 0)
