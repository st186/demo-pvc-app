"""Open a document, suggest front/back crop boxes, and crop them.

Crop boxes are page fractions (x0, y0, x1, y1) with the origin at the
top-left, so the web workspace and the PDF renderer share one coordinate
system regardless of page size or units.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from .card import DEFAULT_DPI, Card, card_px
from .detect import find_cards
from .recipes import BY_NAME, Box, inspect, pick

Frac = tuple[float, float, float, float]
FULL: Frac = (0.0, 0.0, 1.0, 1.0)

# Render at ~2x the final pixel size, then downsample for sharp text.
OVERSAMPLE = 2
MAX_RENDER_PX = 40_000_000


class PasswordRequired(Exception):
    pass


@dataclass
class Side:
    page: int
    box: Frac


@dataclass
class Suggestion:
    doc_type: str
    label: str
    front: Side
    back: Side | None


def open_pdf(src: Path | bytes, password: str | None) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(src if isinstance(src, bytes) else str(src), password=password)
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise PasswordRequired(
                "PDF is password protected" if not password else "Wrong password") from e
        raise


def _to_frac(box: Box | None, size: tuple[float, float]) -> Frac:
    if box is None:
        return FULL
    l, b, r, t = box
    W, H = size
    return (l / W, (H - t) / H, r / W, (H - b) / H)


class Document:
    def __init__(self, data: bytes, filename: str = "", password: str | None = None):
        self.is_pdf = data[:5] == b"%PDF-" or filename.lower().endswith(".pdf")
        if self.is_pdf:
            self.pdf = open_pdf(data, password)
            self.sizes = [p.get_size() for p in self.pdf]
        else:
            self.image = Image.open(io.BytesIO(data)).convert("RGB")
            self.sizes = [self.image.size]

    @classmethod
    def from_path(cls, path: str | Path, password: str | None = None) -> "Document":
        path = Path(path)
        return cls(path.read_bytes(), path.name, password)

    @property
    def page_count(self) -> int:
        return len(self.sizes)

    def preview(self, page: int = 0, max_px: int = 1400) -> Image.Image:
        """Whole page, scaled so its longer side is `max_px`."""
        if not self.is_pdf:
            img = self.image.copy()
            img.thumbnail((max_px, max_px))
            return img
        W, H = self.sizes[page]
        return self.pdf[page].render(scale=max_px / max(W, H)).to_pil().convert("RGB")

    def suggest(self, doc_type: str = "auto") -> Suggestion:
        if self.is_pdf:
            info = inspect(self.pdf)
            recipe = BY_NAME[doc_type] if doc_type != "auto" else pick(info)
            if recipe:
                return Suggestion(
                    recipe.name, recipe.label,
                    Side(recipe.front_page, _to_frac(recipe.front, self.sizes[recipe.front_page])),
                    Side(recipe.back_page, _to_frac(recipe.back, self.sizes[recipe.back_page])))
            page_img = self.preview(0, 3000)
        else:
            page_img = self.image
        # Unknown layout or a plain image: look for card shapes.
        w, h = page_img.size
        sides = [Side(0, (x0 / w, y0 / h, x1 / w, y1 / h)) for x0, y0, x1, y1 in find_cards(page_img)]
        if not sides:
            return Suggestion("manual", "Unknown document", Side(0, FULL), None)
        return Suggestion("scan", "Scanned document", sides[0], sides[1] if len(sides) > 1 else None)

    def crop(self, side: Side, dpi: int = DEFAULT_DPI) -> Image.Image:
        x0, y0, x1, y1 = side.box
        if not self.is_pdf:
            w, h = self.image.size
            return self.image.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))

        page = self.pdf[side.page]
        W, H = self.sizes[side.page]
        bw, bh = (x1 - x0) * W, (y1 - y0) * H
        scale = min(card_px(dpi)[0] * OVERSAMPLE / max(bw, bh), 12)
        if page.get_rotation() == 0:
            return page.render(scale=scale, crop=(x0 * W, (1 - y1) * H, (1 - x1) * W, y0 * H)).to_pil()
        # Rotated pages: render whole page, then cut pixels (avoids rotated crop maths).
        scale = min(scale, (MAX_RENDER_PX / (W * H)) ** 0.5)
        img = page.render(scale=scale).to_pil()
        w, h = img.size
        return img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))


def convert(path: str | Path, password: str | None = None, doc_type: str = "auto",
            dpi: int = DEFAULT_DPI, data: bytes | None = None) -> Card:
    """Auto-crop a file on disk, or in-memory `data` (then `path` only names it)."""
    doc = Document(data, str(path), password) if data else Document.from_path(path, password)
    s = doc.suggest(doc_type)
    return Card(s.doc_type, doc.crop(s.front, dpi), doc.crop(s.back, dpi) if s.back else None)
