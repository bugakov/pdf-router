"""Ticket 11 (optional): structural conventions from DESIGN §10/§11."""

import ast
import dataclasses
from pathlib import Path

import pytest

from pdf_router.ir import IR_VERSION, Metadata, PageResult, IR

SRC = Path(__file__).parent.parent / "src" / "pdf_router"


def test_ir_version() -> None:
    # 0.3: format-neutral IR (Metadata.source_format, SrcTool.DOCX_ADAPTER —
    # ADR-0003 / TKT-DX-03).
    assert IR_VERSION == "0.3"


@pytest.mark.parametrize("cls", [Metadata, PageResult, IR])
def test_no_mutable_default_args(cls) -> None:
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            assert not isinstance(f.default, (list, dict, set)), f"{cls.__name__}.{f.name}"
        # default_factory is the sanctioned pattern for mutables


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_no_renderer_imports_adapter() -> None:
    renderers_dir = SRC / "renderers"
    for py in renderers_dir.glob("*.py"):
        mods = _imported_modules(py)
        assert "adapters" not in mods, f"{py.name} imports adapters"
        # stdlib/third-party/ir/config only
        assert mods <= {
            "json", "re", "pathlib", "dataclasses", "typing", "__future__",
            "pdf_router",
        }, f"{py.name} has unexpected imports: {mods - {'json', 're', 'pathlib', 'dataclasses', 'typing', '__future__', 'pdf_router'}}"


def test_no_adapter_imports_renderer() -> None:
    adapters_dir = SRC / "adapters"
    for py in adapters_dir.glob("*.py"):
        mods = _imported_modules(py)
        assert "renderers" not in mods, f"{py.name} imports renderers"
