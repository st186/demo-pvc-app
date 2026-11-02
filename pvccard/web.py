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
import unicodedata
import warnings
import zipfile
from contextlib import nullcontext
from urllib.parse import quote, urlparse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.formparsers import MultiPartParser

from .card import DEFAULT_DPI, card_px, colour_background, fit_to_card, on_white, round_corners
from .convert import Document, PasswordRequired, PresetMismatch, Side
from .recipes import BY_NAME, RECIPES
from . import passport

MAX_UPLOAD = 15 * 1024 * 1024
MAX_EXPORT_FILES = 100
MAX_PDF_PAGES = 40       # single combined PDF export
MAX_PREVIEW_PAGES = 12   # pages shown in the crop workspace
MAX_SHEET_PAGES = 20     # photo sheet pages per /api/pdf request (the browser splits)
QUALITIES = (300, 600)   # standard, HD
STATIC = Path(__file__).parent / "static"

# Keep uploads in memory instead of spilling files over 1 MB to a temp file.
MultiPartParser.spool_max_size = MAX_UPLOAD + 1024 * 1024
# Refuse absurdly large images early (decompression bombs): Pillow only warns
# between 1x and 2x this limit, so make that an error too.
Image.MAX_IMAGE_PIXELS = 80_000_000
warnings.simplefilter("error", Image.DecompressionBombWarning)
MAX_PAGE_PIXELS = 40_000_000  # /api/pdf pages: A4 at 600 DPI is ~35 MP


PDFIUM_LOCK = threading.Lock()
# Bound how many requests render/resize/encode at once: each holds tens of MB
# of pixels, and queued requests cost little while waiting here.
WORK_SLOTS = threading.BoundedSemaphore(min(4, os.cpu_count() or 2))

PASSWORD_HINTS = {
    "aadhaar": "First 4 capital letters of the name + year of birth (e.g. ANIS1995)",
    "pan": "Date of birth as DDMMYYYY (e.g. 15081990)",
}

def _quietly(fn) -> None:
    try:
        fn()
    except Exception:
        pass  # reported to the user on their first cutout instead


@asynccontextmanager
async def _lifespan(_app):
    # Load the background-removal model in the background so the first
    # photo doesn't wait for a ~200 MB download and model load.
    threading.Thread(target=lambda: _quietly(passport.warm_up), daemon=True).start()
    yield


