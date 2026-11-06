"""Evaluación de la calidad de la imagen para decidir el preprocesado."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass
class ImageQuality:
    width: int
    height: int
    background: float          # nivel medio del fondo (0..255)
    ink: float                 # nivel de la tinta (0..255)
    contrast: float            # (fondo - tinta) / 255
    uneven_lighting: float     # variación del fondo (0..1)
    saturation: float          # saturación media del fondo (0..1); fotos en color
    noise: float               # proporción de motas aisladas
    blur: float                # nitidez (varianza del laplaciano normalizada); bajo = borroso
    bilevel: float             # fracción de píxeles casi blancos o casi negros
    dark_border: float         # fracción del borde que es oscura (fotos con mesa/fondo)
    kind: str = "limpia"       # limpia | escaneo | foto | captura
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        return d


def assess(rgb: np.ndarray) -> ImageQuality:
    h, w = rgb.shape[:2]
    scale = min(1.0, 1200.0 / max(h, w))
    small = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA) \
        if scale < 1 else rgb
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY) if small.ndim == 3 else small
    k = max(15, int(min(gray.shape) / 25) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.medianBlur(bg, 5)
    background = float(np.median(bg))
    p_lo, p_hi = np.percentile(bg, [5, 95])
    uneven = float((p_hi - p_lo) / 255.0)
    ink = float(np.percentile(gray, 1.0))
    contrast = max(0.0, (background - ink) / 255.0)
    if small.ndim == 3:
        hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
        bg_mask = gray > (background - 40)
        saturation = float(hsv[..., 1][bg_mask].mean() / 255.0) if bg_mask.any() else 0.0
    else:
        saturation = 0.0
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    blur = float(lap.var() / (max(1.0, contrast * 255.0) ** 2))
    bilevel = float(((gray < 40) | (gray > 215)).mean())
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    if n > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        noise = float((areas <= 2).sum() / max(1, len(areas)))
    else:
        noise = 0.0
    border = np.concatenate([gray[:4].ravel(), gray[-4:].ravel(), gray[:, :4].ravel(), gray[:, -4:].ravel()])
    dark_border = float((border < background * 0.6).mean())

    q = ImageQuality(w, h, background, ink, contrast, uneven, saturation, noise, blur, bilevel, dark_border)
    reasons = []
    if dark_border > 0.25 or uneven > 0.22 or (saturation > 0.12 and uneven > 0.08):
        q.kind = "foto"
        if dark_border > 0.25:
            reasons.append("bordes oscuros (fondo alrededor de la hoja)")
        if uneven > 0.22:
            reasons.append("iluminación irregular")
        if saturation > 0.12:
            reasons.append("fondo con color")
    elif background >= 248 and noise < 0.05 and uneven < 0.04:
        q.kind = "limpia" if bilevel > 0.9 else "captura"
    else:
        q.kind = "escaneo"
        if background < 235:
            reasons.append("fondo gris")
        if noise >= 0.15:
            reasons.append("ruido o motas")
    if contrast < 0.45:
        reasons.append("contraste bajo")
    if blur < 0.02:
        reasons.append("imagen borrosa")
    q.reasons = tuple(reasons)
    return q
