"""Passport Studio API. Set PVC_PHOTO to a real portrait to also test the
cut-out itself (real photos are never committed)."""

import io
import os

import pytest
from PIL import Image

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pvccard import passport  # noqa: E402
from pvccard.web import app  # noqa: E402

client = TestClient(app)


def test_sizes_and_custom_framing():
    data = client.get("/api/passport/sizes").json()
    ids = [s["id"] for s in data["sizes"]]
    assert ids[:4] == ["passport", "stamp", "joint", "group"]
    for s in data["sizes"]:
        assert 0 < s["head"] < 1 and 0 <= s["top"] < 0.3 and s["description"]
    assert set(data["custom"]) == {"head", "top"}


def test_info_reports_quality():
    info = client.get("/api/passport/info").json()
    assert info["default_quality"] in passport.MODELS
    assert info["default_quality"] == ("best" if info["gpu"] else "fast")


def test_cutout_rejects_bad_input():
    r = client.post("/api/passport/cutout", files={"file": ("x.jpg", b"not an image", "image/jpeg")},
                    data={"quality": "ultra"})
    assert r.status_code == 400
    if passport.available():
        r = client.post("/api/passport/cutout", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
        assert r.status_code == 422


@pytest.mark.skipif(not os.environ.get("PVC_PHOTO") or not passport.available(),
                    reason="set PVC_PHOTO to a portrait photo (and install .[photo])")
def test_cutout_real_photo():
    with open(os.environ["PVC_PHOTO"], "rb") as f:
        r = client.post("/api/passport/cutout", files={"file": ("p.jpg", f.read(), "image/jpeg")})
    d = r.json()
    assert r.status_code == 200 and d["heads"]
    h = d["heads"][0]
    assert 0 <= h["crown"] < h["chin"] <= d["height"]
    assert 0 < h["center"] < d["width"]


def test_pdf_from_pages():
    buf = io.BytesIO()
    Image.new("RGB", (2480, 3508), "white").save(buf, "JPEG")
    r = client.post("/api/pdf", files=[("pages", ("p1.jpg", buf.getvalue(), "image/jpeg"))],
                    data={"dpi": "300", "name": "sheet"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert 'filename="sheet.pdf"' in r.headers["content-disposition"]


def test_installable_app_files():
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["display"] == "standalone"
    for icon in m.json()["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
