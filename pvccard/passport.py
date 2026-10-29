"""Passport photos: cut the person out and locate each head. The browser does
the framing, background, enhancement and sheet layout from this.

Background removal uses rembg's person-segmentation model (downloaded once,
~176 MB, on first use). Install with: pip install -e .[photo]
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_INPUT_PX = 1600
MODEL = "u2net_human_seg"


@dataclass(frozen=True)
class PhotoSize:
    id: str
    label: str
    w_mm: float
    h_mm: float
    head: float  # crown-to-chin height as a fraction of photo height
    top: float   # gap above the crown as a fraction of photo height
    description: str = ""


SIZES = {s.id: s for s in (
    PhotoSize("passport", "India Passport / Visa (3.5×4.5 cm)", 35, 45, 0.70, 0.08,
              "Standard Indian passport, visa, police verification, most forms"),
    PhotoSize("stamp", "Stamp Size Photo (2.5×3.0 cm)", 25, 30, 0.66, 0.08,
              "School ID, college admission, documents"),
    PhotoSize("joint", "Joint Photo · 2 People (4.5×3.5 cm)", 45, 35, 0.50, 0.12,
              "Joint bank account, pensioner and spouse"),
    PhotoSize("group", "Group Photo · SHG (5.0×3.5 cm)", 50, 35, 0.34, 0.12,
              "Self-Help Group (SHG / Bachat Gat) accounts"),
    PhotoSize("2x2", "US Visa / OCI (2×2 in)", 51, 51, 0.60, 0.12,
              "US visa, OCI card and other 51 × 51 mm photos"),
)}
# Framing used for user-defined sizes.
CUSTOM_HEAD, CUSTOM_TOP = 0.62, 0.10

_session = None
_session_lock = threading.Lock()


def _rembg_session():
    global _session
    with _session_lock:
        if _session is None:
            from rembg import new_session  # optional dependency, imported lazily
            _session = new_session(MODEL)
        return _session


@dataclass
class Head:
    crown: float   # y of the top of the head, px
    chin: float    # y of the chin, px
    center: float  # x of the face centre, px
    width: float   # face width, px


@dataclass
class Cutout:
    original: Image.Image  # RGB, upright and resized; same pixels as `image`
    image: Image.Image     # RGBA, background transparent
    heads: list[Head]      # largest first

    # The main head, for single-person framing.
    @property
    def crown(self) -> float:
        return self.heads[0].crown

    @property
    def chin(self) -> float:
        return self.heads[0].chin

    @property
    def center(self) -> float:
        return self.heads[0].center


FACE_MODEL = str(Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx")


def _faces(rgb: np.ndarray) -> list[tuple[float, float, float, float]]:
    """All faces as (centre x, top y, chin y, width), largest first, using YuNet."""
    h, w = rgb.shape[:2]
    det = cv2.FaceDetectorYN.create(FACE_MODEL, "", (w, h), score_threshold=0.7)
    _, faces = det.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if faces is None:
        return []
    out = []
    for f in sorted(faces, key=lambda f: -f[2] * f[3]):
        x, y, fw, fh = (float(v) for v in f[:4])
        eye_y = float(f[5] + f[7]) / 2
        mouth_y = float(f[11] + f[13]) / 2
        # The chin sits about 0.8 eye-to-mouth distances below the mouth.
        chin = max(y + fh, mouth_y + 0.8 * (mouth_y - eye_y))
        out.append((x + fw / 2, y, chin, fw))
    # Ignore tiny background faces (posters, photos on the wall).
    return [f for f in out if f[3] >= 0.35 * out[0][3]] if out else []


def cut_out(data: bytes) -> Cutout:
    """Remove the background and locate the head."""
    from rembg import remove

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((MAX_INPUT_PX, MAX_INPUT_PX), Image.LANCZOS)
    rgba = remove(img, session=_rembg_session(), post_process_mask=True)
    alpha = np.asarray(rgba.getchannel("A"))
    if (alpha > 128).sum() < 0.02 * alpha.size:
        raise ValueError("No person found in this photo")

    ys, xs = np.nonzero(alpha > 128)
    heads = []
    for center, top, chin, fw in _faces(np.asarray(img)):
        # Crown: top of the person mask over the middle of the face (includes hair).
        cols = alpha[:, max(0, int(center - fw * 0.25)):int(center + fw * 0.25)] > 128
        rows = np.nonzero(cols[: int(top) + 1].any(axis=1))[0]
        crown = float(rows[0]) if len(rows) else top - 0.25 * (chin - top)
        heads.append(Head(crown, chin, center, fw))
    if not heads:
        # No face found: assume a head-and-shoulders shot.
        top, left, right = ys.min(), xs.min(), xs.max()
        center = float(xs[ys < top + (ys.max() - top) * 0.3].mean())
        width = 0.5 * float(right - left)
        heads.append(Head(float(top), float(top) + 1.1 * width, center, width))
    return Cutout(img, rgba, heads)
