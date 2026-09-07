---
description: Вынести сборку PageResult из метода AdapterPdfInspector.process() в отдельные процедурные helper-функции, чтобы разделить данные и вызовы pdf-inspector.
---

# Задача: разделить данные и вызовы в `adapters/pdf_inspector.py`

## Что не делаем

- Не удаляем класс `AdapterPdfInspector`. Он нужен как контрактная точка для `core.py` (lazy-import через `ADAPTER_CLASSES` в `src/pdf_router/core.py:41`) и как единое место для `available()` + `name`.
- Не трогаем `ir.py`, dataclasses, `to_dict`/`from_dict`, renderers, core, cli.
- Не вводим новые абстракции (`Pipeline`, `Builder`, `Stage`).
- Не разбиваем на подмодули.

## Что делаем

В `src/pdf_router/adapters/pdf_inspector.py` метод `AdapterPdfInspector.process(self, path)` сейчас смешивает три вещи в одной функции на ~120 строк:

1. Вызовы pdf-inspector (`process_pdf`, `extract_pages_markdown`, `extract_structure_elements`, `extract_text_with_positions`)
2. Построение `Metadata` из `PdfResult`
3. Построение списка `PageResult` со сборкой блоков (headings, tables)

Раздели это на **процедурные helper-функции уровня модуля** и оставь в `process()` только последовательность вызовов.

## Целевая структура `adapters/pdf_inspector.py`

```python
"""pdf-inspector text-path adapter — the only always-loaded adapter.

[docstring сохраняем как есть]

Один вызов process() сводится к:
  1. поднять данные из pdf-inspector (один раз, Rust-проход)
  2. построить Metadata
  3. построить pages со структурными блоками
  4. вернуть IR

Каждый шаг — отдельная функция уровня модуля, без self, без классов-обёрток.
"""

# --- импорты и регулярки — без изменений ---


# --- существующие helper-функции (_parse_markdown_table,
#     _extract_tables_from_markdown, _mode, _headings_from_textitems,
#     _headings_from_structure_tree) — оставляем как есть ---


# --- НОВЫЕ helper-функции уровня модуля ---


def _load_pdf_data(path: str) -> tuple[Metadata, list[Any], list[Any], list[Any]]:
    """Поднять все данные из pdf-inspector одним блоком вызовов.

    Returns:
        metadata    — заполненный `Metadata` (без structural blocks, они заполняются ниже)
        pages_md    — list[Any]  per-page markdown из extract_pages_markdown
        structure   — list[Any]  элементы structure-tree (может быть [])
        text_items  — list[Any]  text-items с позициями (может быть [])

    Каждый вызов pdf-inspector обёрнут в try/except — отсутствующий API
    не должен валить pipeline, как сейчас в строках 233-236 и 244-247.
    """
    import pdf_inspector

    result = pdf_inspector.process_pdf(path)
    pages_md_result = pdf_inspector.extract_pages_markdown(path)
    metadata = _build_metadata(result)

    structure: list[Any] = []
    try:
        structure = pdf_inspector.extract_structure_elements(path)
    except Exception:
        structure = []

    text_items: list[Any] = []
    try:
        text_items = pdf_inspector.extract_text_with_positions(path)
    except Exception:
        text_items = []

    return metadata, list(pages_md_result.pages), structure, text_items


def _build_metadata(result: Any) -> Metadata:
    """Только конструкция Metadata из PdfResult — без структурных блоков.

    Вынесено, чтобы _load_pdf_data и process() не перемешивались.
    """
    return Metadata(
        pdf_type=result.pdf_type,
        page_count=result.page_count,
        confidence=result.confidence,
        processing_time_ms=result.processing_time_ms,
        title=result.title,
        is_complex_layout=result.is_complex_layout,
        has_encoding_issues=result.has_encoding_issues,
        pages_with_tables=list(result.pages_with_tables),
        pages_with_columns=list(result.pages_with_columns),
        pages_needing_ocr=list(result.pages_needing_ocr),
        ocr_reasons_by_page=[
            OcrReasonEntry(page=int(e.page), reasons=list(e.reasons))
            for e in getattr(result, "ocr_reasons_by_page", []) or []
        ],
    )


def _build_pages(
    pages_md: list[Any],
    structure: list[Any],
    text_items: list[Any],
    pages_needing_ocr: list[int],
) -> list[PageResult]:
    """Собрать список PageResult с блоками (headings + tables).

    pages_needing_ocr — 1-indexed список из metadata (нужен для needs_ocr флага).
    """
    reasons_by_page: dict[int, OcrReasonEntry] = {}  # строить из metadata если нужно
    text_items_by_mcid: dict[tuple[int, int], list[Any]] = {}
    for it in text_items:
        if it.mcid is not None:
            text_items_by_mcid.setdefault((it.page, it.mcid), []).append(it)

    headings_by_page = _headings_from_structure_tree(structure, text_items_by_mcid)
    pages_with_struct = set(headings_by_page.keys())

    pages_out: list[PageResult] = []
    for pm in pages_md:
        one_indexed = pm.page + 1
        blocks: list[Any] = []
        blocks.extend(_extract_tables_from_markdown(pm.markdown or ""))
        struct_h = headings_by_page.get(pm.page, [])
        if struct_h:
            blocks.extend(struct_h)
        else:
            blocks.extend(_headings_from_textitems(text_items, pm.page))

        pages_out.append(
            PageResult(
                page=pm.page,
                markdown=pm.markdown or "",
                needs_ocr=one_indexed in pages_needing_ocr,
                ocr_reason=getattr(pm, "ocr_reason", None),
                src_tool=SrcTool.PDF_INSPECTOR,
                blocks=blocks,
            )
        )
    return pages_out


# --- AdapterPdfInspector остаётся, но process() становится коротким ---


class AdapterPdfInspector:
    """Контрактная точка для core.py. Никакой логики — только оркестрация helper'ов."""

    name: ClassVar[str] = "pdf_inspector"

    def available(self) -> bool:
        try:
            import pdf_inspector  # noqa: F401
        except Exception:
            return False
        return True

    def process(self, path: str) -> IR:
        metadata, pages_md, structure, text_items = _load_pdf_data(path)
        pages = _build_pages(
            pages_md,
            structure,
            text_items,
            metadata.pages_needing_ocr,
        )
        return IR(metadata=metadata, pages=pages)
```

