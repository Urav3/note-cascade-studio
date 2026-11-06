"""Lectura del proyecto ``.omr`` de Audiveris para obtener posiciones y calidad.

El MusicXML exportado no indica dónde está cada nota en la imagen ni con qué
seguridad se reconoció. El proyecto ``.omr`` sí: cada cabeza de nota tiene su
rectángulo, su posición en el pentagrama y sus puntuaciones ``grade`` y
``ctx-grade`` (0..1). Aquí se asocian esas cabezas con las notas del modelo
comparando compás (columna «stack»), pentagrama, posición vertical y orden
horizontal. Si la estructura no coincide se renuncia a la asociación en vez de
inventarla.
"""

from __future__ import annotations

import logging
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import Note, PagePosition, Score

log = logging.getLogger(__name__)


@dataclass
class OmrInter:
    kind: str           # head | rest
    shape: str
    x: float
    y: float
    w: float
    h: float
    grade: float
    ctx_grade: float
    staff_id: int
    pitch: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class OmrStack:
    left: float
    right: float
    slots: list[tuple[float, Fraction]] = field(default_factory=list)


@dataclass
class OmrStaff:
    staff_id: int
    part_index: int
    index_in_part: int
    left: float
    right: float
    top: float
    bottom: float


@dataclass
class OmrSystem:
    stacks: list[OmrStack] = field(default_factory=list)
    staves: list[OmrStaff] = field(default_factory=list)
    inters: list[OmrInter] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min((s.top for s in self.staves), default=0.0)

    @property
    def bottom(self) -> float:
        return max((s.bottom for s in self.staves), default=0.0)


@dataclass
class OmrPage:
    sheet: int
    width: int
    height: int
    interline: float
    systems: list[OmrSystem] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)


def _f(el: ET.Element, name: str, default: float = 0.0) -> float:
    try:
        return float(el.get(name, default))
    except (TypeError, ValueError):
        return default


def parse_omr(path: Path | str) -> list[OmrPage]:
    pages: list[OmrPage] = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.match(r"sheet#\d+/sheet#\d+\.xml$", n)]
        names.sort(key=lambda n: int(re.search(r"#(\d+)", n).group(1)))
        for name in names:
            sheet_no = int(re.search(r"#(\d+)", name).group(1))
            root = ET.fromstring(z.read(name))
            pic = root.find("picture")
            width = int(_f(pic, "width")) if pic is not None else 0
            height = int(_f(pic, "height")) if pic is not None else 0
            il_el = root.find("scale/interline")
            interline = _f(il_el, "main", 20.0) if il_el is not None else 20.0
            for page_el in root.findall("page"):
                page = OmrPage(sheet_no, width, height, interline)
                for sys_el in page_el.findall("system"):
                    page.systems.append(_parse_system(sys_el))
                pages.append(page)
    return pages


def _parse_system(sys_el: ET.Element) -> OmrSystem:
    system = OmrSystem()
    for st in sys_el.findall("stack"):
        stack = OmrStack(_f(st, "left"), _f(st, "right"))
        for slot in st.findall("slot"):
            try:
                t = Fraction(slot.get("time-offset", "0"))
            except (ValueError, ZeroDivisionError):
                t = Fraction(0)
            stack.slots.append((stack.left + _f(slot, "x-offset"), t))
        system.stacks.append(stack)
    for pidx, part_el in enumerate(sys_el.findall("part")):
        for sidx, staff_el in enumerate(part_el.findall("staff")):
            ys = [_f(pt, "y") for line in staff_el.findall("lines/line") for pt in line.findall("point")]
            top = min(ys) if ys else 0.0
            bottom = max(ys) if ys else 0.0
            system.staves.append(OmrStaff(int(_f(staff_el, "id")), pidx, sidx + 1, _f(staff_el, "left"),
                                          _f(staff_el, "right"), top, bottom))
    inters = sys_el.find("sig/inters")
    if inters is not None:
        for el in inters:
            tag = el.tag
            if tag not in ("head", "rest"):
                continue
            b = el.find("bounds")
            if b is None or el.get("staff") is None:
                continue
            system.inters.append(OmrInter(
                tag, el.get("shape", ""), _f(b, "x"), _f(b, "y"), _f(b, "w"), _f(b, "h"),
                _f(el, "grade"), _f(el, "ctx-grade", _f(el, "grade")), int(_f(el, "staff")), _f(el, "pitch")))
    return system


