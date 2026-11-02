"""Smoke test a running PVC Studio (used by the Windows build after installing).

    python smoke_test.py http://127.0.0.1:8790

Uses only synthetic inputs and the standard library, so it runs against the
embedded Python of a fresh install.
"""

import io
import json
import sys
import urllib.error
import urllib.request
import uuid
import zipfile

from PIL import Image, ImageDraw

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8790"
failures = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures.append(name)


def request(path, fields=None, files=None):
    """GET, or multipart POST when fields/files are given. Returns (status, headers, body)."""
    if fields is None and files is None:
        req = urllib.request.Request(BASE + path)
    else:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (fields or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (fname, data, ctype) in (files or []):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                         f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(BASE + path, data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def scan_image():
    img = Image.new("RGB", (2000, 1400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((100, 100, 900, 604), fill=(200, 60, 60))
    d.rectangle((1000, 100, 1800, 604), fill=(60, 60, 200))
    return img


# App shell and installable-app files
s, _, body = request("/")
check("home page", s == 200 and b"PVC Studio" in body)
s, h, _ = request("/sw.js")
check("service worker", s == 200 and "javascript" in h.get("Content-Type", ""))
s, _, body = request("/manifest.webmanifest")
icons = json.loads(body)["icons"] if s == 200 else []
check("manifest + icons", s == 200 and all(request(i["src"])[0] == 200 for i in icons), f"{len(icons)} icons")
s, _, body = request("/api/types")
check("document types", s == 200 and len(json.loads(body)) >= 7)

# Card maker: scan with two cards -> open, crop (HD + colour), export zip
scan = png(scan_image())
s, _, body = request("/api/open", files=[("file", ("scan card ü.png", scan, "image/png"))])
opened = json.loads(body) if s == 200 else {}
check("open scanned card", s == 200 and opened.get("type") == "scan" and opened.get("back"))
s, _, body = request("/api/crop", fields={"front": json.dumps(opened["front"]), "back": json.dumps(opened["back"]),
                                          "dpi": "600", "colour": "true"},
                     files=[("file", ("scan.png", scan, "image/png"))])
crop = json.loads(body) if s == 200 else {}
check("crop front/back at HD", s == 200 and crop.get("front", "").startswith("data:image/jpeg"))

import base64  # noqa: E402

front = base64.b64decode(crop["front"].split(",")[1])
s, h, body = request("/api/export", fields={"format": "zip", "cmyk": "true"},
                     files=[("files", ("Card Ü__front.jpg", front, "image/jpeg")),
                            ("files", ("Card Ü__back.jpg", base64.b64decode(crop["back"].split(",")[1]), "image/jpeg"))])
names = zipfile.ZipFile(io.BytesIO(body)).namelist() if s == 200 else []
check("export zip (JPEG + PDF)", s == 200 and len(names) == 3, str(names))

# A real PDF document (two pages) through pdfium
pdf = io.BytesIO()
scan_image().save(pdf, "PDF", save_all=True, append_images=[scan_image()], resolution=150)
s, _, body = request("/api/open", files=[("file", ("two pages.pdf", pdf.getvalue(), "application/pdf"))])
check("open 2-page PDF", s == 200 and len(json.loads(body)["pages"]) == 2)
s, _, body = request("/api/open", files=[("file", ("broken.pdf", b"%PDF-1.4 nope", "application/pdf"))])
check("broken PDF -> clean error", s == 422 and json.loads(body)["error"] == "unreadable")

# Passport Studio: model loads and runs (a blank image has no person)
s, _, body = request("/api/passport/info")
info = json.loads(body)
check("passport info", s == 200 and info["available"], json.dumps(info))
s, _, body = request("/api/passport/cutout", fields={"quality": "fast"},
                     files=[("file", ("blank.png", png(Image.new("RGB", (600, 800), "white")), "image/png"))])
check("background model runs", s == 422 and json.loads(body)["error"] == "no_person", body[:120])
s, _, body = request("/api/passport/status?quality=fast")
check("model ready", s == 200 and json.loads(body)["state"] == "ready", body.decode()[:120])
page = io.BytesIO()
Image.new("RGB", (2480, 3508), "white").save(page, "JPEG")
s, _, body = request("/api/pdf", fields={"dpi": "300", "name": "sheet"},
                     files=[("pages", ("p1.jpg", page.getvalue(), "image/jpeg"))])
check("photo sheet PDF", s == 200 and body.startswith(b"%PDF"))

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
