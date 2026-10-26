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


def png_bytes(color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", card_px(300), color).save(buf, "PNG")
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
        assert img.size == card_px(300)
        assert img.getpixel((0, 0))[3] == 0  # rounded corner is transparent


def test_crop_rejects_bad_box():
    files = {"file": ("scan.png", scan_png(), "image/png")}
    r = client.post("/api/crop", files=files, data={"front": json.dumps({"page": 3, "box": [0, 0, 1, 1]})})
    assert r.status_code == 400


def test_unreadable_file():
    r = client.post("/api/open", files={"file": ("x.pdf", b"%PDF-garbage", "application/pdf")})
    assert r.status_code == 422


def test_export_formats():
    files = [("files", ("a__front.png", png_bytes("red"), "image/png")),
             ("files", ("a__back.png", png_bytes("blue"), "image/png"))]
    r = client.post("/api/export", files=files, data={"format": "zip"})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["a_front.jpg", "a_back.jpg", "a_card.pdf"]

    r = client.post("/api/export", files=files[:1], data={"format": "jpg", "cmyk": "true"})
    img = Image.open(io.BytesIO(r.content))
    assert (img.format, img.mode, img.size) == ("JPEG", "CMYK", card_px(300))

    r = client.post("/api/export", files=files, data={"format": "pdf"})
    assert r.content.startswith(b"%PDF")


def test_export_rejects_wrong_size():
    buf = io.BytesIO()
    Image.new("RGB", (100, 60)).save(buf, "PNG")
    r = client.post("/api/export", files=[("files", ("a__front.png", buf.getvalue(), "image/png"))])
    assert r.status_code == 400