@dataclass
class PositionStats:
    measures_mapped: int = 0
    notes_matched: int = 0
    notes_total: int = 0
    message: str = ""


def _pairs_by_system(score: Score, omr_pages: list[OmrPage], layout) -> tuple[list[tuple], int]:
    """Empareja compases del MusicXML con columnas del .omr sistema a sistema.

    Si en un sistema el número de compases no coincide (p. ej. un compás
    partido), ese sistema se omite en lugar de desplazar todo lo demás.
    """
    flat = [(pg, sys, st) for pg in omr_pages for sys in pg.systems for st in sys.stacks]
    if len(flat) == len(score.measures):
        return [(i, *flat[i]) for i in range(len(flat))], 0
    if not layout or len(layout) != len(score.measures):
        return [], len(score.measures)
    xml_systems: dict[tuple[int, int], list[int]] = defaultdict(list)
    for midx, ml in enumerate(layout):
        xml_systems[(ml.page, ml.system_in_page)].append(midx)
    pairs = []
    skipped = 0
    for p_idx, pg in enumerate(omr_pages):
        for s_idx, system in enumerate(pg.systems):
            measures = xml_systems.get((p_idx, s_idx), [])
            stacks = system.stacks
            if len(measures) != len(stacks):
                stacks = musical_stacks(pg, system)
            if len(measures) == len(stacks):
                pairs.extend((mi, pg, system, st) for mi, st in zip(measures, stacks))
            else:
                skipped += len(measures)
    return pairs, skipped


def musical_stacks(pg: OmrPage, system: OmrSystem) -> list[OmrStack]:
    """Columnas que son compases de verdad. Audiveris a veces crea una columna estrecha sin ninguna nota ni
    silencio al final del sistema: el hueco tras la doble barra donde va la armadura o el compás «de
    cortesía» que anuncia el cambio del sistema siguiente. En el MusicXML no es un compás."""
    il = pg.interline or 20.0
    out = []
    for st in system.stacks:
        has_content = any(st.left - 2 <= i.cx <= st.right + 2 for i in system.inters)
        if has_content or st.right - st.left >= 6 * il:
            out.append(st)
    return out


