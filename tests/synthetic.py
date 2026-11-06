"""Partituras sintéticas con resultado conocido (para pruebas).

Se construyen con el modelo interno, se exportan a MusicXML con el escritor de
la aplicación y, para las pruebas de reconocimiento, se dibujan con Verovio
(dependencia solo de desarrollo, LGPL-3.0). Así se conoce exactamente qué
notas debería reconocer cada motor.
"""

from __future__ import annotations

import io
from fractions import Fraction as F
from pathlib import Path

from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import (
    ClefChange,
    Direction,
    KeyChange,
    Measure,
    Note,
    PartInfo,
    Score,
    TempoMark,
    new_id,
    notation_for_duration,
)


def _note(score: Score, measure: int, onset, dur, name: str | None, staff: int = 1, voice: int = 1,
          lyric: str | None = None, tie_start=False, tie_stop=False, arts=None, fifths: int = 0) -> Note:
    onset, dur = F(onset), F(dur)
    if name is None:
        n = Note(new_id(), 0, staff, voice, measure, onset, dur, rest=True, source="sintetico")
    else:
        step = name[0]
        alter = {"#": 1, "b": -1, "": 0}[name[1:-1]] if len(name) > 2 else 0
        octave = int(name[-1])
        n = Note(new_id(), 0, staff, voice, measure, onset, dur, step=step, alter=alter, octave=octave,
                 midi=P.midi_from(step, alter, octave), source="sintetico", tie_start=tie_start, tie_stop=tie_stop,
                 lyric=lyric, articulations=list(arts or []))
        key_alt = P.key_alterations(fifths)
        if alter != key_alt[step]:
            n.accidental_shown = P.ALTER_TO_ACCIDENTAL[alter]
    found = notation_for_duration(dur)
    if found:
        n.type_name, n.dots, n.tuplet = found
    n.hand = "R" if staff == 1 else "L"
    score.notes.append(n)
    return n


def piano_score(title: str = "Estudio de prueba", composer: str = "Compositor Ficticio", lyrics: bool = False,
                fifths: int = 1, measures: int = 8) -> Score:
    """Pieza para piano a dos manos en 4/4 con acordes, silencios, puntillos, alteraciones y ligaduras."""
    s = Score(title=title, composer=composer)
    s.parts.append(PartInfo("P1", "Piano", "Pno.", 2, 0, True))
    for i in range(measures):
        s.measures.append(Measure(i, str(i + 1), (4, 4), time_sig_change=i == 0, new_system=(i % 4 == 0 and i > 0)))
    s.keys.append(KeyChange(0, 0, fifths))
    s.clefs.append(ClefChange(0, 1, 0, F(0), "G", 2))
    s.clefs.append(ClefChange(0, 2, 0, F(0), "F", 4))
    s.tempos.append(TempoMark(0, F(0), 96.0, text="Moderato", per_minute=96, source="sintetico"))
    s.directions.append(Direction("dynamic", 0, F(0), 0, 1, "p"))
    s.directions.append(Direction("dynamic", 4, F(0), 0, 1, "f"))
    words = iter("La mú si ca sue na sin sa ber leer pa ti tu ras ho la fa ci li tar".split())
    rh_patterns = [
        [(0, 1, "D5"), (1, 1, "B4"), (2, F(3, 2), "G4"), (F(7, 2), F(1, 2), "A4")],
        [(0, F(1, 2), "B4"), (F(1, 2), F(1, 2), "C5"), (1, 1, "D5"), (2, 1, None), (3, 1, "F#5")],
        [(0, 2, "G5"), (2, 1, "E5"), (3, 1, "C#5")],
        [(0, 4, "D5")],
    ]
    for m in range(measures):
        for onset, dur, name in rh_patterns[m % 4]:
            ly = next(words, None) if (lyrics and name) else None
            _note(s, m, onset, dur, name, 1, 1, lyric=ly, fifths=fifths)
        # Mano izquierda: acordes y bajo
        if m % 4 == 3:
            for nm in ("G2", "D3", "B3"):
                _note(s, m, 0, 2, nm, 2, 5, fifths=fifths)
            _note(s, m, 2, 2, None, 2, 5)
        elif m % 2 == 0:
            _note(s, m, 0, 2, "G2", 2, 5, fifths=fifths)
            _note(s, m, 2, 1, "D3", 2, 5, fifths=fifths)
            _note(s, m, 3, 1, "B2", 2, 5, fifths=fifths)
        else:
            for nm in ("C3", "E3", "G3"):
                _note(s, m, 0, 2, nm, 2, 5, fifths=fifths)
            for nm in ("D3", "F#3", "A3"):
                _note(s, m, 2, 2, nm, 2, 5, fifths=fifths)
    s.sort_notes()
    return s


