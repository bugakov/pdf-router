"""Image extraction from PDF pages.

One entry point — ``extract_page_images`` — no class hierarchy:
- raster pass: Image XObjects, filters page-background (area >= 80%).
- vector pass: vector paths rendered to PNG.

The PDF is opened once per page regardless of how many passes run
(previously up to 3 opens: 1 raster + 2 vector — measurable on large
engineering PDFs, where a single open costs ~100 ms per 20 MB).

Image paths follow the sidecar schema from TKT-RT-12:
  <output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.png
where doc-id = SHA-256 of the absolute PDF path (12 hex chars).
Paths stored in PageResult.images are relative to output_dir.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pdf_router.ir import PageResult

if TYPE_CHECKING:
    import pymupdf

logger = logging.getLogger("pdf_router")


# Minimum bounding-rect area (pt²) for a vector diagram to be saved.
# Engineering drawings (ТКР1) have many tiny decorative paths; this
# threshold filters them out. Calibrated against big_test.pdf pages 26-44.
#: Калибровка litdb (тикет text-quality/04). Пороги живут здесь, потому что
#: приёмка измерялась на файлах этого проекта, а не внутри библиотеки.
#:
#: Полностраничный растр сохраняется, если содержимого поверх собственного
#: фона страницы не меньше этой доли И текста на странице нет. Замер:
#: фотография раковин `sources/3181` — 37%, серая заливка `sources/4141` —
#: 4,7%, пустой лист — 5,2%. Разрыв между фотографией и всем остальным
#: широкий, порог взят с запасом от 15%.
FULL_PAGE_MIN_INK = 0.15
#: Минимальная доля площади страницы для векторного контура при 5%: засечки
#: осей гистограмм (0,1% на стр. 74 `sources/3155`) отсекаются, схема
#: жизненного цикла (28%) проходит.
VECTOR_MIN_SHARE = 0.05

#: Минимальная доля площади страницы для векторного контура. 0.001 — это
#: прежний абсолютный порог 500 pt² на странице A4, то есть поведение по
#: умолчанию не меняется; litdb поднимает до 0.05, иначе засечки осей
#: гистограмм становятся картинками.
_DEFAULT_VECTOR_MIN_SHARE = 0.001

#: Drop a raster when BOTH sides are below this (icons, thumbnails,
#: bullets) — calibrated on the S3 corpus 2026-10-01 (300+300 sampled
#: images, boundary cases inspected visually). Zero disables the rule.
#: The production consumer (litdb parse_worker) imports these as the
#: calibrated values; the function/config defaults stay 0 = off.
MIN_PHOTO_SIZE = 150

#: Drop a raster when its THIN side is below this (separator rules,
#: frames) — same calibration. Zero disables the rule.
MIN_PHOTO_THIN = 8


def keep_raster_photo(w: int, h: int, *, min_size: int = 0, min_thin: int = 0) -> bool:
    """True if a raster of ``w``×``h`` px is worth extracting.

    Pure predicate (no IO): the TDD seam for the size gate. The rule is
    exactly "both sides small" OR "thin side sliver" — deliberately no
    degenerate-size special case, so with both thresholds at 0 the predicate
    keeps everything (library default, no behaviour change for existing
    consumers unless they opt in). Doubtful content (narrow charts,
    panoramas, tall strips) is kept; only proven junk goes.
    """
    if min_size > 0 and w < min_size and h < min_size:
        return False
    if min_thin > 0 and min(w, h) < min_thin:
        return False
    return True


def doc_id_from_path(pdf_path: str) -> str:
    """12-char hex SHA-256 of the absolute PDF path — stable across runs."""
    abs_path = str(Path(pdf_path).resolve())
    return hashlib.sha256(abs_path.encode()).hexdigest()[:12]


def page_images_dir(output_dir: Path, doc_id: str) -> Path:
    """Sidecar images directory for one document."""
    d = output_dir / doc_id / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_page_images(
    page: PageResult,
    pdf_path: str,
    output_dir: Path,
    *,
    raster: bool,
    vector: bool,
    min_size: int = 0,
    min_thin: int = 0,
    full_page_min_ink: float = 0.0,
    vector_min_share: float = _DEFAULT_VECTOR_MIN_SHARE,
) -> tuple[list[str], int]:
    """Extract raster photos and/or vector diagrams from one page.

    Opens the PDF once and runs the requested passes against that single
    handle. Returns ``(saved, dropped)``: relative paths from ``output_dir``
    (e.g. ``abcdef123456/images/page0001_photo_00.png``) plus the count of
    rasters refused by the size gate. ``([], 0)`` when both flags are off
    or the page yields nothing.
    """
    if not raster and not vector:
        return [], 0

    import pymupdf

    did = doc_id_from_path(pdf_path)
    img_dir = page_images_dir(output_dir, did)

    saved: list[str] = []
    dropped = 0
    doc = pymupdf.open(pdf_path)
    try:
        pymupdf_page = doc[page.page]
        if pymupdf_page is None:
            return [], 0
        if raster:
            s, d = _extract_raster_photos(
                doc, pymupdf_page, page, output_dir, img_dir,
                min_size=min_size, min_thin=min_thin,
                full_page_min_ink=full_page_min_ink,
            )
            saved.extend(s)
            dropped += d
        if vector:
            saved.extend(
                _extract_vector_diagrams(pymupdf_page, page, output_dir, img_dir,
                                        min_share=vector_min_share)
            )
    finally:
        doc.close()

    return saved, dropped


# ---------------------------------------------------------------------------
# Признак содержимого страницы
# ---------------------------------------------------------------------------

#: Гистограмма считается в этом числе уровней серого на всю страницу.
_INK_BINS = 256
#: Ширина окна для гистограммы — уровней от фона, дальше пиксель не «чернила».
_INK_DPI = 50
#: Абсолютный минимум отклонения: у идеально ровного листа разброс нулевой,
#: и без пола шум сжатия (десятки уровней) сошёл бы за содержимое.
_INK_ABS_FLOOR = 32


def _page_ink_share(pymupdf_page: pymupdf.Page, *, dpi: int = _INK_DPI,
                    abs_floor: int = _INK_ABS_FLOOR) -> float:
    """Доля пикселей страницы, ушедших далеко от ЕЁ СОБСТВЕННОГО фона.

    Сравнивать с белым нельзя: скан серый, бумага желтоватая, а полностраничная
    заливка — вообще однородный тон. Фон берётся медианой гистограммы, поэтому
    измеряется не «отличие от белого», а «содержимое поверх фона».

    Замер на реальных страницах (`sources/3181` стр. 4 — фотографии раковин,
    `sources/4141` стр. 3–4 — серая заливка и пустой лист): фотография 37%
    содержимого, заливка и пустой лист 4,7%, обычный текст 7,6%. Значит
    порог ~15% различает фотографию и всё остальное.

    Считается по гистограмме, без numpy/PIL — их в зависимостях нет.
    Тонкие элементы (подписи осей, колонтитулы) при низком dpi в меру не
    попадают: для векторной графики этот признак не применяется.
    """
    import pymupdf

    pix = pymupdf_page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    hist = [0] * _INK_BINS
    for value in pix.samples:
        hist[value] += 1
    total = sum(hist)
    if not total:
        return 0.0

    half = total // 2
    cum = 0
    background = 0
    for v in range(_INK_BINS):
        cum += hist[v]
        if cum > half:
            background = v
            break

    lo = background - abs_floor
    hi = background + abs_floor + 1
    ink = sum(hist[:max(0, lo)]) + sum(hist[min(_INK_BINS, hi):])
    return ink / total


# ---------------------------------------------------------------------------
# Raster photos
# ---------------------------------------------------------------------------


def _extract_raster_photos(
    doc: pymupdf.Document,
    pymupdf_page: pymupdf.Page,
    page: PageResult,
    output_dir: Path,
    img_dir: Path,
    *,
    min_size: int = 0,
    min_thin: int = 0,
    full_page_min_ink: float = 0.0,
) -> tuple[list[str], int]:
    """Extract embedded raster images (JPEG, PNG, etc.) from an open page.

    Returns ``(saved, dropped)``. ``[]`` (and 0) if the page has no images,
    or if the only image covers >= 80% of the page area (page-background
    scan — not useful for RAG). Rasters refused by the size gate
    (``keep_raster_photo``) are counted in ``dropped``, never written.
    The raster is decoded via PyMuPDF's ``Document.extract_image(xref)``.
    """
    raw_images = pymupdf_page.get_images(full=True)
    if not raw_images:
        return [], 0

    page_area = pymupdf_page.rect.width * pymupdf_page.rect.height

    saved: list[str] = []
    dropped = 0
    photo_count = 0

    for xref, _smask, img_width, img_height, *_ in raw_images:
        # NB: get_images(full=True) yields
        # (xref, smask, width, height, bpc, colorspace, alt, name, filter) —
        # skipping smask is load-bearing: reading rest[0]/rest[1] as dims
        # silently compared (smask × width) to the page area and fed the
        # size gate garbage (smask is 0 for most images, i.e. width 0).

        # Filter: page-background scan (>= 80% page area). A full-page
        # photograph is indistinguishable from a page scan by area alone —
        # both cover everything — so with full_page_min_ink > 0 the page is
        # measured instead of trusted: ink over its own background separates
        # a photograph (37%) from a blank scan or a flat fill (4.7%).
        # Counted as dropped whichever way it goes, so images_dropped stays
        # an honest count of refused rasters.
        if img_width * img_height >= 0.80 * page_area:
            # Полностраничный растр оправдан только если это ИЛЛЮСТРАЦИЯ,
            # а не скан. Отличать приходится двумя вопросами, и оба нужны:
            #   1) содержимое выше порога — иначе перед нами пустой лист или
            #      серая заливка (`sources/4141` стр. 3-4: 4,7-5,2%);
            #   2) на странице нет текста — иначе это простая страница с
            #      текстом, а не рисунок (`sources/3291` стр. 101 — список
            #      литературы в две колонки, 16,5% содержимого, текст есть).
            if full_page_min_ink <= 0.0:
                dropped += 1
                continue
            if _page_ink_share(pymupdf_page) < full_page_min_ink:
                dropped += 1
                continue
            if pymupdf_page.get_text().strip():
                dropped += 1
                continue

        # Filter: size gate — icons, thumbnails, separator rules.
        if not keep_raster_photo(
            img_width, img_height, min_size=min_size, min_thin=min_thin
        ):
            dropped += 1
            continue

        try:
            pix = doc.extract_image(xref)
        except Exception:
            continue

        if not pix:
            continue

        ext = pix.get("ext", "png")
        ext = "png" if ext.lower() in {"jpeg", "jpg", "jpx", "jp2"} else ext
        fname = f"page{page.page + 1:04d}_photo_{photo_count:02d}.{ext}"
        fpath = img_dir / fname
        fpath.write_bytes(pix["image"])  # pix["image"] is bytes

        # Store relative path from output_dir.
        saved.append(str(fpath.relative_to(output_dir)))
        photo_count += 1

    return saved, dropped


# ---------------------------------------------------------------------------
# Vector diagrams
# ---------------------------------------------------------------------------


def _extract_vector_diagrams(
    pymupdf_page: pymupdf.Page,
    page: PageResult,
    output_dir: Path,
    img_dir: Path,
    *,
    min_share: float = _DEFAULT_VECTOR_MIN_SHARE,
) -> list[str]:
    """Extract vector drawings from an open page, rendered as PNG.

    Each drawing whose bounding rect covers at least ``min_share`` of the
    page is saved as a page-scale PNG showing the vector paths on white.

    The threshold is a SHARE of the page, not an absolute area: an absolute
    floor of 500 pt² let 34 tick-mark fragments through on a single page of
    histograms (`sources/3155` стр. 74), each covering 0.1% of the page. The
    default 0.001 reproduces the old behaviour on A4 (500 pt² ≈ 0.1%), so
    nothing changes unless a caller asks for it.
    """
    import pymupdf

    drawings = pymupdf_page.get_drawings()
    if not drawings:
        return []

    def _area(r: pymupdf.Rect) -> float:
        # NB: pymupdf.Rect has no `.area` attribute — width*height is the
        # stable arithmetic (a latent AttributeError hid here while the
        # vector pass was default-off and untested with real drawings).
        return abs((r.x1 - r.x0) * (r.y1 - r.y0))

    # Filter: keep only drawings covering a meaningful share of the page.
    # Normalize first — get_drawings can emit inverted rects (x1 < x0),
    # whose abs()-area passes the threshold but breaks pixmap rendering.
    # Then clip to the page: drawings may extend past the page bounds,
    # and rendering an out-of-page clip fails (FzErrorArgument).
    page_rect = pymupdf_page.rect
    page_area = page_rect.width * page_rect.height
    min_area = max(min_share, 0.0) * page_area
    large: list[pymupdf.Rect] = []
    for d in drawings:
        rect = d.get("rect") or pymupdf.Rect()
        rect.normalize()
        rect &= page_rect
        if not rect.is_empty and _area(rect) >= min_area:
            large.append(rect)
    if not large:
        return []

    saved: list[str] = []
    diagram_count = 0

    zoom = 200 / 72.0  # 200 DPI
    mat = pymupdf.Matrix(zoom, zoom)

    for rect in large:
        fname = f"page{page.page + 1:04d}_diagram_{diagram_count:02d}.png"
        fpath = img_dir / fname
        try:
            pix = pymupdf_page.get_pixmap(matrix=mat, clip=rect, alpha=False)
            pix.save(str(fpath))
        except Exception as exc:
            # One unrenderable clip must not fail the page's extraction
            # (core counts a raised error in pages_image_failed).
            logger.warning(
                "page %d: diagram clip %s not rendered: %s", page.page + 1, rect, exc
            )
            continue

        saved.append(str(fpath.relative_to(output_dir)))
        diagram_count += 1

    return saved