def apply_positions(score: Score, omr_pages: list[OmrPage], sheet_to_page: dict[int, tuple[int, object]],
                    layout=None) -> PositionStats:
    """Asigna ``position`` y ``confidence`` a compases y notas.

    ``sheet_to_page`` relaciona el número de hoja de Audiveris con (índice de
    página del documento, ProcessedPage) para convertir coordenadas.
    """
    stats = PositionStats(notes_total=sum(1 for n in score.notes))
    pairs, skipped = _pairs_by_system(score, omr_pages, layout)
    if not pairs:
        stats.message = ("La estructura del proyecto de Audiveris no coincide con el MusicXML; no se usan sus "
                         "posiciones exactas.")
        return stats
    by_measure: dict[int, list[Note]] = defaultdict(list)
    for n in score.notes:
        by_measure[n.measure].append(n)
    for midx, pg, system, stack in pairs:
        mapping = sheet_to_page.get(pg.sheet)
        if mapping is None:
            continue
        doc_page, proc = mapping
        sx = proc.image.shape[1] / pg.width if pg.width else 1.0
        sy = proc.image.shape[0] / pg.height if pg.height else 1.0

        def norm_box(x: float, y: float, w: float, h: float, approximate: bool = False) -> PagePosition:
            nx, ny, nw, nh = proc.processed_box_to_normalized(x * sx, y * sy, w * sx, h * sy)
            return PagePosition(doc_page, nx, ny, nw, nh, approximate)

        measure = score.measures[midx]
        measure.position = norm_box(stack.left, system.top, stack.right - stack.left, system.bottom - system.top)
        measure.page = doc_page
        stats.measures_mapped += 1
        notes = by_measure.get(midx, [])
        staff_lookup = {(s.part_index, s.index_in_part): s for s in system.staves}
        for (part, staff_no), group in _group_by_staff(notes).items():
            ostaff = staff_lookup.get((part, staff_no))
            if ostaff is None:
                continue
            inters = [i for i in system.inters if i.staff_id == ostaff.staff_id
                      and stack.left - 4 <= i.cx <= stack.right + 4]
            stats.notes_matched += _match_group(score, group, inters, stack, norm_box)
    stats.message = (f"Posiciones de Audiveris: {stats.measures_mapped} compases y {stats.notes_matched} de "
                     f"{stats.notes_total} notas/silencios localizados en la imagen.")
    if skipped:
        stats.message += f" {skipped} compases de sistemas con estructura distinta quedan con posición aproximada."
    return stats


def _group_by_staff(notes: list[Note]) -> dict[tuple[int, int], list[Note]]:
    groups: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for n in notes:
        groups[(n.part, n.staff)].append(n)
    return groups


def _slot_x(stack: OmrStack, onset_q: Fraction) -> float | None:
    target = onset_q / 4
    best = None
    for x, t in stack.slots:
        if t == target:
            return x
        if best is None or abs(t - target) < abs(best[1] - target):
            best = (x, t)
    return best[0] if best and abs(best[1] - target) <= Fraction(1, 16) else None


def _match_group(score: Score, notes: list[Note], inters: list[OmrInter], stack: OmrStack, norm_box) -> int:
    matched = 0
    heads = [i for i in inters if i.kind == "head"]
    rests = [i for i in inters if i.kind == "rest"]
    used: set[int] = set()
    # Notas con altura: misma posición vertical en el pentagrama y x cercana a la columna temporal.
    for n in sorted([n for n in notes if not n.rest], key=lambda n: (n.onset, n.midi)):
        clef = score.clef_at(n.part, n.staff, n.measure, n.onset)
        if clef is None:
            continue
        pos = P.staff_position(n.step, n.octave, clef.sign, clef.line, clef.octave_change)
        sx = _slot_x(stack, n.onset)
        cands = [(k, h) for k, h in enumerate(heads) if k not in used and abs(h.pitch - pos) < 0.6]
        if not cands:
            continue
        if sx is not None:
            k, h = min(cands, key=lambda c: abs(c[1].cx - sx))
            if abs(h.cx - sx) > 60:
                continue
        else:
            k, h = min(cands, key=lambda c: c[1].cx)
        used.add(k)
        n.position = norm_box(h.x, h.y, h.w, h.h)
        n.confidence = round(h.ctx_grade, 3)
        matched += 1
    # Silencios: por orden horizontal.
    rest_notes = sorted([n for n in notes if n.rest], key=lambda n: (n.onset, n.voice))
    rest_inters = sorted(rests, key=lambda r: r.cx)
    used_r: set[int] = set()
    for n in rest_notes:
        sx = _slot_x(stack, n.onset)
        cands = [(k, r) for k, r in enumerate(rest_inters) if k not in used_r]
        if not cands:
            break
        if sx is not None:
            k, r = min(cands, key=lambda c: abs(c[1].cx - sx))
            if abs(r.cx - sx) > 80:
                continue
        else:
            k, r = cands[0]
        used_r.add(k)
        n.position = norm_box(r.x, r.y, r.w, r.h)
        n.confidence = round(r.ctx_grade, 3)
        matched += 1
    return matched