def melody_score(title: str = "Melodía") -> Score:
    s = Score(title=title)
    s.parts.append(PartInfo("P1", "Flauta", "Fl.", 1, 73, False))
    for i in range(4):
        s.measures.append(Measure(i, str(i + 1), (3, 4), time_sig_change=i == 0))
    s.keys.append(KeyChange(0, 0, -1))
    s.clefs.append(ClefChange(0, 1, 0, F(0), "G", 2))
    seq = [[(0, 1, "F4"), (1, 1, "A4"), (2, 1, "C5")], [(0, 2, "Bb4"), (2, 1, "G4")],
           [(0, F(3, 2), "A4"), (F(3, 2), F(1, 2), "G4"), (2, 1, "F4")], [(0, 3, "F4")]]
    for m, items in enumerate(seq):
        for onset, dur, name in items:
            _note(s, m, onset, dur, name, 1, 1, fifths=-1)
    s.sort_notes()
    return s


def to_musicxml_bytes(score: Score) -> bytes:
    from note_cascade.musicxml.writer import score_to_bytes
    return score_to_bytes(score)


def verovio_svg_pages(score: Score, scale: int = 45, page_width: int = 2100, page_height: int = 2970) -> list[str]:
    import verovio
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": page_width, "pageHeight": page_height, "scale": scale, "adjustPageHeight": False,
                   "header": "auto", "footer": "none", "breaks": "auto", "pageMarginLeft": 80,
                   "pageMarginRight": 80, "pageMarginTop": 60, "lyricSize": 4.0})
    ok = tk.loadData(to_musicxml_bytes(score).decode("utf-8"))
    if not ok:
        raise RuntimeError("Verovio no pudo cargar el MusicXML sintético")
    return [tk.renderToSVG(i + 1) for i in range(tk.getPageCount())]


def _flatten_svg(svg: str) -> str:
    """Adapta el SVG de Verovio a renderizadores sencillos (SVG Tiny de Qt).

    Verovio anida un <svg viewBox> interior y usa ``currentColor`` mediante CSS;
    se sustituye el SVG interior por un grupo con la escala equivalente.
    """
    import re
    outer = re.search(r'<svg width="([\d.]+)px" height="([\d.]+)px"', svg)
    inner = re.search(r'<svg class="definition-scale"[^>]*viewBox="0 0 ([\d.]+) ([\d.]+)"[^>]*>', svg)
    if not outer or not inner:
        return svg
    sx = float(outer.group(1)) / float(inner.group(1))
    sy = float(outer.group(2)) / float(inner.group(2))
    svg = svg[:inner.start()] + f'<g transform="scale({sx},{sy})" fill="black" stroke="black" stroke-width="0">' + \
        svg[inner.end():]
    idx = svg.rfind("</svg>")
    idx = svg.rfind("</svg>", 0, idx)
    if idx >= 0:
        svg = svg[:idx] + "</g>" + svg[idx + len("</svg>"):]
    svg = re.sub(r"<style[^>]*>.*?</style>", "", svg, flags=re.S)
    svg = svg.replace("currentColor", "black")
    return svg


def svg_to_png(svg: str, path: Path, width_px: int = 2480) -> Path:
    svg = _flatten_svg(svg)
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = renderer.defaultSize()
    height_px = int(width_px * size.height() / max(1, size.width()))
    img = QImage(width_px, height_px, QImage.Format_RGB888)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, width_px, height_px))
    p.end()
    img.save(str(path))
    return path


def render_png(score: Score, path: Path, width_px: int = 2480, page: int = 0) -> Path:
    """Imagen de una página (renderizada con MuPDF, que sí dibuja los textos de Verovio)."""
    import pymupdf as fitz
    svg = _flatten_svg(verovio_svg_pages(score)[page])
    doc = fitz.open(stream=svg.encode("utf-8"), filetype="svg")
    zoom = width_px / doc[0].rect.width
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(str(path))
    return path


