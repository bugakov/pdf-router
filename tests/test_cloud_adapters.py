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
    if glm_ocr.ZhipuAiClient is None:
        pytest.skip("zai SDK not installed ([cloud] extra)")

    canned = {"layout_details": [[{"content": "# Heading"}, {"content": "body text"}]]}

    class FakeResponse:
        def model_dump(self):
            return canned

    class FakeLayoutParsing:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        layout_parsing = FakeLayoutParsing()

    with mock.patch.object(glm_ocr, "ZhipuAiClient", FakeClient):
        adapter = glm_ocr.AdapterGlmOcr({"api_key": "sk-test", "base_url": "https://x"})
        md = adapter.ocr_page(str(p27_pdf), 0)

    assert "# Heading" in md
    assert "body text" in md


def test_glm_ocr_page_empty_raises(p27_pdf) -> None:
    if glm_ocr.ZhipuAiClient is None:
        pytest.skip("zai SDK not installed ([cloud] extra)")

    class FakeResponse:
        def model_dump(self):
            return {"layout_details": [[]]}

    class FakeLayoutParsing:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        layout_parsing = FakeLayoutParsing()

    with mock.patch.object(glm_ocr, "ZhipuAiClient", FakeClient):
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

    upload_resp = mock.Mock()
    upload_resp.status_code = 200
    upload_resp.text = "https://files.catbox.moe/abc123.png\n"
    upload_resp.raise_for_status = mock.Mock()

    chat_resp = mock.Mock()
    chat_resp.status_code = 200
    chat_resp.raise_for_status = mock.Mock()
    chat_resp.json.return_value = {"choices": [{"message": {"content": "# OCR via deepseek"}}]}

    with mock.patch.object(deepseek_ocr._requests, "post", side_effect=[upload_resp, chat_resp]):
        adapter = deepseek_ocr.AdapterDeepseekOcr({"api_key": "sk-test", "base_url": "https://x"})
        md = adapter.ocr_page(str(p27_pdf), 0)

    assert md == "# OCR via deepseek"


def test_deepseek_empty_content_raises(p27_pdf) -> None:
    if deepseek_ocr._requests is None:
        pytest.skip("requests not installed ([cloud] extra)")

    upload_resp = mock.Mock()
    upload_resp.text = "https://files.catbox.moe/abc123.png"
    upload_resp.raise_for_status = mock.Mock()

    chat_resp = mock.Mock()
    chat_resp.raise_for_status = mock.Mock()
    chat_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}

    with mock.patch.object(deepseek_ocr._requests, "post", side_effect=[upload_resp, chat_resp]):
        adapter = deepseek_ocr.AdapterDeepseekOcr({"api_key": "sk-test"})
        with pytest.raises(deepseek_ocr.DeepseekOcrError):
            adapter.ocr_page(str(p27_pdf), 0)
