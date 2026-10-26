"""Per-document crop recipes.

Regions are PDF points (left, bottom, right, top), origin at the page's
bottom-left, measured from the official downloads. Rendering the region
(rather than pulling out embedded images) keeps text that is drawn on top
of the card background, e.g. the WB e-Ration card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import pypdfium2 as pdfium
from PIL import Image

from .card import Card, card_px

Box = tuple[float, float, float, float]

# Render at ~2x the final pixel size, then downsample for sharp text.
OVERSAMPLE = 2


@dataclass
class PdfInfo:
    doc: pdfium.PdfDocument
    text: str
    sizes: list[tuple[float, float]]
    images: list[tuple[int, int, Box]] = field(default_factory=list)  # page 0: px w, px h, bounds


def inspect(doc: pdfium.PdfDocument) -> PdfInfo:
    page = doc[0]
    text = " ".join(page.get_textpage().get_text_range().split())
    images = []
    for obj in page.get_objects(max_depth=3):
        if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
            w, h = obj.get_px_size()
            images.append((w, h, tuple(obj.get_bounds())))
    return PdfInfo(doc, text, [p.get_size() for p in doc], images)


def render_region(doc: pdfium.PdfDocument, page_no: int, box: Box | None, dpi: int) -> Image.Image:
    page = doc[page_no]
    W, H = page.get_size()
    l, b, r, t = box or (0, 0, W, H)
    target_w = card_px(dpi)[0] * OVERSAMPLE
    scale = min(target_w / max(r - l, t - b), 12)
    return page.render(scale=scale, crop=(l, b, W - r, H - t)).to_pil()


def _near(size: tuple[float, float], w: float, h: float, tol: float = 2) -> bool:
    return abs(size[0] - w) <= tol and abs(size[1] - h) <= tol


@dataclass
class Recipe:
    name: str
    label: str
    match: Callable[[PdfInfo], bool]
    front: Box | None = None
    back: Box | None = None
    front_page: int = 0
    back_page: int = 0

    def extract(self, info: PdfInfo, dpi: int) -> Card:
        front = render_region(info.doc, self.front_page, self.front, dpi)
        back = render_region(info.doc, self.back_page, self.back, dpi)
        return Card(self.name, front, back)


PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

RECIPES: list[Recipe] = [
    # Already card-shaped: page 1 front, page 2 back (rotated 90 in the file).
    Recipe("ayushman", "Ayushman Card (PM-JAY)",
           match=lambda i: len(i.sizes) == 2 and _near(i.sizes[0], 306, 154, 6),
           front_page=0, back_page=1),
    Recipe("aadhaar", "e-Aadhaar",
           match=lambda i: "Enrolment No" in i.text or "VID :" in i.text,
           front=(33.0, 101.5, 291.5, 264.0), back=(303.0, 101.5, 561.5, 264.0)),
    Recipe("pan", "e-PAN",
           match=lambda i: "ePAN" in i.text and bool(PAN_RE.search(i.text)),
           front=(68.3, 44.0, 302.7, 191.0), back=(309.0, 44.0, 542.7, 191.0)),
    Recipe("ration_wb", "West Bengal e-Ration Card",
           match=lambda i: "food.wb.gov.in" in i.text,
           front=(45.0, 25.0, 297.8, 183.4), back=(300.0, 25.0, 552.8, 183.4)),
    Recipe("epic", "Voter ID (e-EPIC)",
           match=lambda i: "ELECTION COMMISSION OF INDIA" in i.text and "Epic no" in i.text,
           front=(32.5, 593.0, 277.5, 747.0), back=(327.0, 593.0, 572.0, 747.0)),
    Recipe("abha", "ABHA Card",
           match=lambda i: "@abdm" in i.text and _near(i.sizes[0], 465, 576, 6),
           front=(9.9, 292.9, 455.1, 566.1), back=(9.9, 9.9, 455.1, 283.1)),
    # WB driving licence: image-only A4 with front/back as two card images.
    Recipe("dl_wb", "Driving Licence (WB)",
           match=lambda i: _near(i.sizes[0], 2480, 3508, 10)
           and sum(1 for w, h, _ in i.images if (w, h) == (1020, 642)) == 2,
           front=(204.0, 2810.0, 1220.0, 3448.0), back=(1260.0, 2810.0, 2276.0, 3448.0)),
]

BY_NAME = {r.name: r for r in RECIPES}


def pick(info: PdfInfo) -> Recipe | None:
    return next((r for r in RECIPES if r.match(info)), None)
