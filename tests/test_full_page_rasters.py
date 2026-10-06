"""Полностраничные растры: фотография против пустого скана (тикет 04).

Правило `0.80` отбрасывало любой растр, закрывающий >= 80% страницы, как
«фон страницы, скан». Но полностраничная фотография неотличима от скана по
площади: обе занимают 100%. Проверено на `sources/3181.pdf` стр. 4 —
фотографии раковин, растр на 100% страницы, — уходили в никуда.

Различение делается по содержимому: доля пикселей, ушедших дальше порога
от СОБСТВЕННОГО фона страницы (медиана гистограммы), а не от белого. Скан
серый, бумага желтоватая — сравнивать с белым нельзя.

Замер на реальных страницах (рендер 50 dpi): фотография 37% содержимого,
серая заливка и пустой лист 4,7%, обычный текст 7,6%.

Второе правило: векторный проход отсекал контуры по АБСОЛЮТНОЙ площади
(500 pt² ~ 22x22 pt), и на странице с гистограммами (`sources/3155` стр. 74)
пропускал 34 фрагмента по 0,1% страницы — засечки осей. Теперь порог в доле
площади страницы; значение по умолчанию 0.001 эквивалентно прежним 500 pt²
на странице A4, поэтому поведение по умолчанию не меняется.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pdf_router.config import RouterConfig
from pdf_router.extract_images import (
    _page_ink_share,
    extract_page_images,
)
from pdf_router.ir import PageResult


def _pdf_with_text(tmp: Path, image: bytes, w: int, h: int) -> Path:
    """Страница с полностраничным растром И текстом поверх — как стр. 101
    `sources/3291`: список литературы в две колонки."""
    import fitz

    path = tmp / "withtext.pdf"
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    page.insert_image(fitz.Rect(0, 0, w, h), stream=image)
    for i in range(12):
        page.insert_text((40, 60 + i * 20), f"Reference line {i} of a bibliography",
                         fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _photo_png(w: int, h: int) -> bytes:
    """Полностраничное фото: светлый фон и крупные тёмные объекты.

    Однотонная заливка НЕ годится: у неё нет содержимого вовсе, и мера
    честно даёт 0%. Нужна тональная структура, как у настоящей фотографии.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (225, 220, 210))
    d = ImageDraw.Draw(img)
    for i in range(6):
        x = 40 + (i % 3) * (w // 3)
        y = 60 + (i // 3) * (h // 2)
        d.ellipse([x, y, x + w // 4, y + h // 5], fill=(45, 40, 35))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _blank_png(w: int, h: int) -> bytes:
    """Скан пустого листа: ровный фон плюс шум сжатия."""
    from PIL import Image

    img = Image.new("RGB", (w, h), (205, 205, 205))
    px = img.load()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            d = ((x * 7 + y * 13) % 21) - 10
            v = px[x, y][0] + d
            px[x, y] = (max(0, min(255, v)),) * 3
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf(tmp: Path, image: bytes, w: int, h: int) -> Path:
    """Страница 600x800 pt с картинкой на всю площадь."""
    import fitz

    path = tmp / "full.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_image(fitz.Rect(0, 0, 600, 800), stream=image)
    doc.save(str(path))
    doc.close()
    return path


def _extract(path: Path, out: Path, **kw):
    return extract_page_images(
        PageResult(page=0), str(path), out, **kw,
    )


# ── Признак содержимого ──────────────────────────────────────────────────

def test_ink_share_separates_photo_from_blank(tmp_path):
    """Фотография ≠ пустой скан по доле содержимого."""
    import fitz

    (tmp_path / "p").mkdir()
    (tmp_path / "b").mkdir()
    photo = _pdf(tmp_path / "p", _photo_png(600, 800), 600, 800)
    blank = _pdf(tmp_path / "b", _blank_png(600, 800), 600, 800)

    f_photo = _page_ink_share(fitz.open(str(photo))[0])
    f_blank = _page_ink_share(fitz.open(str(blank))[0])

    assert f_photo > 0.15, f"фотография должна быть богатой, а не {f_photo:.1%}"
    assert f_blank < 0.15, f"пустой скан должен быть бедным, а не {f_blank:.1%}"


# ── Правило 0.80 ────────────────────────────────────────────────────────

def test_full_page_raster_dropped_by_default(tmp_path):
    """Поведение по умолчанию прежнее: полностраничный растр отбрасывается."""
    pdf = _pdf(tmp_path, _photo_png(600, 800), 600, 800)
    out = tmp_path / "out"
    out.mkdir()
    saved, dropped = _extract(pdf, out, raster=True, vector=False)
    assert saved == []
    assert dropped == 1


def test_full_page_raster_kept_when_page_has_content(tmp_path):
    """С порогом содержимого фотография сохраняется."""
    pdf = _pdf(tmp_path, _photo_png(600, 800), 600, 800)
    out = tmp_path / "out"
    out.mkdir()
    saved, dropped = _extract(pdf, out, raster=True, vector=False,
                              full_page_min_ink=0.15)
    assert len(saved) == 1, "фотография на всю страницу должна сохраниться"
    assert dropped == 0


def test_full_page_raster_still_dropped_when_page_blank(tmp_path):
    """Пустой скан под порогом содержимого отбрасывается, даже с порогом."""
    pdf = _pdf(tmp_path, _blank_png(600, 800), 600, 800)
    out = tmp_path / "out"
    out.mkdir()
    saved, dropped = _extract(pdf, out, raster=True, vector=False,
                              full_page_min_ink=0.15)
    assert saved == [], "пустой лист не должен попадать в базу"
    assert dropped == 1


def test_full_page_raster_with_text_dropped(tmp_path):
    """Скан страницы с текстом — не иллюстрация, даже если содержимого много.

    `sources/3291` стр. 101: список литературы в две колонки даёт 16,5%
    содержимого, и первый вариант правила её принимал. Страница с текстом —
    это простая страница, а не рисунок.
    """
    pdf = _pdf_with_text(tmp_path, _photo_png(600, 800), 600, 800)
    out = tmp_path / "out"
    out.mkdir()
    saved, dropped = _extract(pdf, out, raster=True, vector=False,
                              full_page_min_ink=0.15)
    assert saved == [], "страница с текстом не должна сохраняться как иллюстрация"
    assert dropped == 1


# ── Порог векторного прохода ────────────────────────────────────────────

def test_vector_threshold_is_share_of_page(tmp_path):
    """Мелкие засечки осей отсекаются долей площади, а не абсолютной площадью."""
    import fitz

    path = tmp_path / "vec.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    # Много мелких прямоугольников (засечки осей) и один крупный.
    # 40 мелких прямоугольников между порогами: 40x30 pt = 0,25% страницы —
    # выше умолчания (0,1%) и ниже требуемых 5%.
    for i in range(40):
        x = 20 + (i % 8) * 70
        y = 40 + (i // 8) * 60
        page.draw_rect(fitz.Rect(x, y, x + 40, y + 30))
    page.draw_rect(fitz.Rect(100, 400, 500, 700))  # крупный контур 33%
    doc.save(str(path))
    doc.close()

    out_lo = tmp_path / "lo"
    out_lo.mkdir()
    out_hi = tmp_path / "hi"
    out_hi.mkdir()

    saved_lo, _ = _extract(path, out_lo, raster=False, vector=True,
                           vector_min_share=0.001)
    saved_hi, _ = _extract(path, out_hi, raster=False, vector=True,
                           vector_min_share=0.05)
    assert len(saved_lo) > len(saved_hi), "мелкий порог должен ловить больше"
    assert len(saved_hi) == 1, "при пороге 5% остаётся только крупный контур"


# ── Конфигурация ────────────────────────────────────────────────────────

def test_new_keys_exist_and_default_off():
    cfg = RouterConfig()
    assert cfg.routing.images_full_page_min_ink == 0.0
    assert cfg.routing.images_vector_min_share == pytest.approx(0.001)
