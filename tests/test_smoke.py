"""Smoke-тесты пакета."""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_import() -> None:
    import pdf_router

    assert pdf_router.__version__


def test_version_matches_pyproject() -> None:
    """Версия дублируется в pyproject.toml и в __init__.py.

    litdb пишет `pdf_router.__version__` в `parse_jobs.tool_version`, и по
    нему судят, каким адаптером разобран документ. Если две копии разойдутся,
    признак станет бесполезным, поэтому расхождение ловим здесь.
    """
    import pdf_router

    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match, "в pyproject.toml нет поля version"
    assert match.group(1) == pdf_router.__version__, (
        f"pyproject: {match.group(1)} != __version__: {pdf_router.__version__}"
    )
