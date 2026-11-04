"""Per-document crop recipes.

Regions are PDF points (left, bottom, right, top), origin at the page's
bottom-left, measured from the official downloads. A region of None means
the whole page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import pypdfium2 as pdfium

Box = tuple[float, float, float, float]


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


def _near(size: tuple[float, float], w: float, h: float, tol: float = 2) -> bool:
    return abs(size[0] - w) <= tol and abs(size[1] - h) <= tol


@dataclass
class Recipe:
    name: str
    label: str
    description: str
    match: Callable[[PdfInfo], bool]
    front: Box | None = None
    back: Box | None = None
    front_page: int = 0
    back_page: int = 0
    # Finds (front, back) on this particular file, overriding the fixed boxes
    # (which stay as the fallback) when the layout moves between versions.
    locate: Callable[[PdfInfo], tuple[Box, Box] | None] | None = None

    def boxes(self, info: PdfInfo) -> tuple[Box | None, Box | None]:
        found = self.locate(info) if self.locate else None
        return found or (self.front, self.back)


def _twin_cards(info: PdfInfo) -> tuple[Box, Box] | None:
    """Cut-out front/back cards that share artwork (e-Aadhaar): the same
    header and footer images appear twice, side by side at the same height.
    Each card is the area those images span in its half of the page."""
    W, H = info.sizes[0]
    # Banners and strips, not dotted cut lines or the page-wide letter art.
    art = [(w, h, b) for w, h, b in info.images if b[3] - b[1] >= 8 and b[2] - b[0] >= 0.3 * W / 2]
    left, right = [], []
    for w, h, a in art:
        for w2, h2, c in art:
            if (w, h) == (w2, h2) and a[2] <= W / 2 + 6 and c[0] >= W / 2 - 6 and abs(a[1] - c[1]) < 2:
                left.append(a)
                right.append(c)
    if len(left) < 2:
        return None

    def span(bs: list[Box]) -> Box:
        l, b = min(x[0] for x in bs), min(x[1] for x in bs)
        r, t = max(x[2] for x in bs), max(x[3] for x in bs)
        return (l, b, r, t + 1.2)  # the card edge sits just above the header art

    front, back = span(left), span(right)
    # A card is landscape; anything else means these weren't the cards.
    for l, b, r, t in (front, back):
        if not 1.3 < (r - l) / max(1, t - b) < 1.9:
            return None
    return front, back


PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

RECIPES: list[Recipe] = [
    # Already card-shaped: page 1 front, page 2 back (rotated 90 in the file).
    Recipe("ayushman", "Ayushman Card (PM-JAY)", "NHA card PDF, page 1 front, page 2 back",
           match=lambda i: len(i.sizes) == 2 and _near(i.sizes[0], 306, 154, 6),
           front_page=0, back_page=1),
    Recipe("aadhaar", "e-Aadhaar", "UIDAI e-Aadhaar cut card strip",
           match=lambda i: "Enrolment No" in i.text or "VID :" in i.text,
           front=(33.0, 101.5, 291.5, 264.0), back=(303.0, 101.5, 561.5, 264.0),
           locate=_twin_cards),
    Recipe("pan", "e-PAN", "Income Tax e-PAN cut card section",
           match=lambda i: "ePAN" in i.text and bool(PAN_RE.search(i.text)),
           front=(68.3, 44.0, 302.7, 191.0), back=(309.0, 44.0, 542.7, 191.0)),
    Recipe("ration_wb", "West Bengal e-Ration Card", "WB Food & Supplies digital ration card",
           match=lambda i: "food.wb.gov.in" in i.text,
           front=(45.0, 25.0, 297.8, 183.4), back=(300.0, 25.0, 552.8, 183.4)),
    Recipe("epic", "Voter ID (e-EPIC)", "ECI e-EPIC download, card pair at top",
           match=lambda i: "ELECTION COMMISSION OF INDIA" in i.text and "Epic no" in i.text,
           front=(32.5, 593.0, 277.5, 747.0), back=(327.0, 593.0, 572.0, 747.0)),
    Recipe("abha", "ABHA Card", "ABDM health account card, front over back",
           match=lambda i: "@abdm" in i.text and _near(i.sizes[0], 465, 576, 6),
           # Inside the thin grey frame drawn around each card image.
           front=(11.0, 294.0, 454.0, 565.0), back=(11.0, 11.0, 454.0, 283.0)),
    # WB driving licence: image-only A4 with front/back as two card images.
    Recipe("dl_wb", "Driving Licence (WB)", "Parivahan DL PDF, card pair at top",
           match=lambda i: _near(i.sizes[0], 2480, 3508, 10)
           and sum(1 for w, h, _ in i.images if (w, h) == (1020, 642)) == 2,
           front=(204.0, 2810.0, 1220.0, 3448.0), back=(1260.0, 2810.0, 2276.0, 3448.0)),
]

BY_NAME = {r.name: r for r in RECIPES}


def pick(info: PdfInfo) -> Recipe | None:
    return next((r for r in RECIPES if r.match(info)), None)
