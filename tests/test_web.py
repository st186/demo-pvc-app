import base64
import io
import json
import zipfile

import pytest
from PIL import Image, ImageDraw

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pvccard.card import card_px  # noqa: E402
from pvccard.web import app  # noqa: E402

client = TestClient(app)


def scan_png() -> bytes:
    """A fake scan: two card-shaped blocks side by side on white."""
    img = Image.new("RGB", (2000, 1400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((100, 100, 900, 604), fill=(200, 60, 60))
    d.rectangle((1000, 100, 1800, 604), fill=(60, 60, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def png_bytes(color: str, dpi: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", card_px(dpi), color).save(buf, "PNG")
    return buf.getvalue()


def test_index_and_types():
    assert client.get("/").status_code == 200
    ids = [t["id"] for t in client.get("/api/types").json()]
    assert {"aadhaar", "pan", "ayushman", "epic"} <= set(ids)


def test_open_then_crop_scan():
    files = {"file": ("scan.png", scan_png(), "image/png")}
    opened = client.post("/api/open", files=files).json()
    assert opened["type"] == "scan"
    assert len(opened["pages"]) == 1
    assert opened["front"]["box"][0] < opened["back"]["box"][0]  # front is the left card

    files = {"file": ("scan.png", scan_png(), "image/png")}
    data = {"front": json.dumps(opened["front"]), "back": json.dumps(opened["back"])}
    cropped = client.post("/api/crop", files=files, data=data).json()
    for side in ("front", "back"):
        img = Image.open(io.BytesIO(base64.b64decode(cropped[side].split(",")[1])))
        assert img.size == card_px(600)  # HD by default
        assert img.format == "JPEG"


def test_crop_standard_quality():
    opened = client.post("/api/open", files={"file": ("scan.png", scan_png(), "image/png")}).json()
    r = client.post("/api/crop", files={"file": ("scan.png", scan_png(), "image/png")},
                    data={"front": json.dumps(opened["front"]), "dpi": "300", "sharpen": "false"})
    img = Image.open(io.BytesIO(base64.b64decode(r.json()["front"].split(",")[1])))
    assert img.size == card_px(300)


@pytest.mark.parametrize("front", [
    {"page": 3, "box": [0, 0, 1, 1]},
    {"page": 0, "box": [0, 0, "NaN", 1]},
    {"page": 0, "box": [0, 0, 1]},
    {"page": 0, "box": [0.5, 0.5, 0.5, 0.5]},
    "not json",
])
def test_crop_rejects_bad_box(front):
    files = {"file": ("scan.png", scan_png(), "image/png")}
    raw = front if isinstance(front, str) else json.dumps(front)
    r = client.post("/api/crop", files=files, data={"front": raw})
    assert r.status_code == 400 and r.json()["error"] == "bad_box"


def test_empty_and_garbage_files():
    assert client.post("/api/open", files={"file": ("x.pdf", b"", "application/pdf")}).status_code == 422
    r = client.post("/api/open", files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422 and r.json()["error"] == "unreadable"


def test_exif_rotation_is_applied():
    img = Image.new("RGB", (1400, 2000), "white")   # stored portrait...
    d = ImageDraw.Draw(img)
    d.rectangle((100, 100, 604, 900), fill=(200, 60, 60))
    exif = Image.Exif()
    exif[0x0112] = 6                                 # ...but "rotate 90°" in EXIF
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    opened = client.post("/api/open", files={"file": ("photo.jpg", buf.getvalue(), "image/jpeg")}).json()
    assert opened["pages"][0]["width"] > opened["pages"][0]["height"]


def test_unreadable_file():
    r = client.post("/api/open", files={"file": ("x.pdf", b"%PDF-garbage", "application/pdf")})
    assert r.status_code == 422


def test_export_formats():
    files = [("files", ("a__front.png", png_bytes("red"), "image/png")),
             ("files", ("a__back.png", png_bytes("blue"), "image/png"))]
    r = client.post("/api/export", files=files, data={"format": "zip"})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.namelist() == ["a_front.jpg", "a_back.jpg", "a_card.pdf"]
    front = Image.open(io.BytesIO(z.read("a_front.jpg")))
    assert front.getpixel((0, 0)) == (255, 255, 255)        # rounded corner on white
    assert front.getpixel((505, 319))[0] > 200               # card colour untouched

    r = client.post("/api/export", files=files[:1], data={"format": "jpg", "cmyk": "true"})
    img = Image.open(io.BytesIO(r.content))
    assert (img.format, img.mode, img.size) == ("JPEG", "CMYK", card_px(300))

    r = client.post("/api/export", files=files, data={"format": "pdf"})
    assert r.content.startswith(b"%PDF")

    hd = [("files", ("b__front.png", png_bytes("red", 600), "image/png"))]
    img = Image.open(io.BytesIO(client.post("/api/export", files=hd, data={"format": "jpg"}).content))
    assert img.size == card_px(600) and img.info["dpi"] == (600, 600)


def test_export_duplicate_names_and_many_cards():
    files = [("files", (f"c{i % 3}__{side}.jpg", png_bytes("red"), "image/jpeg"))
             for i in range(6) for side in ("front", "back")]
    r = client.post("/api/export", files=files, data={"format": "zip"})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 18 and len(set(names)) == 18      # nothing overwritten


def test_export_too_many_files():
    files = [("files", (f"c{i}__front.png", png_bytes("red"), "image/png")) for i in range(101)]
    assert client.post("/api/export", files=files).status_code == 413


def test_export_rejects_wrong_size():
    buf = io.BytesIO()
    Image.new("RGB", (100, 60)).save(buf, "PNG")
    r = client.post("/api/export", files=[("files", ("a__front.png", buf.getvalue(), "image/png"))])
    assert r.status_code == 400
