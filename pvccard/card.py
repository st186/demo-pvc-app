"""CR80 (PVC card) geometry and final output writing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

CARD_W_MM = 85.6
CARD_H_MM = 53.98
CARD_ASPECT = CARD_W_MM / CARD_H_MM
CORNER_RADIUS_MM = 3.18  # ISO/IEC 7810 ID-1
DEFAULT_DPI = 600  # HD: text and lines in the PDFs stay vector-sharp at this size


def card_px(dpi: int) -> tuple[int, int]:
    return round(CARD_W_MM / 25.4 * dpi), round(CARD_H_MM / 25.4 * dpi)


@dataclass
class Card:
    doc_type: str
    front: Image.Image
    back: Image.Image | None


def fit_to_card(img: Image.Image, dpi: int, fit: str = "stretch",
                sharpen: bool = False) -> Image.Image:
    """Resize a cropped side to exact CR80 pixels.

    stretch: fill the card exactly (matches how print shops prepare these files)
    contain: keep proportions, pad with white
    sharpen: light unsharp mask, mainly helps low-resolution photos and scans
    """
    img = img.convert("RGB")
    if img.height > img.width:
        img = img.rotate(90, expand=True)
    size = card_px(dpi)
    if fit == "contain":
        img = ImageOps.pad(img, size, Image.LANCZOS, color="white")
    else:
        img = img.resize(size, Image.LANCZOS)
    if sharpen:
        img = img.filter(ImageFilter.UnsharpMask(radius=dpi / 300, percent=60, threshold=2))
    return img


def colour_background(img: Image.Image) -> Image.Image:
    """Give the white areas of a card a soft saffron-to-green background.

    Only near-white pixels take the colour, so text, numbers, photo and QR
    modules keep their original ink. Decorative only: it does not imitate
    any official security design.
    """
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = a.shape[:2]
    t = np.clip(0.6 * np.linspace(0, 1, h)[:, None] + 0.4 * np.linspace(0, 1, w)[None, :], 0, 1)[..., None]
    saffron, white, green = (np.array(c, np.float32) for c in ((255, 226, 196), (255, 252, 246), (214, 240, 214)))
    bg = np.where(t < 0.5, saffron + (white - saffron) * (t / 0.5), white + (green - white) * ((t - 0.5) / 0.5))
    k = np.clip((a.mean(2, keepdims=True) - 215) / 35, 0, 1)  # 0 for ink, 1 for paper white
    out = a * (1 - k) + a * bg / 255 * k
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def round_corners(img: Image.Image, dpi: int, radius_mm: float = CORNER_RADIUS_MM) -> Image.Image:
    """Make the corners transparent, matching the physical card's rounding."""
    r = round(radius_mm / 25.4 * dpi)
    ss = 4  # supersample the corner for smooth edges
    big = Image.new("L", (r * ss * 2, r * ss * 2), 0)
    ImageDraw.Draw(big).ellipse((0, 0, big.width - 1, big.height - 1), fill=255)
    corner = big.resize((r * 2, r * 2), Image.LANCZOS).crop((0, 0, r, r))  # top-left quarter
    mask = Image.new("L", img.size, 255)
    w, h = img.size
    mask.paste(corner, (0, 0))
    mask.paste(corner.transpose(Image.FLIP_LEFT_RIGHT), (w - r, 0))
    mask.paste(corner.transpose(Image.FLIP_TOP_BOTTOM), (0, h - r))
    mask.paste(corner.transpose(Image.ROTATE_180), (w - r, h - r))
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def on_white(img: Image.Image) -> Image.Image:
    if img.mode != "RGBA":
        return img
    bg = Image.new("RGB", img.size, "white")
    bg.paste(img, mask=img.getchannel("A"))
    return bg


def save_card(card: Card, out_dir: Path, stem: str, dpi: int = DEFAULT_DPI,
              fit: str = "stretch", cmyk: bool = False, rounded: bool = True,
              sharpen: bool = True, colour: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sides = [("front", card.front)] + ([("back", card.back)] if card.back else [])
    pages, written = [], []
    for name, img in sides:
        page = fit_to_card(img, dpi, fit, sharpen)
        if colour:
            page = colour_background(page)
        if rounded:
            page = round_corners(page, dpi)
            png = out_dir / f"{stem}_{name}.png"
            page.save(png, dpi=(dpi, dpi))
            written.append(png)
            page = on_white(page)
        pages.append(page)
        out = page.convert("CMYK") if cmyk else page
        path = out_dir / f"{stem}_{name}.jpg"
        out.save(path, "JPEG", quality=95, dpi=(dpi, dpi), subsampling=0)
        written.append(path)
    pdf_path = out_dir / f"{stem}_card.pdf"
    pages[0].save(pdf_path, "PDF", resolution=dpi, save_all=True,
                  append_images=pages[1:])
    written.append(pdf_path)
    return written
