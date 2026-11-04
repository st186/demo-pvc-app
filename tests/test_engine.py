"""Run with real downloads by pointing PVC_SAMPLES at a folder of them.

Real ID documents are never committed; sample-based tests skip without them.
"""

import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pvccard import convert, save_card
from pvccard.card import DEFAULT_DPI, card_px, round_corners
from pvccard.detect import find_cards

SAMPLES = Path(os.environ.get("PVC_SAMPLES", "samples"))

# samples/cases.json (git-ignored, holds real names/passwords):
# {"file.pdf": {"type": "aadhaar", "password": "ABCD1990"}, ...}
_manifest = SAMPLES / "cases.json"
CASES = json.loads(_manifest.read_text()) if _manifest.exists() else {}


def test_find_cards_side_by_side():
    img = Image.new("RGB", (2000, 1400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((100, 100, 900, 604), fill=(200, 60, 60))     # front
    d.rectangle((1000, 100, 1800, 604), fill=(60, 60, 200))   # back
    d.rectangle((100, 900, 1800, 1000), fill="black")         # not card-shaped
    found = find_cards(img)
    expected = [(100, 100, 901, 605), (1000, 100, 1801, 605)]
    assert len(found) == 2
    for box, want in zip(found, expected):
        assert all(abs(a - b) <= 4 for a, b in zip(box, want)), (box, want)


def test_round_corners():
    img = round_corners(Image.new("RGB", card_px(300), "red"), 300)
    assert img.getpixel((0, 0))[3] == 0          # corner cut away
    assert img.getpixel((505, 319))[3] == 255    # centre untouched
    assert img.getpixel((505, 0))[3] == 255      # straight edge untouched


def test_colour_background_keeps_ink():
    from pvccard.card import colour_background
    img = Image.new("RGB", (400, 250), "white")
    ImageDraw.Draw(img).rectangle((150, 100, 250, 150), fill="black")
    out = colour_background(img)
    assert out.getpixel((200, 125)) == (0, 0, 0)          # ink untouched
    assert out.getpixel((2, 2)) != (255, 255, 255)        # paper coloured
    assert out.getpixel((2, 2))[0] > out.getpixel((2, 2))[2]   # saffron at top-left


def test_card_px():
    assert card_px(300) == (1011, 638)
    assert card_px(600) == (2022, 1275)


@pytest.mark.parametrize("name", CASES)
def test_sample(name, tmp_path):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"sample {name} not present")
    case = CASES[name]
    card = convert(path, case.get("password"))
    assert card.doc_type == case["type"]
    assert card.back is not None
    files = save_card(card, tmp_path, "x")
    images = [f for f in files if f.suffix != ".pdf"]
    assert len(images) == 4  # front/back as PNG (transparent corners) and JPEG
    for f in images:
        assert Image.open(f).size == card_px(DEFAULT_DPI)


def _pdf_with_cards(card_bottom: float) -> bytes:
    """A4 e-Aadhaar-like page: letter art at the top, and front/back cut-out
    cards that share header and footer images, `card_bottom` pt from the bottom."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(595, 842)

    def place(img, l, b, r, t):
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        obj = pdfium.PdfImage.new(pdf)
        obj.load_jpeg(io.BytesIO(buf.getvalue()))
        obj.set_matrix(pdfium.PdfMatrix().scale(r - l, t - b).translate(l, b))
        page.insert_obj(obj)

    header = Image.new("RGB", (920, 106), (240, 140, 60))
    footer = Image.new("RGB", (1063, 99), (200, 30, 30))
    place(Image.new("RGB", (1063, 706), (220, 220, 220)), 32, 580, 292, 743)   # letter art
    for left in (33, 303):                                                       # front, back
        place(header, left, card_bottom + 136, left + 258, card_bottom + 161)
        place(footer, left, card_bottom, left + 259, card_bottom + 13)
    page.gen_content()
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


@pytest.mark.parametrize("card_bottom", [101, 40, 160])
def test_aadhaar_cards_found_wherever_they_sit(card_bottom):
    from pvccard.convert import Document
    import pypdfium2 as pdfium
    from pvccard.recipes import BY_NAME, inspect
    doc = Document(_pdf_with_cards(card_bottom), "a.pdf")
    front, back = BY_NAME["aadhaar"].boxes(inspect(doc.pdf))
    for (l, b, r, t), left in ((front, 33), (back, 303)):
        assert abs(l - left) < 1 and abs(b - card_bottom) < 1
        assert abs(t - (card_bottom + 161 + 1.2)) < 1
