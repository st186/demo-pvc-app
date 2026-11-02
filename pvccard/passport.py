"""Passport photos: cut the person out and locate each head. The browser does
the framing, background, enhancement and sheet layout from this.

Background removal uses rembg models, downloaded once on first use:
  fast: u2net_human_seg (~176 MB), about 1 s per photo on a CPU
  best: BiRefNet general-lite (~224 MB, MIT), cleaner hair and edges but
        10-30 s and ~7 GB RAM per photo on a CPU (fast on a GPU)
With a GPU (NVIDIA via CUDA, or any GPU on Windows via DirectML) "best"
takes about a second, so it becomes the default.
Install with: pip install -e .[photo]   (GPU: pip install -e .[photo-gpu])
"""

from __future__ import annotations

import io
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_INPUT_PX = 1600
MODELS = {"fast": "u2net_human_seg", "best": "birefnet-general-lite"}


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

# Hardware providers tried before the CPU. DirectML runs on any Windows GPU.
GPU_PROVIDERS = ("CUDAExecutionProvider", "DmlExecutionProvider", "ROCMExecutionProvider")


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False


# GPU state: None until a model session proves whether the GPU works.
_gpu_active: bool | None = None
_gpu_failed = False


def gpu_provider() -> str | None:
    """The GPU provider to try, unless it has already failed on this machine."""
    if _gpu_failed:
        return None
    import onnxruntime as ort
    return next((p for p in GPU_PROVIDERS if p in ort.get_available_providers()), None)


def default_quality() -> str:
    # Only once a session has actually run on the GPU (a GPU package can be
    # installed on a PC whose GPU does not work with it).
    return "best" if available() and _gpu_active and gpu_provider() else "fast"


# Exact download sizes, for the first-run download progress bar.
MODEL_BYTES = {"fast": 175_997_641, "best": 224_005_088}

_sessions: dict[str, object] = {}
_session_lock = threading.Lock()
# Per quality: idle | downloading | loading | ready | failed
_state = {q: "idle" for q in MODELS}
# Recent cut-out durations (seconds), to estimate progress for the next one.
_durations = {q: deque(maxlen=10) for q in MODELS}


def _session_class(quality: str):
    from rembg.sessions import sessions_class
    return {c.name(): c for c in sessions_class}[MODELS[quality]]


def _model_dir(quality: str) -> Path:
    return Path(_session_class(quality).model_dir())


def status(quality: str) -> dict:
    """Model state, download progress (0-1) and expected seconds per photo."""
    state = _state[quality]
    progress = 1.0 if state in ("loading", "ready") else 0.0
    if state == "downloading":
        d = _model_dir(quality)
        # pooch writes a temporary file next to the final one; count both.
        got = sum(f.stat().st_size for f in d.iterdir() if f.is_file()) if d.exists() else 0
        progress = min(0.99, got / MODEL_BYTES[quality])
    recent = _durations[quality]
    if recent:
        seconds = sum(recent) / len(recent)
    else:
        seconds = 1.5 if _gpu_active and gpu_provider() else {"fast": 2.0, "best": 25.0}[quality]
    return {"state": state, "progress": round(progress, 3), "seconds": round(seconds, 1)}


# Runs that must not overlap: GPU sessions (DirectML can't run concurrently)
# and BiRefNet on the CPU (several GB per run).
_run_locks = {q: threading.Lock() for q in MODELS}


def _new_session(quality: str, gpu: str | None):
    import onnxruntime as ort
    opts = ort.SessionOptions()
    if quality == "best" and not gpu:
        # Without the arena the CPU peak drops from ~12 GB to ~7 GB.
        opts.enable_cpu_mem_arena = False
    if gpu == "DmlExecutionProvider" or (quality == "best" and not gpu):
        opts.enable_mem_pattern = False  # required by DirectML
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    providers = [gpu, "CPUExecutionProvider"] if gpu else ["CPUExecutionProvider"]
    cls = _session_class(quality)
    return cls(MODELS[quality], opts, providers=providers)


def _rembg_session(quality: str):
    global _gpu_active, _gpu_failed
    with _session_lock:
        if quality not in _sessions:
            cls = _session_class(quality)
            downloaded = (Path(cls.model_dir()) / f"{cls.name()}.onnx").exists()
            _state[quality] = "loading" if downloaded else "downloading"
            gpu = gpu_provider()
            try:
                try:
                    session = _new_session(quality, gpu)
                except Exception:
                    if not gpu:
                        raise
                    _gpu_failed = True  # GPU unusable here: fall back to the CPU
                    session = _new_session(quality, None)
            except Exception:
                _state[quality] = "failed"
                raise
            if gpu and not _gpu_failed:
                _gpu_active = gpu in session.inner_session.get_providers()
                _gpu_failed = not _gpu_active
            _sessions[quality] = session
            _state[quality] = "ready"
        return _sessions[quality]


def _forget_gpu_sessions() -> None:
    """After a GPU failure mid-run, drop sessions so they reload on the CPU."""
    global _gpu_failed, _gpu_active
    with _session_lock:
        _gpu_failed, _gpu_active = True, False
        _sessions.clear()
        for q in _state:
            _state[q] = "idle"


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


FACE_MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
# Loaded from memory: OpenCV on Windows cannot open paths with non-ASCII
# characters (e.g. a user name in Hindi or Bengali).
_FACE_MODEL_BYTES = np.fromfile(FACE_MODEL, np.uint8)


def _faces(rgb: np.ndarray) -> list[tuple[float, float, float, float]]:
    """All faces as (centre x, top y, chin y, width), largest first, using YuNet."""
    h, w = rgb.shape[:2]
    det = cv2.FaceDetectorYN.create("onnx", _FACE_MODEL_BYTES, np.array([], np.uint8), (w, h), 0.7)
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


def warm_up() -> None:
    """Load (and on first run download) the models ahead of the first photo.

    The fast model loads first and finds out whether the GPU works; with a
    working GPU the best model, then the default, is loaded too.
    """
    if available():
        _rembg_session("fast")
        if _gpu_active:
            _rembg_session("best")


def _remove(img: Image.Image, quality: str) -> Image.Image:
    from rembg import remove

    for attempt in range(2):
        session = _rembg_session(quality)
        on_gpu = bool(_gpu_active and gpu_provider())
        serial = on_gpu or quality == "best"
        try:
            if serial:
                with _run_locks[quality]:
                    return remove(img, session=session, post_process_mask=True)
            return remove(img, session=session, post_process_mask=True)
        except Exception:
            if not on_gpu or attempt:
                raise
            _forget_gpu_sessions()  # GPU failed mid-run: retry once on the CPU
    raise RuntimeError("unreachable")


def cut_out(data: bytes, quality: str = "fast") -> Cutout:
    """Remove the background and locate each head."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((MAX_INPUT_PX, MAX_INPUT_PX), Image.LANCZOS)
    started = time.monotonic()
    rgba = _remove(img, quality)
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
    _durations[quality].append(time.monotonic() - started)
    return Cutout(img, rgba, heads)
