"""Transformaciones de imagen. Todas trabajan sobre copias y devuelven, cuando
corresponde, la matriz 3x3 que lleva coordenadas de la entrada a la salida."""

from __future__ import annotations

import math

import cv2
import numpy as np


def identity() -> np.ndarray:
    return np.eye(3, dtype=np.float64)


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return rgb if rgb.ndim == 2 else cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def rotate(gray: np.ndarray, angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Gira la imagen ``angle_deg`` (positivo = antihorario) ampliando el lienzo."""
    h, w = gray.shape[:2]
    if abs(angle_deg) < 1e-3:
        return gray.copy(), identity()
    a = math.radians(angle_deg)
    cos, sin = abs(math.cos(a)), abs(math.sin(a))
    nw, nh = int(round(w * cos + h * sin)), int(round(w * sin + h * cos))
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    m[0, 2] += (nw - w) / 2
    m[1, 2] += (nh - h) / 2
    border = 255 if gray.ndim == 2 else (255, 255, 255)
    out = cv2.warpAffine(gray, m, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                         borderValue=border)
    return out, np.vstack([m, [0, 0, 1]])


def rotate90(gray: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Gira k·90° en sentido antihorario."""
    k %= 4
    h, w = gray.shape[:2]
    out = np.rot90(gray, k).copy()
    if k == 0:
        return out, identity()
    if k == 1:   # (x, y) -> (y, w-1-x)
        m = np.array([[0, 1, 0], [-1, 0, w - 1], [0, 0, 1]], dtype=np.float64)
    elif k == 2:
        m = np.array([[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1]], dtype=np.float64)
    else:        # (x, y) -> (h-1-y, x)
        m = np.array([[0, -1, h - 1], [1, 0, 0], [0, 0, 1]], dtype=np.float64)
    return out, m


def scale(gray: np.ndarray, factor: float) -> tuple[np.ndarray, np.ndarray]:
    if abs(factor - 1.0) < 1e-3:
        return gray.copy(), identity()
    h, w = gray.shape[:2]
    interp = cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA
    out = cv2.resize(gray, (max(1, int(round(w * factor))), max(1, int(round(h * factor)))), interpolation=interp)
    sx = out.shape[1] / w
    sy = out.shape[0] / h
    return out, np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], dtype=np.float64)


def crop(gray: np.ndarray, box: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    h, w = gray.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    out = gray[y0:y1, x0:x1].copy()
    return out, np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)


def normalize_background(gray: np.ndarray, interline: float | None = None) -> np.ndarray:
    """Elimina fondos grises, sombras e iluminación irregular (división por el fondo)."""
    h, w = gray.shape[:2]
    k = int(max(31, (interline or 20) * 4)) | 1
    scale_f = min(1.0, 1000.0 / max(h, w))
    small = cv2.resize(gray, (max(1, int(w * scale_f)), max(1, int(h * scale_f))), interpolation=cv2.INTER_AREA)
    ks = max(5, int(k * scale_f) | 1)
    bg_small = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks)))
    bg_small = cv2.GaussianBlur(bg_small, (ks, ks), 0)
    bg = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    norm = np.clip(gray.astype(np.float32) / np.maximum(bg, 1.0) * 255.0, 0, 255)
    return norm.astype(np.uint8)


def stretch_contrast(gray: np.ndarray, low_pct: float = 0.5, high_pct: float = 99.0) -> np.ndarray:
    lo, hi = np.percentile(gray, [low_pct, high_pct])
    if hi - lo < 10:
        return gray.copy()
    out = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def clahe(gray: np.ndarray) -> np.ndarray:
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def denoise(gray: np.ndarray, strength: float = 7.0) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, None, h=strength, templateWindowSize=7, searchWindowSize=21)


def remove_specks(gray: np.ndarray, max_area: int = 3) -> np.ndarray:
    """Elimina motas negras diminutas (polvo, ruido de compresión)."""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    out = gray.copy()
    small = np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] <= max_area) + 1
    if len(small):
        mask = np.isin(labels, small)
        out[mask] = 255
    return out


def adaptive_threshold(gray: np.ndarray, interline: float | None = None) -> np.ndarray:
    block = int(max(15, (interline or 20) * 1.5)) | 1
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 12)


def find_page_quad(gray: np.ndarray) -> np.ndarray | None:
    """Busca el contorno de la hoja (4 esquinas) en una fotografía."""
    h, w = gray.shape[:2]
    scale_f = 800.0 / max(h, w)
    small = cv2.resize(gray, (int(w * scale_f), int(h * scale_f)), interpolation=cv2.INTER_AREA)
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < 0.25 * small.shape[0] * small.shape[1] or area > 0.985 * small.shape[0] * small.shape[1]:
        return None
    peri = cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, 0.02 * peri, True)
    if len(approx) != 4 or not cv2.isContourConvex(approx):
        return None
    pts = approx[:, 0, :].astype(np.float64) / scale_f
    return order_quad(pts)


def order_quad(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1)[:, 0]
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float64)


def warp_quad(gray: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tl, tr, br, bl = quad
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float64)
    m = cv2.getPerspectiveTransform(quad.astype(np.float32), dst.astype(np.float32))
    out = cv2.warpPerspective(gray, m, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, m.astype(np.float64)


def apply_matrix(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    v = m @ np.array([x, y, 1.0])
    return float(v[0] / v[2]), float(v[1] / v[2])


def invert(m: np.ndarray) -> np.ndarray:
    return np.linalg.inv(m)
