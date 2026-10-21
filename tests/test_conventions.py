"""Ticket 11 (optional): structural conventions from DESIGN §10/§11."""

import ast
import dataclasses
from pathlib import Path

import pytest

from pdf_router.ir import IR_VERSION, Metadata, PageResult, IR

SRC = Path(__file__).parent.parent / "src" / "pdf_router"


def test_ir_version() -> None:
    # 0.6: office ingest (source_format "xlsx", XLSX_ADAPTER —
    #   ADR-0008 / TKT-OI-03);
    # 0.5: OCR structure blocks (TableBlock.source, scan headings {1,2} —
    #   ADR-0005 / TKT-ST-04);
    # 0.4: djvu ingest (SrcTool.DJVU_TEXT_LAYER — ADR-0004 / TKT-DJV-04);
    # 0.3 was format-neutral IR (source_format, DOCX_ADAPTER — ADR-0003).
    assert IR_VERSION == "0.6"


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


#: Modules a renderer may import: stdlib plus the IR/config surface.
#: ``docling_core`` is allowed *only* in the docling renderer — the optional
#: extra (ADR-0009), imported lazily inside the renderer so that
#: ``import pdf_router`` stays cheap. Every other renderer keeps the strict set.
RENDERER_IMPORTS = {
    "json", "re", "pathlib", "dataclasses", "typing", "__future__",
    "pdf_router",
}


def test_no_renderer_imports_adapter() -> None:
    renderers_dir = SRC / "renderers"
    for py in renderers_dir.glob("*.py"):
        mods = _imported_modules(py)
        assert "adapters" not in mods, f"{py.name} imports adapters"
        # stdlib/third-party/ir/config only
        allowed = set(RENDERER_IMPORTS)
        if py.name == "docling.py":
            allowed.add("docling_core")
        assert mods <= allowed, f"{py.name} has unexpected imports: {mods - allowed}"


def test_no_adapter_imports_renderer() -> None:
    adapters_dir = SRC / "adapters"
    for py in adapters_dir.glob("*.py"):
        mods = _imported_modules(py)
        assert "renderers" not in mods, f"{py.name} imports renderers"