app = FastAPI(title="PVC Card Studio", docs_url=None, redoc_url=None, lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


@app.middleware("http")
async def _guard(request: Request, call_next):
    # The server listens on localhost; refuse requests that web pages on other
    # sites send to it (a cross-origin POST needs no CORS preflight).
    origin = request.headers.get("origin")
    if request.method != "GET" and origin and urlparse(origin).netloc != request.headers.get("host"):
        return JSONResponse({"error": "forbidden", "message": "Cross-origin request refused"}, status_code=403)
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        # Revalidate app files every time so an update never mixes old and new code.
        response.headers["Cache-Control"] = "no-cache"
    return response


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


def _pdfium_lock(data: bytes, filename: str):
    """PDFIUM_LOCK for PDFs; images need no lock, so a slow image decode
    never holds up everyone else's PDFs."""
    is_pdf = data[:5] == b"%PDF-" or filename.lower().endswith(".pdf")
    return PDFIUM_LOCK if is_pdf else nullcontext()


def _open(data: bytes, filename: str, password: str) -> Document:
    """Call inside _pdfium_lock()."""
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
    except (ValueError, KeyError, TypeError, OverflowError):
        raise ApiError(400, "bad_box", "Invalid crop box")
    if not all(math.isfinite(v) for v in values):
        raise ApiError(400, "bad_box", "Invalid crop box")
    if not 0 <= page < doc.page_count or x1 - x0 < 0.01 or y1 - y0 < 0.01:
        raise ApiError(400, "bad_box", "Crop box is outside the page")
    return Side(page, (x0, y0, x1, y1))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# Installable Chrome app (PWA). The service worker is served from the root so
# its scope covers the whole app.
@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


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
        with _pdfium_lock(data, file.filename or ""):
            with _open(data, file.filename or "", password) as doc:
                try:
                    s = doc.suggest(doc_type)
                except PresetMismatch as e:
                    raise ApiError(422, "preset_mismatch", f"{e}. Choose Auto-detect or another preset.")
                previews = [doc.preview(i, 1400 if doc.page_count <= 4 else 1000)
                            for i in range(min(doc.page_count, MAX_PREVIEW_PAGES))]
        pages = [{"width": p.width, "height": p.height, "image": _data_url(p, 85)} for p in previews]
    side = lambda x: x and {"page": x.page, "box": list(x.box)}
    return {"type": s.doc_type, "label": s.label, "pages": pages,
            "front": side(s.front), "back": side(s.back)}


@app.post("/api/crop")
def crop(file: UploadFile = File(...), password: str = Form(""), front: str = Form(...),
         back: str = Form(""), fit: str = Form("stretch"), dpi: int = Form(DEFAULT_DPI),
         sharpen: bool = Form(True), colour: bool = Form(False)):
    """Render the chosen boxes as CR80 card images (square corners; the
    browser shows them rounded and export applies the rounding)."""
    if fit not in ("stretch", "contain"):
        raise ApiError(400, "bad_fit", "fit must be stretch or contain")
    if dpi not in QUALITIES:
        raise ApiError(400, "bad_dpi", "dpi must be 300 or 600")
    data = _read(file)
    with WORK_SLOTS:
        with _pdfium_lock(data, file.filename or ""):
            with _open(data, file.filename or "", password) as doc:
                sides = {name: _side(raw, doc) for name, raw in (("front", front), ("back", back))}
                if not sides["front"]:
                    raise ApiError(400, "bad_box", "Front crop box is required")
                raw = {name: s and doc.crop(s, dpi) for name, s in sides.items()}
        def finish(img: Image.Image) -> str:
            card = fit_to_card(img, dpi, fit, sharpen)
            return _data_url(colour_background(card) if colour else card, 95)
        return {name: img and finish(img) for name, img in raw.items()}


def _safe(name: str) -> str:
    # Keep letters, digits and combining marks (Devanagari/Bengali vowel signs).
    kept = "".join(c if c.isalnum() or c in "-_" or unicodedata.category(c).startswith("M") else "_"
                   for c in name)
    return re.sub(r"_+", "_", kept).strip("_")[:60] or "card"


def _attachment(data: bytes, media: str, filename: str) -> Response:
    # HTTP headers are Latin-1: give an ASCII name plus the full Unicode name
    # (RFC 6266), so files named in Hindi, Bengali etc. still download.
    ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_")
    disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(data, media_type=media, headers={"Content-Disposition": disposition})


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
            q = sizes.get(img.size)
            if q:
                img = img.convert("RGB")  # decodes; a truncated file fails here
        except Exception:
            raise ApiError(400, "bad_image", "Card images must be JPEG or PNG")
        if not q or (state["dpi"] and q != state["dpi"]):
            raise ApiError(400, "bad_size", "Card images must all be CR80 at 300 or 600 DPI")
        state["dpi"] = q
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

    with WORK_SLOTS:  # each export holds tens of MB of pixels
        return _export(files, format, load, jpeg, pdf)


def _export(files, format, load, jpeg, pdf) -> Response:
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


# ---------- passport photos ----------

@app.get("/api/passport/sizes")
def passport_sizes() -> dict:
    return {"sizes": [vars(s) for s in passport.SIZES.values()],
            "custom": {"head": passport.CUSTOM_HEAD, "top": passport.CUSTOM_TOP}}


@app.get("/api/passport/status")
def passport_status(quality: str = "fast") -> dict:
    """Polled by the browser while a photo is processed, for the progress bar."""
    if quality not in passport.MODELS:
        raise ApiError(400, "bad_quality", "quality must be fast or best")
    if not passport.available():
        return {"state": "failed", "progress": 0, "seconds": 0}
    return passport.status(quality)


@app.get("/api/passport/info")
def passport_info() -> dict:
    ok = passport.available()
    return {"available": ok, "gpu": ok and passport.gpu_provider(),
            "default_quality": passport.default_quality()}


@app.post("/api/passport/cutout")
def passport_cutout(file: UploadFile = File(...), quality: str = Form("fast")):
    """Person with transparent background, plus head positions in its pixels."""
    if quality not in passport.MODELS:
        raise ApiError(400, "bad_quality", "quality must be fast or best")
    data = _read(file)
    try:
        with WORK_SLOTS:
            cut = passport.cut_out(data, quality)
    except ImportError:
        raise ApiError(501, "photo_extra_missing",
                       "Background removal is not installed: pip install -e .[photo]")
    except ValueError as e:
        raise ApiError(422, "no_person", str(e))
    except Exception:
        raise ApiError(422, "unreadable", "This file could not be opened as a photo")
    buf = io.BytesIO()
    cut.image.save(buf, "PNG", compress_level=3)
    return {"image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "original": _data_url(cut.original, 92),
            "width": cut.image.width, "height": cut.image.height,
            "heads": [vars(h) for h in cut.heads]}


@app.post("/api/pdf")
def pages_pdf(pages: list[UploadFile] = File(...), dpi: int = Form(300), name: str = Form("photos")):
    """Combine page images (e.g. A4 photo sheets) into one PDF at `dpi`."""
    if len(pages) > MAX_SHEET_PAGES:
        raise ApiError(413, "too_many", f"At most {MAX_SHEET_PAGES} pages per PDF")
    if dpi not in QUALITIES:
        raise ApiError(400, "bad_dpi", "dpi must be 300 or 600")
    with WORK_SLOTS:
        imgs = []
        for p in pages:
            try:
                img = Image.open(io.BytesIO(p.file.read(MAX_UPLOAD)))
                too_big = img.width * img.height > MAX_PAGE_PIXELS
                if not too_big:
                    imgs.append(img.convert("RGB"))
            except Exception:
                raise ApiError(400, "bad_image", "Pages must be JPEG or PNG")
            if too_big:
                raise ApiError(413, "too_large", "Page images must be at most A4 at 600 DPI")
        buf = io.BytesIO()
        imgs[0].save(buf, "PDF", resolution=dpi, save_all=True, append_images=imgs[1:])
    return _attachment(buf.getvalue(), "application/pdf", f"{_safe(name)}.pdf")
