"""PVC Card Studio web app.

Run: python -m pvccard.serve   (or: uvicorn pvccard.web:app --reload)
Uploads are processed in memory and never written to disk. The browser keeps
the file and re-sends it on each crop, so the server holds no state.

pdfium is not thread-safe and FastAPI runs these handlers in a thread pool,
so every pdfium call happens under PDFIUM_LOCK. Resizing and encoding run
outside the lock. To use more CPU cores, run several worker processes
(uvicorn --workers N); each process has its own pdfium and lock.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import threading
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.formparsers import MultiPartParser

from .card import DEFAULT_DPI, card_px, fit_to_card, on_white, round_corners
from .convert import Document, PasswordRequired, Side
from .recipes import BY_NAME, RECIPES

MAX_UPLOAD = 15 * 1024 * 1024
MAX_EXPORT_FILES = 100
MAX_PDF_PAGES = 40       # single combined PDF export
QUALITIES = (300, 600)   # standard, HD
STATIC = Path(__file__).parent / "static"

# Keep uploads in memory instead of spilling files over 1 MB to a temp file.
MultiPartParser.spool_max_size = MAX_UPLOAD + 1024 * 1024
# Refuse absurdly large images early (decompression bombs).
Image.MAX_IMAGE_PIXELS = 80_000_000


PDFIUM_LOCK = threading.Lock()
# Bound how many requests render/resize/encode at once: each holds tens of MB
# of pixels, and queued requests cost little while waiting here.
WORK_SLOTS = threading.BoundedSemaphore(min(4, os.cpu_count() or 2))

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


@app.exception_handler(Exception)
def _unexpected(_, e: Exception) -> JSONResponse:
    return JSONResponse({"error": "server", "message": "Something went wrong processing this file"},
                        status_code=500)


def _data_url(img: Image.Image, quality: int) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, subsampling=0 if quality >= 90 else 2)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _read(file: UploadFile) -> bytes:
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ApiError(413, "too_large", "File is larger than 15 MB")
    if not data:
        raise ApiError(422, "unreadable", "The file is empty")
    return data


def _open(data: bytes, filename: str, password: str) -> Document:
    """Call with PDFIUM_LOCK held."""
    try:
        return Document(data, filename, password or None)
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
        values = [float(v) for v in d["box"]]
        x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in values)
    except (ValueError, KeyError, TypeError):
        raise ApiError(400, "bad_box", "Invalid crop box")
    if not all(math.isfinite(v) for v in values):
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
    data = _read(file)
    with WORK_SLOTS:
        with PDFIUM_LOCK:
            with _open(data, file.filename or "", password) as doc:
                s = doc.suggest(doc_type)
                previews = [doc.preview(i, 1400) for i in range(min(doc.page_count, 4))]
        pages = [{"width": p.width, "height": p.height, "image": _data_url(p, 85)} for p in previews]
    side = lambda x: x and {"page": x.page, "box": list(x.box)}
    return {"type": s.doc_type, "label": s.label, "pages": pages,
            "front": side(s.front), "back": side(s.back)}


@app.post("/api/crop")
def crop(file: UploadFile = File(...), password: str = Form(""), front: str = Form(...),
         back: str = Form(""), fit: str = Form("stretch"), dpi: int = Form(DEFAULT_DPI),
         sharpen: bool = Form(True)):
    """Render the chosen boxes as CR80 card images (square corners; the
    browser shows them rounded and export applies the rounding)."""
    if fit not in ("stretch", "contain"):
        raise ApiError(400, "bad_fit", "fit must be stretch or contain")
    if dpi not in QUALITIES:
        raise ApiError(400, "bad_dpi", "dpi must be 300 or 600")
    data = _read(file)
    with WORK_SLOTS:
        with PDFIUM_LOCK:
            with _open(data, file.filename or "", password) as doc:
                sides = {name: _side(raw, doc) for name, raw in (("front", front), ("back", back))}
                if not sides["front"]:
                    raise ApiError(400, "bad_box", "Front crop box is required")
                raw = {name: s and doc.crop(s, dpi) for name, s in sides.items()}
        return {name: img and _data_url(fit_to_card(img, dpi, fit, sharpen), 95)
                for name, img in raw.items()}


def _safe(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name).strip("_")[:60] or "card"


def _attachment(data: bytes, media: str, filename: str) -> Response:
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/export")
def export(files: list[UploadFile] = File(...), format: str = Form("zip"),
           cmyk: bool = Form(False), rounded: bool = Form(True)):
    """Build print files from browser-adjusted card images.

    Each upload is named "<card name>__front.<ext>" or "<card name>__back.<ext>"
    (JPEG or PNG), with a card's sides next to each other. format: jpg (one
    file -> that JPEG, several -> zip), pdf (all cards in one PDF), zip (JPEGs
    and a PDF per card). Cards are processed one at a time to bound memory.
    """
    if format not in ("jpg", "pdf", "zip"):
        raise ApiError(400, "bad_format", "format must be jpg, pdf or zip")
    if len(files) > MAX_EXPORT_FILES:
        raise ApiError(413, "too_many", f"At most {MAX_EXPORT_FILES} images per download")
    if format == "pdf" and len(files) > MAX_PDF_PAGES:
        raise ApiError(413, "too_many", f"At most {MAX_PDF_PAGES} pages per PDF")

    sizes = {card_px(q): q for q in QUALITIES}
    state = {"dpi": None}

    def load(f: UploadFile) -> tuple[str, str, Image.Image]:
        stem, _, side = Path(f.filename or "").stem.rpartition("__")
        if side not in ("front", "back"):
            raise ApiError(400, "bad_name", "Files must be named <card>__front / <card>__back")
        try:
            img = Image.open(io.BytesIO(f.file.read(MAX_UPLOAD)))
        except Exception:
            raise ApiError(400, "bad_image", "Card images must be JPEG or PNG")
        q = sizes.get(img.size)
        if not q or (state["dpi"] and q != state["dpi"]):
            raise ApiError(400, "bad_size", "Card images must all be CR80 at 300 or 600 DPI")
        state["dpi"] = q
        img = img.convert("RGB")
        if rounded:
            img = on_white(round_corners(img, q))
        return _safe(stem), side, img

    def jpeg(img: Image.Image) -> bytes:
        buf = io.BytesIO()
        (img.convert("CMYK") if cmyk else img).save(
            buf, "JPEG", quality=95, dpi=(state["dpi"],) * 2, subsampling=0)
        return buf.getvalue()

    def pdf(pages: list[Image.Image]) -> bytes:
        buf = io.BytesIO()
        pages[0].save(buf, "PDF", resolution=state["dpi"], save_all=True, append_images=pages[1:])
        return buf.getvalue()

    if format == "pdf":
        loaded = [load(f) for f in files]
        names = {n for n, _, _ in loaded}
        name = loaded[0][0] if len(names) == 1 else "pvc_cards"
        return _attachment(pdf([img for _, _, img in loaded]), "application/pdf", f"{name}.pdf")
    if format == "jpg" and len(files) == 1:
        name, side, img = load(files[0])
        return _attachment(jpeg(img), "image/jpeg", f"{name}_{side}.jpg")

    # Group consecutive uploads by card name, then handle one card at a time.
    groups: list[list[UploadFile]] = []
    for f in files:
        stem = Path(f.filename or "").stem.rpartition("__")[0]
        if groups and Path(groups[-1][0].filename or "").stem.rpartition("__")[0] == stem:
            groups[-1].append(f)
        else:
            groups.append([f])
    buf = io.BytesIO()
    names: list[str] = []
    with zipfile.ZipFile(buf, "w") as z:
        for group in groups:
            card = [load(f) for f in group]
            name = card[0][0]
            if name in names:
                name = f"{name}_{len(names) + 1}"
            names.append(name)
            for _, side, img in card:
                z.writestr(f"{name}_{side}.jpg", jpeg(img))
            if format == "zip":
                z.writestr(f"{name}_card.pdf", pdf([img for _, _, img in card]))
    zip_name = names[0] if len(names) == 1 else "pvc_cards"
    return _attachment(buf.getvalue(), "application/zip", f"{zip_name}.zip")
