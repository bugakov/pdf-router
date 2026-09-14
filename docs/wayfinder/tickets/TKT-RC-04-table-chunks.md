# TKT-RC-04 — Table chunks: KV embed, facts summary, row-split, caption rule

## Question

The table half of the renderer, as pure functions over `TableBlock` +
context (from RC-03's contract):

1. **Caption-row demotion** (the Рыбы p.14 case): row 0 demotes to
   caption when it is a single content cell (rest empty/merged-blank)
   or starts with `Таблица|Табл.|Table|№`; key header = the next row;
   caption joins the chunk context. Document the exact predicate.
2. **Facts summary** (`summary = "facts"`): deterministic stats from
   the grid — column count/row count, per-column numeric detection
   (decide the float/int parser for Cyrillic-formatted numbers:
   spaces as thousand separators, comma decimals), min/max/sum per
   numeric column with header names, first/last row mention. `none`
   emits nothing.
3. **KV embed form**: per data row — `Ключ: значение, Ключ: значение`
   lines keyed by the (possibly demoted) header, prefixed by context
   (title + caption) and suffixed by the summary. This text is the
   `embed` field verbatim.
4. **Row-split policy**: whole table while it fits `max_tokens` (the
   full hybrid block counts); over budget → row-split into parts, each
   part = context + duplicated header + its rows + local facts; parts
   share `table_id` (`{page}-t{n}`), carry `part`/`parts`;
   `metadata.table.header` repeated per part.

Blocked by: [TKT-RC-03](TKT-RC-03-chunks-contract.md).

## Resolution

Done 2026-09-14, in `src/pdf_router/renderers/chunks.py`:

- **Demotion predicate** (`_looks_like_caption_row`): row has ≥1
  content cell AND (exactly one content cell OR any content cell
  starts with a strong prefix `Таблица|Табл.|табл|Table`, case- and
  position-insensitive). `№` is deliberately NOT a prefix — it fronts
  real «№ п/п» headers; a lone «№ 4-2» cell demotes via the
  single-cell rule anyway. Rationale: the live Рыбы p.14 anchor glues
  «Таблица 2» INTO a cell of the scattered caption band
  (`'Таблица 2БассейнКубанн'`), so per-cell matching is required, not
  row-0-as-a-whole. Keys come from `rows[1]` (band = one row after
  demotion); no demotion → `header_rows` band as stored.
- **Facts summary** (`_facts_summary`): numeric-column detection via a
  Cyrillic-number parser (`_parse_num`: NBSP/space thousands, comma
  decimals, trailing `%`), column counts as numeric when ≥50% of its
  cells parse; per column `мин/макс/сумма` with the header label;
  first/last row quoted verbatim; deterministic formatting (`_fmt_num`).
- **KV embed** (`_kv_lines`): one line per data row —
  `Ключ: значение, ...` keyed by the (possibly multi-row `/`-joined)
  header band, positional `Колонка N` fallback when the band is
  unusable; prefixed by context (`embed_context` crumbs + caption),
  suffixed by the summary. This assembly is the `embed` field.
- **Row-split** (`_table_parts`): whole table while the whole embed
  fits `max_tokens`; else greedy row packing (rows atomic), every part
  repeating context + header band + LOCAL facts; parts share
  `{page}-t{n}` as `table_id` with `-{k:02d}` ids and `part`/`parts`
  metadata; `markdown` for a demoted-caption part is a rebuilt pipe
  table (`_rows_to_pipe`), for a whole table — `block.markdown`
  verbatim (IR doctrine).
