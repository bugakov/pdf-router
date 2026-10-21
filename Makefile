# Bare `make` = the CI gate; run it before every push.
# Keep lint/types/test in sync with .github/workflows/ci.yml.

UV := uv run

.DEFAULT_GOAL := check
.PHONY: setup sync lint types test test-docling test-full check fix

## First-time machine setup: djvulibre, dev extras, .runtime binaries.
## pdfium/onnxruntime are linux-x64 builds pinned to the same versions as
## CI (see ci.yml); on other OS/arch adjust the two URLs + checksum.
setup:
	@command -v djvused >/dev/null 2>&1 || { \
		if command -v apt-get >/dev/null 2>&1; then \
			sudo apt-get update -q && sudo apt-get install -y -q djvulibre-bin; \
		else \
			echo "WARNING: djvulibre-bin missing and apt-get unavailable — install djvulibre manually"; \
		fi; }
	uv sync --extra dev --locked
	mkdir -p .runtime
	curl -fsSL --retry 3 -o /tmp/pdfium.tgz \
		https://github.com/bblanchon/pdfium-binaries/releases/download/chromium/7988/pdfium-linux-x64.tgz
	echo "7358c15e26a746cd67854887ea11b3b807c436056788eee9294fb972b8f8e0be  /tmp/pdfium.tgz" | sha256sum -c -
	tar -xzf /tmp/pdfium.tgz -C .runtime
	curl -fsSL --retry 3 -o /tmp/ort.tgz \
		https://github.com/microsoft/onnxruntime/releases/download/v1.27.0/onnxruntime-linux-x64-1.27.0.tgz
	tar -xzf /tmp/ort.tgz -C .runtime

## Refresh the venv after pyproject/uv.lock changes (opt-in: prunes extras).
sync:
	uv sync --extra dev --locked

lint:
	$(UV) ruff check src tests

types:
	$(UV) mypy

## Fast tier — exactly what CI runs.
test:
	$(UV) pytest -m "not slow" -q

## Whole suite incl. slow OCR anchors (~20 s/page; corpus tests skip gracefully).
test-full:
	$(UV) pytest -q

## The docling renderer's own tier (ADR-0009): the optional extra must be
## installed, otherwise the whole module skips and the schema gate
## (DoclingDocument.model_validate) never runs.
test-docling:
	uv sync --extra dev --extra docling --locked
	$(UV) pytest tests/test_docling_renderer.py -q

## Pre-push gate: lint + types + fast tests = the ci.yml sequence.
check: lint types test

fix:
	$(UV) ruff check --fix src tests
