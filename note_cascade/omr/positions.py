"""Posiciones aproximadas para motores que no informan dónde está cada nota.

Se combina la distribución de compases por sistema del MusicXML (saltos de
sistema y de página) con la geometría detectada en la imagen (pentagramas y
barras de compás). La posición vertical de cada nota se calcula a partir de
su altura y de la clave, y la horizontal por proporción dentro del compás.
Todas estas posiciones se marcan como aproximadas.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import PagePosition, Score
from note_cascade.music_model.timeline import measure_lengths

log = logging.getLogger(__name__)


def read_homr_staff_boxes(path: Path | str, width: int, height: int) -> list[tuple[float, float, float, float]]:
    """Cajas (x0, y0, x1, y1) en píxeles de los sistemas detectados por HOMR."""
    boxes = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            _cls, cx, cy, w, h = (float(v) for v in parts)
            boxes.append(((cx - w / 2) * width, (cy - h / 2) * height, (cx + w / 2) * width, (cy + h / 2) * height))
    except (OSError, ValueError):
        return []
    return sorted(boxes, key=lambda b: b[1])


def approximate_positions(score: Score, measure_ranges: dict[int, list[list[int]]], processed: dict[int, object],
                          overwrite: bool = False) -> int:
    """Asigna posiciones aproximadas.

    ``measure_ranges[doc_page] = [[m0, m1, ...] por sistema]`` según el motor.
    ``processed[doc_page] = ProcessedPage``.
    Devuelve el número de compases localizados.
    """
    lengths = measure_lengths(score)
    by_measure = defaultdict(list)
    for n in score.notes:
        by_measure[n.measure].append(n)
    located = 0
    for doc_page, systems in measure_ranges.items():
        proc = processed.get(doc_page)
        if proc is None:
            continue
        geom = proc.geometry
        if len(geom.systems) != len(systems):
            log.info("Página %s: el motor tiene %s sistemas y la imagen %s; no se aproximan posiciones.",
                     doc_page + 1, len(systems), len(geom.systems))
            continue
        for sys_measures, gsys in zip(systems, geom.systems):
            spans = gsys.measure_spans(min_width=geom.interline * 1.5)
            if sys_measures:
                spans = drop_courtesy_span(score, spans, sys_measures[-1], geom.interline, len(sys_measures))
            if len(spans) != len(sys_measures):
                spans = _equal_spans(gsys.x0 + geom.interline * 6, gsys.x1, len(sys_measures),
                                     first_start=gsys.x0)
            for midx, (xa, xb) in zip(sys_measures, spans):
                if midx >= len(score.measures):
                    continue
                m = score.measures[midx]
                if m.position is None or overwrite:
                    nx, ny, nw, nh = proc.processed_box_to_normalized(xa, gsys.y0, xb - xa, gsys.y1 - gsys.y0)
                    m.position = PagePosition(doc_page, nx, ny, nw, nh, approximate=True)
                    m.page = doc_page
                located += 1
                length = float(lengths[midx]) or 4.0
                start_x = xa + geom.interline * (5 if xa == gsys.x0 else 1)
                usable = max(geom.interline, (xb - start_x) - geom.interline)
                # Las notas que sí están localizadas en este compás (en cualquier pentagrama) dicen dónde
                # cae cada instante: el espaciado de una partitura no es proporcional a la duración.
                anchors = _onset_anchors(by_measure.get(midx, []), proc, doc_page)
                ends = [(0.0, start_x), (length, start_x + usable)]
                for n in by_measure.get(midx, []):
                    if n.position is not None and not overwrite:
                        continue
                    staff_idx = n.staff - 1
                    if staff_idx >= len(gsys.staves):
                        continue
                    staff = geom.staves[gsys.staves[staff_idx]]
                    x = x_for_onset(anchors, ends, float(n.onset))
                    if n.rest:
                        y = staff.position_to_y(0, x)
                    else:
                        clef = score.clef_at(n.part, n.staff, n.measure, n.onset)
                        if clef is None:
                            continue
                        pos = P.staff_position(n.step, n.octave, clef.sign, clef.line, clef.octave_change)
                        y = staff.position_to_y(pos, x)
                    il = geom.interline
                    nx, ny, nw, nh = proc.processed_box_to_normalized(x - il * 0.6, y - il * 0.5, il * 1.2, il)
                    n.position = PagePosition(doc_page, nx, ny, nw, nh, approximate=True)
    return located


def _onset_anchors(notes, proc, doc_page: int) -> list[tuple[float, float]]:
    """(instante en negras, x en la imagen procesada) de las notas con posición exacta."""
    by_onset: dict[float, list[float]] = defaultdict(list)
    for n in notes:
        pos = n.position
        if pos is None or pos.approximate or pos.page != doc_page or n.grace:
            continue
        x, _y = proc.normalized_to_processed(pos.x + pos.w / 2, pos.y + pos.h / 2)
        by_onset[float(n.onset)].append(x)
    return sorted((o, sorted(xs)[len(xs) // 2]) for o, xs in by_onset.items())


def x_for_onset(anchors: list[tuple[float, float]], ends: list[tuple[float, float]], onset: float) -> float:
    """x de un instante del compás: la de otra nota del mismo instante si la hay; si no, interpolando entre
    los instantes conocidos más cercanos (y los bordes del compás cuando no hay nada a un lado)."""
    pts = list(anchors)
    if not any(o <= 0 for o, _ in pts):
        pts.append(ends[0])
    if not any(o >= ends[1][0] for o, _ in pts):
        pts.append(ends[1])
    pts.sort()
    for o, x in pts:
        if abs(o - onset) < 1e-9:
            return x
    before = [p for p in pts if p[0] < onset]
    after = [p for p in pts if p[0] > onset]
    if before and after:
        (o0, x0), (o1, x1) = before[-1], after[0]
        return x0 + (x1 - x0) * (onset - o0) / (o1 - o0)
    if before and len(before) >= 2:
        (o0, x0), (o1, x1) = before[-2], before[-1]
        return x1 + (x1 - x0) * (onset - o1) / (o1 - o0)
    if after and len(after) >= 2:
        (o0, x0), (o1, x1) = after[0], after[1]
        return x0 - (x1 - x0) * (o0 - onset) / (o1 - o0)
    return (before or after)[0][1]


def courtesy_follows(score: Score, last_measure: int) -> bool:
    """El sistema siguiente empieza con cambio de armadura o de compás: al final de este suele haber una
    armadura o indicación «de cortesía» tras la última barra."""
    nxt = last_measure + 1
    if nxt >= len(score.measures):
        return False
    return score.measures[nxt].time_sig_change or any(k.measure == nxt for k in score.keys)


def drop_courtesy_span(score: Score, spans: list[tuple[float, float]], last_measure: int, interline: float,
                       expected: int) -> list[tuple[float, float]]:
    """Quita el último tramo entre barras si solo es el hueco de la armadura/compás de cortesía (estrecho y
    con cambio en el sistema siguiente): no es un compás."""
    if len(spans) == expected + 1 and spans and spans[-1][1] - spans[-1][0] < 8 * interline \
            and courtesy_follows(score, last_measure):
        return spans[:-1]
    return spans


def _equal_spans(x_start: float, x_end: float, count: int, first_start: float | None = None) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    width = (x_end - x_start) / count
    spans = [(x_start + i * width, x_start + (i + 1) * width) for i in range(count)]
    if first_start is not None and spans:
        spans[0] = (first_start, spans[0][1])
    return spans