def render_pdf_vector(score: Score, path: Path) -> Path:
    """PDF vectorial (SVG de Verovio convertido con PyMuPDF), escalado a A4."""
    import pymupdf as fitz
    out = fitz.open()
    for svg in verovio_svg_pages(score):
        src = fitz.open(stream=_flatten_svg(svg).encode("utf-8"), filetype="svg")
        pdf = fitz.open("pdf", src.convert_to_pdf())
        page = out.new_page(width=595, height=842)
        page.show_pdf_page(page.rect, pdf, 0)
    out.save(str(path))
    return path


def pdf_with_image(image_path: Path, path: Path, rotate: int = 0, margin_pt: float = 36) -> Path:
    """PDF que solo contiene una imagen (escaneo, foto o captura dentro de un documento)."""
    import pymupdf as fitz
    from PIL import Image
    with Image.open(image_path) as im:
        w, h = im.size
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=88)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    scale = min((595 - 2 * margin_pt) / w, (842 - 2 * margin_pt) / h)
    rect = fitz.Rect(margin_pt, margin_pt, margin_pt + w * scale, margin_pt + h * scale)
    page.insert_image(rect, stream=buf.getvalue())
    page.insert_text((margin_pt, 830), "Edición de prueba © 2026 · Referencia 123-45", fontsize=7)
    if rotate:
        page.set_rotation(rotate)
    doc.save(str(path))
    return path


def photo_like(image_path: Path, path: Path, angle: float = 2.5, perspective: bool = True, jpeg_quality: int = 60) -> Path:
    """Simula una foto de móvil: perspectiva, inclinación, sombra y compresión JPEG."""
    import cv2
    import numpy as np
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    canvas = np.full((int(h * 1.2), int(w * 1.2), 3), (60, 70, 80), np.uint8)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    if perspective:
        dst = np.float32([[w * 0.06, h * 0.05], [w * 1.14, h * 0.09], [w * 1.10, h * 1.15], [w * 0.10, h * 1.12]])
    else:
        dst = np.float32([[w * 0.1, h * 0.1], [w * 1.1, h * 0.1], [w * 1.1, h * 1.1], [w * 0.1, h * 1.1]])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, m, (canvas.shape[1], canvas.shape[0]), dst=canvas.copy(),
                                 borderMode=cv2.BORDER_TRANSPARENT)
    rot = cv2.getRotationMatrix2D((warped.shape[1] / 2, warped.shape[0] / 2), angle, 1.0)
    warped = cv2.warpAffine(warped, rot, (warped.shape[1], warped.shape[0]), borderValue=(60, 70, 80))
    yy, xx = np.mgrid[0:warped.shape[0], 0:warped.shape[1]]
    shade = (0.7 + 0.3 * (xx / warped.shape[1])).astype(np.float32)
    photo = np.clip(warped.astype(np.float32) * shade[..., None], 0, 255).astype(np.uint8)
    cv2.imwrite(str(path), photo, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    return path


def note_events(score: Score) -> list[tuple[int, float, float, int]]:
    """(compás, inicio, duración, midi) de las notas con altura, para comparar."""
    return sorted((n.measure, float(n.onset), float(n.duration), n.midi) for n in score.notes
                  if not n.rest and not n.grace)


def accuracy(truth: Score, result: Score) -> dict[str, float]:
    """Métricas de reconocimiento frente a la referencia (alineando por compás)."""
    from collections import Counter
    from note_cascade.music_validation.compare import align
    pairs = align(truth, result)
    tp_pitch = tp_full = 0
    n_truth = sum(1 for n in truth.notes if not n.rest and not n.grace)
    n_result = sum(1 for n in result.notes if not n.rest and not n.grace)
    for i, j in pairs:
        if i is None or j is None:
            continue
        a = [n for n in truth.notes_in_measure(i) if not n.rest and not n.grace]
        b = [n for n in result.notes_in_measure(j) if not n.rest and not n.grace]
        tp_pitch += sum((Counter(n.midi for n in a) & Counter(n.midi for n in b)).values())
        tp_full += sum((Counter((n.onset, n.duration, n.midi) for n in a) &
                        Counter((n.onset, n.duration, n.midi) for n in b)).values())
    return {
        "notas_referencia": n_truth, "notas_reconocidas": n_result,
        "precision_altura": tp_pitch / max(1, n_result), "exhaustividad_altura": tp_pitch / max(1, n_truth),
        "notas_exactas": tp_full / max(1, n_truth),
        "notas_omitidas": max(0, n_truth - tp_pitch), "notas_adicionales": max(0, n_result - tp_pitch),
    }
