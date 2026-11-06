"""Obtención y clasificación del texto de cada página."""

from __future__ import annotations

import logging

import cv2
import numpy as np

from note_cascade.layout import text_ocr
from note_cascade.music_model import terms
from note_cascade.omr.octave_lines import label_kind
from note_cascade.layout.text_classifier import TextElement, classify_page, merge_words_into_lines, staves_to_norm
from note_cascade.pdf_processing.pdf_document import TextSpan

log = logging.getLogger(__name__)


def page_text_spans(source_page, processed, use_ocr: bool = True) -> tuple[list[TextSpan], str]:
    """Texto de la página.

    Capa de texto del PDF si existe y, además, OCR por franjas encima y debajo de cada
    sistema: ahí están las indicaciones pequeñas que muchos PDF dibujan como trazados
    («8va - - -», «♩. = 75», dinámicas) y que no aparecen en la capa de texto.
    """
    info = getattr(source_page, "pdf_info", None)
    vector = info is not None and info.words and info.kind in ("vector", "mixed")
    spans: list[TextSpan] = merge_words_into_lines(info.words) if (info is not None and info.words) else []
    origin = "pdf" if spans else "ninguno"
    if not use_ocr or not text_ocr.available():
        return spans, origin
    try:
        if not vector:
            spans = spans + text_ocr.ocr_image(processed.original)
            origin = "ocr"
        band_spans = _band_ocr(processed)
        spans = spans + [b for b in band_spans if not any(_overlap(b, s) > 0.25 for s in spans)]
        if band_spans and origin == "pdf":
            origin = "pdf+ocr"
    except Exception as exc:  # el OCR es auxiliar: nunca debe impedir la conversión
        log.warning("OCR falló en la página %s: %s", source_page.index + 1, exc)
    return spans, origin


def _overlap(a: TextSpan, b: TextSpan) -> float:
    ix = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    iy = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    inter = ix * iy
    area = max(1e-9, min(a.width * a.height, b.width * b.height))
    return inter / area


def _band_ocr(processed) -> list[TextSpan]:
    """OCR ampliado de las franjas que rodean cada sistema (coordenadas normalizadas)."""
    staves, systems = staves_to_norm(processed)
    if not systems:
        return []
    img = processed.original
    h, w = img.shape[:2]
    il_px = float(np.median([s.interline for s in staves])) * h if staves else 20.0
    zoom = float(np.clip(32.0 / max(il_px, 1.0), 1.0, 2.5))
    result: list[TextSpan] = []
    for sys in systems:
        bands = [(sys.y0 * h - 8 * il_px, sys.y0 * h + 0.5 * il_px, False),
                 (sys.y1 * h - 0.5 * il_px, sys.y1 * h + 6 * il_px, False)]
        # entre pentagramas del mismo sistema: solo para las 8va/8vb de la mano izquierda y las dinámicas
        # (ahí hay muchas cabezas y plicas que el OCR confunde con letras)
        sys_staves = [staves[i] for i in getattr(sys, "staves", [])] if staves else []
        for a, b in zip(sys_staves, sys_staves[1:]):
            bands.append((a.bottom * h - 0.5 * il_px, b.top * h + 0.5 * il_px, True))
        for top, bottom, between in bands:
            y0, y1 = int(max(0, top)), int(min(h, bottom))
            x0, x1 = int(max(0, sys.x0 * w - 2 * il_px)), int(min(w, sys.x1 * w + 2 * il_px))
            if y1 - y0 < 8 or x1 - x0 < 8:
                continue
            crop = img[y0:y1, x0:x1]
            if zoom > 1.05:
                crop = cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
            pad = cv2.copyMakeBorder(crop, 30, 30, 30, 30, cv2.BORDER_CONSTANT,
                                     value=(255, 255, 255) if crop.ndim == 3 else 255)
            ph, pw = pad.shape[:2]
            for s in text_ocr.ocr_image(pad, max_side=6000, min_score=0.6):
                # coordenadas del recorte ampliado → página original normalizada
                def cx(v: float) -> float:
                    return (x0 + (v * pw - 30) / zoom) / w

                def cy(v: float) -> float:
                    return (y0 + (v * ph - 30) / zoom) / h

                nx0, nx1, ny0, ny1 = cx(s.x0), cx(s.x1), cy(s.y0), cy(s.y1)
                if nx1 <= nx0 or ny1 <= ny0:
                    continue
                if between and not (label_kind(s.text) or terms.is_dynamic(s.text.strip(" ."))):
                    continue
                result.append(TextSpan(s.text, nx0, ny0, nx1, ny1, size=(ny1 - ny0) * 0.8, source="ocr",
                                       confidence=s.confidence))
    # quitar repeticiones (las franjas de dos sistemas pueden solaparse)
    unique: list[TextSpan] = []
    for s in result:
        if not any(_overlap(s, u) > 0.5 and s.text == u.text for u in unique):
            unique.append(s)
    return unique


def make_multirest_checker(processed):
    """Devuelve f(índice de sistema, x normalizada) -> ¿hay un silencio de varios compases ahí?"""
    geom = processed.geometry
    img = processed.image
    if not geom.systems:
        return None
    _, bw = cv2.threshold(img, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    def check(sys_idx: int, nx: float) -> bool:
        if sys_idx is None or sys_idx >= len(geom.systems):
            return False
        sys = geom.systems[sys_idx]
        mid_y = (sys.y0 + sys.y1) / 2
        _, ny = processed.processed_to_normalized((sys.x0 + sys.x1) / 2, mid_y)
        px, _ = processed.normalized_to_processed(nx, ny)
        spans = sys.measure_spans(geom.interline)
        span = next(((a, b) for a, b in spans if a <= px <= b), None)
        if span is None:
            return False
        staff = geom.staves[sys.staves[0]]
        y = int(round(staff.y(2, px)))
        il = geom.interline
        a, b = int(span[0] + il), int(span[1] - il)
        if b - a < 3 * il:
            return False
        band = bw[max(0, int(y - il * 0.45)):int(y + il * 0.45) + 1, a:b]
        if band.size == 0:
            return False
        cols = band.mean(axis=0) > 0.85  # columnas casi totalmente negras en la franja central
        run = _longest_run(cols)
        return run > 0.35 * (b - a)

    return check


def _longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def classify_text(source_page, processed, page_number: int, is_first_page: bool, use_ocr: bool = True,
                  piano_like: bool = False) -> tuple[list[TextElement], str]:
    spans, origin = page_text_spans(source_page, processed, use_ocr)
    staves, systems = staves_to_norm(processed)
    h, w = processed.original.shape[:2]
    checker = make_multirest_checker(processed)
    elements = classify_page(spans, staves, systems, page_number, page_aspect=h / max(1, w),
                             is_first_page=is_first_page, multirest_at=checker, piano_like=piano_like)
    return elements, origin
