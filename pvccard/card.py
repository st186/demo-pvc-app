"""CR80 (PVC card) geometry and final output writing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

CARD_W_MM = 85.6
CARD_H_MM = 53.98
CARD_ASPECT = CARD_W_MM / CARD_H_MM


def card_px(dpi: int) -> tuple[int, int]:
    return round(CARD_W_MM / 25.4 * dpi), round(CARD_H_MM / 25.4 * dpi)


@dataclass
class Card:
    doc_type: str
    front: Image.Image
    back: Image.Image | None


def fit_to_card(img: Image.Image, dpi: int, fit: str = "stretch") -> Image.Image:
    """Resize a cropped side to exact CR80 pixels.

    stretch: fill the card exactly (matches how print shops prepare these files)
    contain: keep proportions, pad with white
    """
    img = img.convert("RGB")
    if img.height > img.width:
        img = img.rotate(90, expand=True)
    size = card_px(dpi)
    if fit == "contain":
        return ImageOps.pad(img, size, Image.LANCZOS, color="white")
    return img.resize(size, Image.LANCZOS)


def save_card(card: Card, out_dir: Path, stem: str, dpi: int = 300,
              fit: str = "stretch", cmyk: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sides = [("front", card.front)] + ([("back", card.back)] if card.back else [])
    pages, written = [], []
    for name, img in sides:
        page = fit_to_card(img, dpi, fit)
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
