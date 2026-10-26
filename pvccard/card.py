"""CR80 (PVC card) geometry and final output writing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def round_corners(img: Image.Image, dpi: int, radius_mm: float = CORNER_RADIUS_MM) -> Image.Image:
    """Make the corners transparent, matching the physical card's rounding."""
    ss = 4  # supersample the mask for smooth edges
    r = round(radius_mm / 25.4 * dpi) * ss
    mask = Image.new("L", (img.width * ss, img.height * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), r, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask.resize(img.size, Image.LANCZOS))
    return out


def on_white(img: Image.Image) -> Image.Image:
    if img.mode != "RGBA":
        return img
    bg = Image.new("RGB", img.size, "white")
    bg.paste(img, mask=img.getchannel("A"))
    return bg


def save_card(card: Card, out_dir: Path, stem: str, dpi: int = DEFAULT_DPI,
              fit: str = "stretch", cmyk: bool = False, rounded: bool = True,
              sharpen: bool = True) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sides = [("front", card.front)] + ([("back", card.back)] if card.back else [])
    pages, written = [], []
    for name, img in sides:
        page = fit_to_card(img, dpi, fit, sharpen)
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
