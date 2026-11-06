"""Preparación de páginas antes del reconocimiento.

Se decide qué correcciones aplicar según el tipo real de imagen (vectorial
limpia, captura, escaneo o fotografía) para no deteriorar imágenes que ya
están limpias. La imagen original nunca se modifica: todas las operaciones se
hacen sobre copias y se registra la matriz que relaciona las coordenadas de
la imagen procesada con las de la página original (para la revisión visual).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from note_cascade.image_processing import transforms as T
from note_cascade.image_processing.quality import ImageQuality, assess
from note_cascade.image_processing.staff_detection import PageGeometry, binarize, detect_staves, orientation_score

log = logging.getLogger(__name__)

LEVELS = ("auto", "ninguna", "suave", "fuerte")


@dataclass
class PreprocessOptions:
    level: str = "auto"
    target_interline: float = 22.0
    min_interline: float = 15.0
    max_interline: float = 38.0
    allow_rotation: bool = True
    max_dpi: float = 600.0


@dataclass
class ProcessedPage:
    page_index: int
    original: np.ndarray                 # RGB original (copia de referencia)
    image: np.ndarray                    # gris procesado (entrada para los motores)
    matrix: np.ndarray                   # original -> procesado
    dpi: float | None
    quality: ImageQuality
    geometry: PageGeometry               # en coordenadas de la imagen procesada
    original_geometry: PageGeometry | None = None
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_music(self) -> bool:
        return self.geometry.has_music

    @property
    def original_size(self) -> tuple[int, int]:
        return self.original.shape[1], self.original.shape[0]

    def processed_to_normalized(self, x: float, y: float) -> tuple[float, float]:
        inv = T.invert(self.matrix)
        ox, oy = T.apply_matrix(inv, x, y)
        w, h = self.original_size
        return ox / w, oy / h

    def normalized_to_processed(self, nx: float, ny: float) -> tuple[float, float]:
        w, h = self.original_size
        return T.apply_matrix(self.matrix, nx * w, ny * h)

    def processed_box_to_normalized(self, x: float, y: float, bw: float, bh: float) -> tuple[float, float, float, float]:
        pts = [self.processed_to_normalized(px, py) for px, py in
               ((x, y), (x + bw, y), (x, y + bh), (x + bw, y + bh))]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _ink_bbox(gray: np.ndarray, interline: float) -> tuple[float, float, float, float] | None:
    """Rectángulo que contiene toda la tinta significativa, con un margen de seguridad."""
    import cv2
    _, bw = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # ignorar motas aisladas y bordes oscuros de escaneo pegados al borde
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    h, w = bw.shape
    border = max(4, int(min(h, w) * 0.01))
    bw[:border, :] = 0
    bw[-border:, :] = 0
    bw[:, :border] = 0
    bw[:, -border:] = 0
    rows = np.flatnonzero(bw.sum(axis=1) > 2)
    cols = np.flatnonzero(bw.sum(axis=0) > 2)
    if len(rows) == 0 or len(cols) == 0:
        return None
    m = max(interline * 3, 20)
    x0, x1 = max(0, cols[0] - m), min(w, cols[-1] + m)
    y0, y1 = max(0, rows[0] - m), min(h, rows[-1] + m)
    if x0 < w * 0.04 and y0 < h * 0.04 and x1 > w * 0.96 and y1 > h * 0.96:
        return None
    return float(x0), float(y0), float(x1), float(y1)


def choose_dpi(origin: str, kind_hint: str, native_dpi: float | None, setting: str) -> float | None:
    if origin != "pdf":
        return None
    if setting and setting != "auto":
        try:
            return float(setting)
        except ValueError:
            pass
    if kind_hint in ("raster", "mixed") and native_dpi:
        return float(min(600, max(150, round(native_dpi / 25) * 25)))
    return 300.0


def preprocess(rgb: np.ndarray, page_index: int, dpi: float | None, options: PreprocessOptions,
               rerender: Callable[[float], np.ndarray] | None = None) -> ProcessedPage:
    """Prepara una página. ``rerender(dpi)`` permite volver a rasterizar un PDF."""
    steps: list[str] = []
    warnings: list[str] = []
    quality = assess(rgb)
    original = rgb
    original_geometry = detect_staves(T.to_gray(rgb))

    # Resolución: para PDF se vuelve a rasterizar en lugar de ampliar la imagen.
    if rerender is not None and dpi and original_geometry.has_music:
        il = original_geometry.interline
        new_dpi = None
        if il < options.min_interline:
            new_dpi = min(options.max_dpi, dpi * options.target_interline / il)
        elif il > options.max_interline * 1.3:
            new_dpi = max(150.0, dpi * options.target_interline / il)
        if new_dpi and abs(new_dpi - dpi) / dpi > 0.15:
            steps.append(f"Página rasterizada de nuevo a {new_dpi:.0f} ppp (antes {dpi:.0f} ppp) "
                         f"porque el interlineado era de {il:.1f} píxeles.")
            dpi = new_dpi
            rgb = rerender(dpi)
            original = rgb
            quality = assess(rgb)
            original_geometry = detect_staves(T.to_gray(rgb))

    level = options.level if options.level in LEVELS else "auto"
    gray = T.to_gray(rgb)
    matrix = T.identity()
    if rgb.ndim == 3:
        steps.append("Conversión a escala de grises.")

    def apply(img_m: tuple[np.ndarray, np.ndarray], description: str) -> None:
        nonlocal gray, matrix
        gray, m = img_m
        matrix = m @ matrix
        steps.append(description)

    kind = quality.kind
    strong = level == "fuerte" or (level == "auto" and kind == "foto")
    light = level == "suave" or (level == "auto" and kind == "escaneo")

    # 1) Perspectiva (solo fotografías)
    if level != "ninguna" and strong:
        quad = T.find_page_quad(gray)
        if quad is not None:
            apply(T.warp_quad(gray, quad), "Corrección de perspectiva: se recortó y enderezó la hoja fotografiada.")

    # 2) Fondo, sombras y contraste
    if level != "ninguna" and (strong or light):
        if quality.background < 240 or quality.uneven_lighting > 0.06 or strong:
            gray = T.normalize_background(gray, original_geometry.interline or None)
            steps.append("Eliminación del fondo gris y de las sombras (normalización de iluminación).")
        if strong and quality.noise > 0.15:
            gray = T.denoise(gray, 6.0)
            steps.append("Reducción de ruido (compresión JPEG, grano).")
        elif quality.noise > 0.25:
            gray = T.remove_specks(gray, 2)
            steps.append("Eliminación de motas aisladas.")
        if quality.contrast < 0.45 or strong:
            gray = T.stretch_contrast(gray)
            steps.append("Mejora del contraste.")

    geom = detect_staves(gray)

    # 2b) Inclinación fuerte: la detección por franjas necesita líneas casi
    # horizontales, así que primero se estima el ángulo con Hough.
    if level != "ninguna" and (not geom.has_music or len(geom.staves) < len(original_geometry.staves)):
        from note_cascade.image_processing.staff_detection import estimate_runs, estimate_skew
        bw = binarize(gray)
        _thick, il = estimate_runs(bw)
        angle = estimate_skew(bw, il or 20.0)
        if abs(angle) > 0.3:
            rotated, m = T.rotate(gray, angle)
            g_rot = detect_staves(rotated)
            if len(g_rot.staves) > len(geom.staves):
                apply((rotated, m), f"Corrección de inclinación de {angle:+.2f}° (estimada con líneas largas).")
                geom = g_rot

    # 3) Orientación (90°/180°)
    if level != "ninguna" and options.allow_rotation:
        if not geom.has_music:
            rotated, m = T.rotate90(gray, 1)
            g2 = detect_staves(rotated)
            if g2.has_music and len(g2.staves) >= 2:
                apply((rotated, m), "La página estaba girada 90°: se corrigió la orientación.")
                geom = g2
        if geom.has_music and len(geom.staves) >= 2:
            bw = binarize(gray)
            score = orientation_score(bw, geom)
            if score < -0.04:
                rot, m = T.rotate90(gray, 2)
                g3 = detect_staves(rot)
                if g3.has_music and orientation_score(binarize(rot), g3) > 0.04:
                    apply((rot, m), "La página estaba al revés: se giró 180°.")
                    geom = g3

    # 4) Inclinación de los pentagramas
    if level != "ninguna" and geom.has_music and abs(geom.skew_deg) > 0.12:
        angle = geom.skew_deg
        apply(T.rotate(gray, angle), f"Corrección de inclinación de {angle:+.2f}°.")
        geom = detect_staves(gray)

    # 4b) Umbralización adaptativa: solo si, tras normalizar la iluminación, el contraste sigue siendo bajo
    if level != "ninguna" and strong and geom.has_music:
        lo, med = np.percentile(gray, [1, 50])
        if (med - lo) / 255.0 < 0.35:
            gray = T.adaptive_threshold(gray, geom.interline)
            steps.append("Umbralización adaptativa (el contraste seguía siendo bajo tras corregir la iluminación).")
            geom = detect_staves(gray)

    # 4c) Recorte de márgenes vacíos (solo zonas sin tinta). Únicamente en el nivel «fuerte» elegido a
    # mano: en pruebas con capturas, recortar cambió el resultado de Audiveris a peor (275 → 265 notas),
    # y los márgenes blancos no perjudican a los motores.
    if level == "fuerte" and geom.has_music:
        box = _ink_bbox(gray, geom.interline)
        if box is not None:
            h, w = gray.shape[:2]
            x0, y0, x1, y1 = box
            if (x1 - x0) * (y1 - y0) < 0.8 * w * h:
                apply(T.crop(gray, box), "Recorte de los márgenes vacíos de la página.")
                geom = detect_staves(gray)

    # 5) Escala según el interlineado
    if geom.has_music:
        il = geom.interline
        factor = 1.0
        if il < options.min_interline:
            factor = min(4.0, options.target_interline / il)
        elif il > options.max_interline:
            factor = max(0.35, options.target_interline / il)
        if abs(factor - 1) > 0.08:
            verb = "Ampliación" if factor > 1 else "Reducción"
            apply(T.scale(gray, factor), f"{verb} ×{factor:.2f} para obtener un interlineado de "
                                         f"{il * factor:.0f} píxeles (antes {il:.1f}).")
            geom = detect_staves(gray)
    else:
        warnings.append("No se encontraron pentagramas en esta página: puede que no contenga música o que la "
                        "imagen sea demasiado pequeña o borrosa.")

    if not steps or steps == ["Conversión a escala de grises."]:
        steps.append("La imagen ya estaba limpia: no se aplicaron correcciones para no deteriorarla.")

    return ProcessedPage(page_index, original, gray, matrix, dpi, quality, geom, original_geometry,
                         steps, warnings)
