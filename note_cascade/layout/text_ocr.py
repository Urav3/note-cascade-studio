"""OCR de texto convencional (RapidOCR, Apache-2.0) para páginas rasterizadas.

El OCR solo se usa para *clasificar* textos (títulos, tempos, letras…), nunca
para reconocer notas. Los modelos vienen incluidos en el paquete «rapidocr»
(se instalan con HOMR) y funcionan sin conexión.
"""

from __future__ import annotations

import logging
import threading

import cv2
import numpy as np

from note_cascade.pdf_processing.pdf_document import TextSpan

log = logging.getLogger(__name__)

_engine = None
_lock = threading.Lock()


def available() -> bool:
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:
        return False


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            import logging as _logging
            from rapidocr import RapidOCR
            _logging.getLogger("RapidOCR").setLevel(_logging.WARNING)
            _engine = RapidOCR()
            for name in list(_logging.root.manager.loggerDict):
                if name.lower().startswith("rapidocr"):
                    _logging.getLogger(name).setLevel(_logging.WARNING)
        return _engine


def ocr_image(image: np.ndarray, max_side: int = 2400, min_score: float = 0.5) -> list[TextSpan]:
    """Reconoce texto. Las coordenadas devueltas están normalizadas (0..1) a ``image``."""
    if not available():
        return []
    h, w = image.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    img = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else image
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    engine = _get_engine()
    with _lock:
        result = engine(img)
    spans: list[TextSpan] = []
    if result is None or result.boxes is None:
        return spans
    ih, iw = img.shape[:2]
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        if not text or not text.strip() or float(score) < min_score:
            continue
        pts = np.asarray(box, dtype=float)
        x0, y0 = pts[:, 0].min() / iw, pts[:, 1].min() / ih
        x1, y1 = pts[:, 0].max() / iw, pts[:, 1].max() / ih
        spans.append(TextSpan(text.strip(), x0, y0, x1, y1, size=(y1 - y0) * 0.8, source="ocr",
                              confidence=round(float(score), 3)))
    return spans
