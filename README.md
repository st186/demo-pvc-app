# pvccard

Turns downloaded Indian government ID PDFs into print-ready PVC card files:
front and back as separate CR80 (85.6 × 54 mm) JPEGs, plus a 2-page PDF.
Default output is HD 600 DPI (2022 × 1275 px); `--dpi 300` gives the
standard 1011 × 638 px.

### Quality

Most of these PDFs draw names, numbers and lines as vector text, so HD
renders them at true 600 DPI rather than enlarging a 300 DPI image. Embedded
pictures (photos, QR codes, card backgrounds, image-only scans) are limited to
what the government file contains, usually 200–300 DPI; a light unsharp mask
(on by default, `--no-sharpen` to skip) makes them crisper. AI upscaling is
deliberately not used: it can invent detail, which is unacceptable for the
digits and photos on an identity document.

## Supported documents

| Type | Source | How it is cropped |
|---|---|---|
| `aadhaar` | e-Aadhaar letter (password: first 4 letters of name in CAPS + birth year) | Cut-out card strip at the bottom of the page |
| `pan` | e-PAN (password: DOB as DDMMYYYY) | Card front/back inside the "Cut" box |
| `ayushman` | Ayushman (PM-JAY) card PDF | Page 1 front, page 2 back |
| `abha` | ABHA card PDF | Top half front, bottom half back |
| `epic` | Voter e-EPIC (new format) | Card pair at the top of the page |
| `ration_wb` | West Bengal e-Ration card (PHH, RKSY, …) | Card pair at the bottom of the page |
| `dl_wb` | WB driving licence PDF | Card pair at the top of the page |
| `scan` | Anything else (image-only PDFs such as the old Bihar e-EPIC, JPG/PNG) | Card shapes detected automatically |

Cards are cropped by rendering the card area of the page at high resolution,
not by extracting embedded images, because several documents (e.g. the
e-Ration card) draw the holder's details as text on top of a blank card image.

The tool only crops and resizes. It never edits the content of a document.

## Web app (PVC Card Studio)

```bash
pip install -e ".[web,photo,dev]"   # or .[web,photo-gpu,dev] with a GPU
python -m pvccard.serve            # open http://localhost:8000
python -m pvccard.serve --host 0.0.0.0 --port 8000 --workers 4   # on a server
```

- **Uploads & Queue**: drop many PDFs/images at once; password-protected PDFs
  ask for their password. Pick a card preset or leave it on auto-detect.
- **Cropper Workspace**: the detected front/back boxes sit on the page; drag
  to move, drag handles to resize (corners keep the card shape), Auto-Snap
  restores the detected boxes, zoom and multi-page switching.
- **Live Preview**: rounded-corner CR80 preview with brightness, contrast,
  saturation and Auto-Enhance; HD 600 / 300 DPI, sharpening on/off, fill or
  keep-ratio fitting, RGB or CMYK JPEGs.
- **Downloads / Print**: front or back JPEG, Download All (JPEGs + PDF per
  card, for every selected card), or print straight to a CR80 card printer.

The server is stateless: the browser re-sends the file for each crop and
uploads are kept in memory, never written to disk.

### Load and reliability

- pdfium is not thread-safe (concurrent use crashed the process with heap
  corruption in testing), so all pdfium calls run under one lock per process.
  Use `--workers N` (about one per CPU core) to use more cores.
- `pvccard.serve` sets `MALLOC_ARENA_MAX=2` on Linux before starting; without
  it glibc kept ~2 GB resident under load instead of ~800 MB.
- Measured on 4 cores: 100 documents opened and cropped at HD, 25 at a time,
  in ~35 s with 1 worker and ~21 s with 4; 0 errors across 500+ documents,
  including bursts of 50 concurrent requests. `scripts/load_test.py` repeats
  this against your own samples.
- The browser keeps cropped cards as compressed JPEGs and decodes pixels only
  for the card on screen, so a long queue stays light (~50 MB for 12 HD cards).
  Download All is split into batches of 40 cards.

API: `GET /api/types`, `POST /api/open` (page previews + suggested boxes),
`POST /api/crop` (card PNGs for given boxes), `POST /api/export` (JPEG, PDF or
zip from adjusted PNGs).

## Passport Studio

The second tool in the app (top switch: PVC Card Maker | Passport Studio).

- Upload any photo of a person. AI removes the background and finds every
  face, then frames the photo for the chosen preset: India Passport / Visa
  3.5×4.5 cm, Stamp 2.5×3.0 cm, Joint Photo (2 people) 4.5×3.5 cm, Group /
  SHG 5.0×3.5 cm, US / OCI 2×2 in, or a custom size in mm.
- Workspace: drag or resize the frame (locked to the photo shape), AI
  Auto-Center, Fit Frame, Rotate.
- Live output with Hold to Compare; background colours (or keep the
  original); enhancement looks (Natural, Cool Blue, Rosy, Warm, Standard) at
  Low/Med/High; brightness, contrast, cutout edge softness, backdrop despill;
  photo border width and colour.
- Print Photo Sheet: copies per photo (steps of 6), A4 or 4×6 in paper
  (orientation picked for capacity), capacity bar, live preview, PDF or
  print. Photos are always printed at their exact size, e.g. 30 passport
  photos per A4, 8 per 4×6.

Background removal models (downloaded once, on first use):

| Quality | Model | CPU | GPU |
|---|---|---|---|
| Fast | rembg `u2net_human_seg` | ~1 s, ~1 GB RAM | — |
| Best edges | BiRefNet general-lite (MIT) | 10–30 s, ~7 GB RAM | ~1 s |

Best is chosen automatically when a GPU is available. Install the GPU
runtime with `pip install -e ".[photo-gpu]"` (DirectML on Windows works with
any GPU; CUDA on Linux), otherwise `pip install -e ".[photo]"`. RMBG-2.0 is
not used because its licence is non-commercial; check MODNet's licence before
using it commercially too. Faces are found with OpenCV's YuNet model (MIT,
bundled in `pvccard/models`).

## Install as a Chrome app

PVC Studio is an installable web app (PWA). Start the server, open it in
Chrome and click **Install App** in the top bar (or the install icon in the
address bar). It then opens in its own window from the desktop / Start menu.
Chrome allows installing from `http://localhost` or any `https://` address;
for other machines on a network, serve it over HTTPS.

## Command line

```bash
pip install -e .[dev]

pvccard DOC.pdf -o out/                 # auto-detects the document type
pvccard AADHAAR.pdf -p ABCD1990 -o out/ # password-protected PDFs
pvccard DOC.pdf --cmyk                  # CMYK JPEGs for print shops
pvccard DOC.pdf --fit contain           # keep proportions, pad with white
pvccard DOC.pdf --square                # no rounded corners
pvccard DOC.pdf --dpi 300 --no-sharpen  # standard quality, no sharpening
```

Output per side: `<name>_front.png` / `_back.png` (rounded 3.18 mm corners,
transparent outside), `<name>_front.jpg` / `_back.jpg` (same, corners white),
plus `<name>_card.pdf`.

## Tests

```bash
pytest                              # unit tests only
PVC_SAMPLES=/path/to/samples pytest # also runs real samples listed in samples/cases.json
```

Real identity documents must never be committed. `samples/`, `out/` and all
PDF/image files are git-ignored.

## Adding a document type

Add a `Recipe` to `pvccard/recipes.py` with a `match` function (text or page
layout fingerprint) and the front/back regions in PDF points
(left, bottom, right, top), then add the sample to your local `samples/cases.json`.
