"""Find card-shaped rectangles in a flat image (scans, image-only PDFs)."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from .card import CARD_ASPECT


def find_cards(img: Image.Image, max_cards: int = 2,
               aspect_tol: float = 0.12, min_area: float = 0.02) -> list[tuple[int, int, int, int]]:
    """Return up to `max_cards` boxes (x0, y0, x1, y1) in reading order.

    Cards are found as large non-white blobs whose shape is close to CR80.
    """
    g = np.array(img.convert("L"))
    H, W = g.shape
    mask = (g < 235).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < min_area * W * H:
            continue
        aspect = max(w, h) / min(w, h)
        if abs(aspect - CARD_ASPECT) / CARD_ASPECT <= aspect_tol:
            boxes.append((x, y, x + w, y + h))

    boxes = sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)[:max_cards]
    # Front first: top-to-bottom rows, then left-to-right.
    row = max(1, H // 20)
    return sorted(boxes, key=lambda b: (b[1] // row, b[0]))
