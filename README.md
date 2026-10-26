# pvccard

Turns downloaded Indian government ID PDFs into print-ready PVC card files:
front and back as separate CR80 (85.6 × 54 mm) JPEGs at 300 DPI
(1011 × 638 px), plus a 2-page PDF.

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
pip install -e .[web,dev]
uvicorn pvccard.web:app --port 8000
# open http://localhost:8000
```

- **Uploads & Queue**: drop many PDFs/images at once; password-protected PDFs
  ask for their password. Pick a card preset or leave it on auto-detect.
- **Cropper Workspace**: the detected front/back boxes sit on the page; drag
  to move, drag handles to resize (corners keep the card shape), Auto-Snap
  restores the detected boxes, zoom and multi-page switching.
- **Live Preview**: rounded-corner CR80 preview with brightness, contrast,
  saturation and Auto-Enhance; fill or keep-ratio fitting; RGB or CMYK JPEGs.
- **Downloads / Print**: front or back JPEG, Download All (JPEGs + PDF per
  card, for every selected card), or print straight to a CR80 card printer.

The server is stateless: the browser re-sends the file for each crop and
nothing is written to disk.

API: `GET /api/types`, `POST /api/open` (page previews + suggested boxes),
`POST /api/crop` (card PNGs for given boxes), `POST /api/export` (JPEG, PDF or
zip from adjusted PNGs).

## Command line

```bash
pip install -e .[dev]

pvccard DOC.pdf -o out/                 # auto-detects the document type
pvccard AADHAAR.pdf -p ABCD1990 -o out/ # password-protected PDFs
pvccard DOC.pdf --cmyk                  # CMYK JPEGs for print shops
pvccard DOC.pdf --fit contain           # keep proportions, pad with white
pvccard DOC.pdf --square                # no rounded corners
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