## Что важно сохранить

1. **Поведение идентично.** Все golden-anchor тесты (`tests/test_p5.py`, `tests/test_p27.py`, `tests/test_big_test.py`, `tests/test_ir_structural_blocks.py`) должны проходить без изменений. Никаких новых полей, никаких изменений в IR.

2. **Lazy-import сохраняется.** `import pdf_inspector` остаётся внутри `_load_pdf_data` и `available()`, не поднимается на уровень модуля.

3. **Try/except для опциональных вызовов** (`extract_structure_elements`, `extract_text_with_positions`) переезжает в `_load_pdf_data`. Семантика ровно та же: исключение → пустой список.

4. **Docstring `adapters/pdf_inspector.py` сохраняется** без изменений (он уже точно описывает происходящее).

5. **Порядок блоков в `PageResult.blocks`** остаётся: tables → headings. Это важно для golden-anchor'а `tests/test_ir_structural_blocks.py`.

## Что проверить после изменения

Запустить:
```sh
uv run pytest tests/test_p5.py tests/test_p27.py tests/test_big_test.py \
                 tests/test_ir.py tests/test_ir_structural_blocks.py \
                 tests/test_renderers.py -v
```

Все должны быть зелёными. Особое внимание:

- `tests/test_p5.py:24-26` — `page.markdown` непустой и содержит «Обозначение», «ИЛО9.1».
- `tests/test_ir_structural_blocks.py` — порядок и количество блоков по страницам.
- `tests/test_renderers.py:87-101` — JSON round-trip.

Дополнительно — грэпом убедиться, что `AdapterPdfInspector.process` теперь ≤ 15 строк (вместо текущих ~85).

## Если что-то идёт не так

- Если золотой тест падает на порядке блоков — проверь, что `blocks.extend(_extract_tables_from_markdown(...))` идёт **до** `blocks.extend(... headings ...)`, а не после.
- Если падает на `ocr_reason` — в текущем коде (`src/pdf_router/adapters/pdf_inspector.py:281-285`) `ocr_reason` собирается из metadata по 1-indexed странице. В новой версии metadata передаётся в `_build_pages` через `pages_needing_ocr`; `ocr_reason` нужно либо тоже прокинуть, либо взять из `pm.ocr_reason` (текущий fallback в `:284`). Реши по тому, как сейчас тесты ожидают.
- Если mypy ругается на `Any` — добавь `# type: ignore[arg-type]` только там, где это уже было в исходнике (строки 230-247 сейчас без аннотаций, так что добавлять не надо).

## Критерий готовности

- `process()` ≤ 15 строк.
- `_load_pdf_data` / `_build_metadata` / `_build_pages` — отдельные функции уровня модуля, без `self`, без `cls`.
- Никаких новых классов, dataclass-обёрток, билдеров.
- Все тесты зелёные.
- `git diff --stat` показывает только `src/pdf_router/adapters/pdf_inspector.py`.