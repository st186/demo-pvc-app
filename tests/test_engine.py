"""Run with real downloads by pointing PVC_SAMPLES at a folder of them.

Real ID documents are never committed; sample-based tests skip without them.
"""

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pvccard import convert, save_card
from pvccard.card import card_px, round_corners
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
    assert find_cards(img) == [(100, 100, 901, 605), (1000, 100, 1801, 605)]


def test_round_corners():
    img = round_corners(Image.new("RGB", card_px(300), "red"), 300)
    assert img.getpixel((0, 0))[3] == 0          # corner cut away
    assert img.getpixel((505, 319))[3] == 255    # centre untouched
    assert img.getpixel((505, 0))[3] == 255      # straight edge untouched


def test_card_px():
    assert card_px(300) == (1011, 638)


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
        assert Image.open(f).size == (1011, 638)
