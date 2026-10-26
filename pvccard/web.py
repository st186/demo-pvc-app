"""PVC Card Studio web app.

Run: uvicorn pvccard.web:app --reload
Uploads are processed in memory and never written to disk. The browser keeps
the file and re-sends it on each crop, so the server holds no state.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .card import DEFAULT_DPI, card_px, fit_to_card, on_white, round_corners
from .convert import Document, PasswordRequired, Side
from .recipes import BY_NAME, RECIPES

MAX_UPLOAD = 15 * 1024 * 1024
QUALITIES = (300, 600)  # standard, HD
STATIC = Path(__file__).parent / "static"

PASSWORD_HINTS = {
    "aadhaar": "First 4 capital letters of the name + year of birth (e.g. ANIS1995)",
    "pan": "Date of birth as DDMMYYYY (e.g. 15081990)",
}

app = FastAPI(title="PVC Card Studio", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


@app.exception_handler(ApiError)
def _api_error(_, e: ApiError) -> JSONResponse:
    return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)


def _data_url(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def _open(file: UploadFile, password: str) -> Document:
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ApiError(413, "too_large", "File is larger than 15 MB")
    try:
        return Document(data, file.filename or "", password or None)
    except PasswordRequired as e:
        raise ApiError(401, "wrong_password" if password else "password_required", str(e))
    except Exception:
        raise ApiError(422, "unreadable", "This file could not be opened as a PDF or image")


def _side(raw: str, doc: Document) -> Side | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
        page = int(d["page"])
        x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in d["box"])
    except (ValueError, KeyError, TypeError):
        raise ApiError(400, "bad_box", "Invalid crop box")
    if not 0 <= page < doc.page_count or x1 - x0 < 0.01 or y1 - y0 < 0.01:
        raise ApiError(400, "bad_box", "Crop box is outside the page")
    return Side(page, (x0, y0, x1, y1))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/types")
def types() -> list[dict]:
    return [{"id": r.name, "label": r.label, "description": r.description,
             "password_hint": PASSWORD_HINTS.get(r.name)} for r in RECIPES]


@app.post("/api/open")
def open_document(file: UploadFile = File(...), password: str = Form(""),
                  doc_type: str = Form("auto")):
    """Page previews for the crop workspace plus suggested front/back boxes."""
    if doc_type != "auto" and doc_type not in BY_NAME:
        raise ApiError(400, "bad_type", "Unknown document type")
    doc = _open(file, password)
    s = doc.suggest(doc_type)
    pages = []
    for i in range(min(doc.page_count, 4)):
        img = doc.preview(i, 1400)
        pages.append({"width": img.width, "height": img.height, "image": _data_url(img, "JPEG")})
    side = lambda x: x and {"page": x.page, "box": list(x.box)}
    return {"type": s.doc_type, "label": s.label, "pages": pages,
            "front": side(s.front), "back": side(s.back)}


@app.post("/api/crop")
def crop(file: UploadFile = File(...), password: str = Form(""), front: str = Form(...),
         back: str = Form(""), fit: str = Form("stretch"), dpi: int = Form(DEFAULT_DPI),
         sharpen: bool = Form(True)):
    """Render the chosen boxes as CR80 card images with rounded corners."""
    if fit not in ("stretch", "contain"):
        raise ApiError(400, "bad_fit", "fit must be stretch or contain")
    if dpi not in QUALITIES:
        raise ApiError(400, "bad_dpi", "dpi must be 300 or 600")
    doc = _open(file, password)
    out = {}
    for name, raw in (("front", front), ("back", back)):
        side = _side(raw, doc)
        out[name] = side and _data_url(
            round_corners(fit_to_card(doc.crop(side, dpi), dpi, fit, sharpen), dpi))
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name).strip("_")[:60] or "card"


def _attachment(data: bytes, media: str, filename: str) -> Response:
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/export")
def export(files: list[UploadFile] = File(...), format: str = Form("zip"),
           cmyk: bool = Form(False)):
    """Build print files from browser-adjusted card PNGs.

    Each upload is named "<card name>__front.png" or "<card name>__back.png".
    format: jpg (one file -> that JPEG, several -> zip), pdf (all cards, one
    page per side), zip (JPEGs and a PDF per card).
    """
    if format not in ("jpg", "pdf", "zip"):
        raise ApiError(400, "bad_format", "format must be jpg, pdf or zip")
    cards: dict[str, dict[str, Image.Image]] = {}
    sizes = {card_px(q): q for q in QUALITIES}
    dpi = None
    for f in files[:100]:
        stem, _, side = (f.filename or "").removesuffix(".png").rpartition("__")
        if side not in ("front", "back"):
            raise ApiError(400, "bad_name", "Files must be named <card>__front.png / __back.png")
        img = Image.open(io.BytesIO(f.file.read(MAX_UPLOAD)))
        if img.size not in sizes or (dpi and sizes[img.size] != dpi):
            raise ApiError(400, "bad_size", "Card images must all be CR80 at 300 or 600 DPI")
        dpi = sizes[img.size]
        cards.setdefault(_safe(stem), {})[side] = on_white(img.convert("RGBA"))

    def jpeg(img: Image.Image) -> bytes:
        buf = io.BytesIO()
        (img.convert("CMYK") if cmyk else img).save(buf, "JPEG", quality=95, dpi=(dpi, dpi), subsampling=0)
        return buf.getvalue()

    def pdf(pages: list[Image.Image]) -> bytes:
        buf = io.BytesIO()
        pages[0].save(buf, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])
        return buf.getvalue()

    ordered = [(name, side, sides[side]) for name, sides in cards.items()
               for side in ("front", "back") if side in sides]
    if format == "pdf":
        name = ordered[0][0] if len(cards) == 1 else "pvc_cards"
        return _attachment(pdf([img for _, _, img in ordered]), "application/pdf", f"{name}.pdf")
    if format == "jpg" and len(ordered) == 1:
        name, side, img = ordered[0]
        return _attachment(jpeg(img), "image/jpeg", f"{name}_{side}.jpg")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, side, img in ordered:
            z.writestr(f"{name}_{side}.jpg", jpeg(img))
        if format == "zip":
            for name, sides in cards.items():
                z.writestr(f"{name}_card.pdf", pdf([sides[s] for s in ("front", "back") if s in sides]))
    zip_name = ordered[0][0] if len(cards) == 1 else "pvc_cards"
    return _attachment(buf.getvalue(), "application/zip", f"{zip_name}.zip")
