"""Entry point: any supported file in, a Card (front/back images) out."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from .card import Card
from .detect import find_cards
from .recipes import BY_NAME, inspect, pick


class PasswordRequired(Exception):
    pass


def open_pdf(path: Path, password: str | None) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path), password=password)
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise PasswordRequired(
                "PDF is password protected" if not password else "Wrong password") from e
        raise


def from_flat_image(img: Image.Image) -> Card:
    """Scans / image-only PDFs: detect the cards by shape."""
    boxes = find_cards(img)
    if not boxes:
        raise ValueError("No card found in image")
    sides = [img.crop(b) for b in boxes]
    return Card("scan", sides[0], sides[1] if len(sides) > 1 else None)


def convert(path: str | Path, password: str | None = None, doc_type: str = "auto",
            dpi: int = 300) -> Card:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return from_flat_image(Image.open(path).convert("RGB"))

    doc = open_pdf(path, password)
    info = inspect(doc)
    recipe = BY_NAME[doc_type] if doc_type != "auto" else pick(info)
    if recipe:
        return recipe.extract(info, dpi)
    # Unknown layout: render the first page and look for card shapes.
    scale = min(dpi / 72, 3500 / max(info.sizes[0]))
    return from_flat_image(doc[0].render(scale=scale).to_pil())
