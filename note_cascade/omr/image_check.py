"""Comprobación del resultado del motor contra la propia imagen de la partitura.

El programa aprende de la misma partitura cómo son sus cabezas de nota (negra, blanca y redonda):
toma como modelo las notas que el motor situó en la página y busca en todas las páginas cabezas
iguales. Con eso:

1. **Verifica** cada nota reconocida: hay una cabeza en ese sitio y su altura en el pentagrama
   corresponde a la nota (``Note.image_verified``). Si la altura no corresponde, se avisa.
2. **Sitúa** en la imagen las notas cuya posición era aproximada (p. ej. compases que el motor no
   pudo relacionar con la imagen o notas aportadas por el segundo motor).
3. **Encuentra cabezas que ningún motor reconoció.** Si forman acorde con una nota conocida del
   mismo pentagrama (mismo instante, pegada a ella) se añaden con su ritmo; si no, solo se avisa.
4. **Comprueba las alteraciones** (♯, ♭, ♮): aprende su forma de las que el motor sí leyó, las busca
   delante de cada nota y corrige la nota (y las iguales que siguen en el compás) si no la llevaba.
5. **Busca ligaduras que el motor no vio**: dos notas iguales seguidas con un arco entre ellas (o un arco
   que sale hacia el final del renglón, cuando la ligadura cruza de sistema o de página).

Nada se cambia en silencio: cada nota añadida o altura dudosa queda como aviso con su corrección.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction

import cv2
import numpy as np

from note_cascade.image_processing import transforms as T
from note_cascade.image_processing.staff_detection import PageGeometry, binarize
from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import Note, PagePosition, Score, log_pitch_change, new_id
from note_cascade.music_validation.issues import Fix, Issue

log = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.62   # basta para confirmar una nota que el motor ya leyó
STRONG_MATCH = 0.75      # necesario para situar o añadir una nota
MIN_EXAMPLES = 3


def head_class(n: Note) -> str:
    t = n.type_name
    if t in ("whole", "breve"):
        return "redonda"
    if t == "half":
        return "blanca"
    if t:
        return "negra"
    return "redonda" if n.duration >= 4 else "blanca" if n.duration >= 2 else "negra"


@dataclass
class _Page:
    rec: object
    geom: PageGeometry
    ink: np.ndarray          # tinta sin las líneas del pentagrama (float32 0/1)
    m: np.ndarray            # original (píxeles) → procesada
    minv: np.ndarray
    size: tuple[int, int]    # tamaño de la página original

    def to_proc(self, pos: PagePosition) -> tuple[float, float, float, float]:
        w, h = self.size
        x0, y0 = T.apply_matrix(self.m, pos.x * w, pos.y * h)
        x1, y1 = T.apply_matrix(self.m, (pos.x + pos.w) * w, (pos.y + pos.h) * h)
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def to_norm(self, x0: float, y0: float, x1: float, y1: float) -> PagePosition:
        w, h = self.size
        a = T.apply_matrix(self.minv, x0, y0)
        b = T.apply_matrix(self.minv, x1, y1)
        return PagePosition(self.rec.index, a[0] / w, a[1] / h, (b[0] - a[0]) / w, (b[1] - a[1]) / h)


@dataclass
class Head:
    page: int
    cls: str
    cx: float
    cy: float
    w: float
    h: float
    score: float
    used: bool = False


def _load_page(project, rec) -> _Page | None:
    geom = rec.geometry_obj()
    if geom is None or not geom.staves or not rec.processed_file:
        return None
    gray = cv2.imread(str(project.page_path(rec.processed_file)), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return None
    bw = binarize(gray)
    il = geom.interline
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(2.5 * il)), 1)))
    ink = cv2.subtract(bw, lines)
    k = max(3, int(round(geom.thickness * 2 + 1)))
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    m = np.array(rec.matrix, dtype=np.float64)
    return _Page(rec, geom, ink.astype(np.float32), m, np.linalg.inv(m), tuple(rec.original_size))


# ---------------------------------------------------------------------------
# Modelos de cabeza aprendidos de la partitura
# ---------------------------------------------------------------------------

def learn_templates(score: Score, pages: dict[int, _Page]) -> dict[str, np.ndarray]:
    """Plantilla media de cada clase de cabeza, en unidades de interlineado (independiente de la página)."""
    samples: dict[str, list[np.ndarray]] = defaultdict(list)
    sizes: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for n in score.notes:
        if n.rest or n.grace or n.cue or n.position is None or n.position.approximate:
            continue
        pg = pages.get(n.position.page)
        if pg is None:
            continue
        x0, y0, x1, y1 = pg.to_proc(n.position)
        il = pg.geom.interline
        sizes[head_class(n)].append(((x1 - x0) / il, (y1 - y0) / il))
    canon = {c: (float(np.median([s[0] for s in v])), float(np.median([s[1] for s in v])))
             for c, v in sizes.items() if len(v) >= MIN_EXAMPLES}
    for n in score.notes:
        if n.rest or n.grace or n.cue or n.position is None or n.position.approximate:
            continue
        c = head_class(n)
        pg = pages.get(n.position.page)
        if pg is None or c not in canon or len(samples[c]) >= 150:
            continue
        x0, y0, x1, y1 = pg.to_proc(n.position)
        il = pg.geom.interline
        tw, th = canon[c][0] * il, canon[c][1] * il
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        xa, ya = int(round(cx - tw / 2)), int(round(cy - th / 2))
        patch = pg.ink[ya:ya + int(round(th)), xa:xa + int(round(tw))]
        if patch.size == 0 or ya < 0 or xa < 0:
            continue
        samples[c].append(cv2.resize(patch, (24, 24), interpolation=cv2.INTER_AREA))
    out = {}
    for c, v in samples.items():
        if len(v) >= MIN_EXAMPLES:
            tpl = np.median(np.stack(v), axis=0).astype(np.float32)
            if tpl.std() > 0.05:
                out[c] = (tpl, canon[c])
    return out


def detect_heads(pg: _Page, templates) -> list[Head]:
    il = pg.geom.interline
    heads: list[Head] = []
    for c, (tpl, (rw, rh)) in templates.items():
        tw, th = max(4, int(round(rw * il))), max(4, int(round(rh * il)))
        t = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_LINEAR)
        if pg.ink.shape[0] < th or pg.ink.shape[1] < tw:
            continue
        res = cv2.matchTemplate(pg.ink, t, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= MATCH_THRESHOLD)
        order = np.argsort(-res[ys, xs])
        for k in order:
            cx, cy = xs[k] + tw / 2, ys[k] + th / 2
            if any(abs(cx - o.cx) < 0.7 * tw and abs(cy - o.cy) < 0.6 * th for o in heads):
                continue
            if not _in_staff_zone(pg.geom, cx, cy):
                continue
            heads.append(Head(pg.rec.index, c, float(cx), float(cy), float(tw), float(th), float(res[ys[k], xs[k]])))
    return heads


def _in_staff_zone(geom: PageGeometry, x: float, y: float) -> bool:
    il = geom.interline
    return any(s.x0 - il <= x <= s.x1 + il and s.top - 5 * il <= y <= s.bottom + 5 * il for s in geom.staves)


# ---------------------------------------------------------------------------
# Altura a partir de la posición vertical
# ---------------------------------------------------------------------------

def _measure_staff(score: Score, pg: _Page, mi: int, staff_no: int):
    """Pentagrama de la imagen (objeto Staff) que corresponde a ``staff_no`` en el compás ``mi``."""
    m = score.measures[mi]
    if m.position is None:
        return None
    x0, y0, x1, y1 = pg.to_proc(m.position)
    cy = (y0 + y1) / 2
    geom = pg.geom
    for sysobj in geom.systems:
        if sysobj.y0 - geom.interline <= cy <= sysobj.y1 + geom.interline:
            if 1 <= staff_no <= len(sysobj.staves):
                return geom.staves[sysobj.staves[staff_no - 1]]
    return None


def written_diatonic(score: Score, n_part: int, staff_no: int, mi: int, onset, staff, x: float, y: float) -> int | None:
    clef = score.clef_at(n_part, staff_no, mi, onset)
    if clef is None:
        return None
    mid_step, mid_oct = P.clef_middle_line(clef.sign, clef.line, clef.octave_change)
    steps = int(round((staff.y(2, x) - y) / (staff.interline / 2)))
    return P.diatonic_index(mid_step, mid_oct) + steps


# ---------------------------------------------------------------------------
# Proceso completo
# ---------------------------------------------------------------------------

def check_against_image(score: Score, project) -> list[Issue]:
    if len(score.parts) != 1 or not project.pages:
        return []
    pages: dict[int, _Page] = {}
    for rec in project.pages:
        try:
            pg = _load_page(project, rec)
        except Exception as exc:  # la comprobación es auxiliar
            log.warning("No se pudo preparar la página %s para comprobar notas: %s", rec.index + 1, exc)
            pg = None
        if pg is not None:
            pages[rec.index] = pg
    if not pages:
        return []
    templates = learn_templates(score, pages)
    if "negra" not in templates and "redonda" not in templates and "blanca" not in templates:
        return []
    issues: list[Issue] = []
    by_page = {i: detect_heads(pg, templates) for i, pg in pages.items()}
    verified = 0
    pitch_issues: list[Issue] = []
    # 1) Verificar las notas con posición exacta y marcar sus cabezas como usadas
    for n in score.notes:
        if n.rest or n.grace or n.position is None or n.position.approximate:
            continue
        pg = pages.get(n.position.page)
        if pg is None:
            continue
        x0, y0, x1, y1 = pg.to_proc(n.position)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        head = _head_at(by_page[pg.rec.index], cx, cy)
        if head is not None:
            head.used = True
        staff = _measure_staff(score, pg, n.measure, n.staff)
        if staff is None:
            continue
        d = written_diatonic(score, n.part, n.staff, n.measure, n.onset, staff, cx, cy)
        if d is None:
            continue
        diff = P.diatonic_index(n.step, n.octave) - d
        shift = _octave_shift_at(score, n)
        if diff == 7 * shift:
            n.position_consistent = True   # la altura sale de la posición de la cabeza con su clave
        if head is None:
            continue
        if diff == 0 or (shift and diff == 7 * shift):
            n.image_verified = True
            verified += 1
        elif diff % 7 != 0 and abs(diff) <= 4:
            step, octave = P.from_diatonic_index(d + 7 * shift)
            pitch_issues.append(_pitch_issue(score, n, step, octave))
    issues += pitch_issues[:40]
    # 1b) Alteraciones leídas como cabeza de nota (p. ej. el ♮ delante de Do5 leído como «Do♯5»)
    try:
        issues += _accidentals_read_as_notes(score, pages, by_page)
    except Exception as exc:  # auxiliar
        log.warning("No se pudieron buscar alteraciones leídas como notas: %s", exc)
    # 2) Notas con posición aproximada: buscar su cabeza
    relocated = _relocate(score, pages, by_page)
    issues += _unlocated_notes(score, pages, by_page)
    # 3) Cabezas sin nota
    added, proposals = _add_missing(score, pages, by_page)
    if added:
        score.sort_notes()
        by_measure: dict[int, list[Note]] = defaultdict(list)
        for n in added:
            by_measure[n.measure].append(n)
        for mi, notes in sorted(by_measure.items()):
            names = ", ".join(n.name() for n in notes)
            issues.append(Issue(
                "aviso", "imagen", f"Compás {score.measures[mi].number}: nota(s) que ningún motor vio",
                f"En la imagen hay {len(notes)} cabeza(s) de nota pegada(s) a un acorde reconocido que ningún motor "
                f"leyó. Se añadieron con el ritmo de ese acorde: {names}. Compruébalo escuchando; si sobra, quítalas.",
                measure=mi, note_ids=[n.id for n in notes], evidence="comprobado", code="nota_imagen",
                fix=Fix("Quitar las notas añadidas", "delete_notes", {"note_ids": [n.id for n in notes]})))
    issues += proposals[:30]
    acc_issues: list[Issue] = []
    try:
        acc_issues = check_accidentals(score, pages, by_page)
    except Exception as exc:  # auxiliar
        log.warning("No se pudieron comprobar las alteraciones: %s", exc)
    issues += acc_issues
    try:
        issues += detect_ties(score, pages)
    except Exception as exc:  # auxiliar
        log.warning("No se pudieron buscar ligaduras: %s", exc)
    project.add_log(f"Comprobación con la imagen: {verified} notas confirmadas, {relocated} situadas, "
                    f"{len(added)} añadidas, {len(proposals)} cabezas sin explicar, "
                    f"{len(pitch_issues)} alturas dudosas, {len(acc_issues)} alteraciones corregidas.")
    return issues


def _accidentals_read_as_notes(score: Score, pages: dict[int, _Page], by_page: dict[int, list[Head]]) -> list[Issue]:
    """Una nota del motor sin cabeza en la imagen, justo a la izquierda de otra nota del mismo acorde que está
    en la misma línea o espacio, sí tiene cabeza y lleva alteración: es esa alteración (♯, ♭, ♮) leída como
    nota. En un mismo pentagrama e instante no puede haber dos cabezas en el mismo sitio sin que se vean las
    dos, así que se quita y se avisa (con la corrección para volver a ponerla)."""
    phantoms: dict[int, list[tuple[Note, Note]]] = defaultdict(list)
    removed: set[str] = set()
    for n in score.notes:
        if n.rest or n.grace or n.image_verified or n.position is None or n.position.approximate:
            continue
        pg = pages.get(n.position.page)
        if pg is None:
            continue
        nx0, ny0, nx1, ny1 = pg.to_proc(n.position)
        ncx, ncy = (nx0 + nx1) / 2, (ny0 + ny1) / 2
        if _head_at(by_page[pg.rec.index], ncx, ncy) is not None:
            continue
        il = pg.geom.interline
        for o in score.notes_in_measure(n.measure, include_rests=False):
            if o is n or o.id in removed or not o.image_verified or o.staff != n.staff or o.onset != n.onset \
                    or (o.step, o.octave) != (n.step, n.octave) or o.position is None or o.position.page != n.position.page:
                continue
            ox0, oy0, ox1, oy1 = pg.to_proc(o.position)
            ocx, ocy = (ox0 + ox1) / 2, (oy0 + oy1) / 2
            if not (0.3 * il <= ocx - ncx <= 3.0 * il and abs(ocy - ncy) <= 0.5 * il):
                continue
            box = _accidental_box(pg, ocx, ocy, ox0)
            ink_there = box is not None and box[0] - 0.3 * il <= ncx <= box[0] + box[2] + 0.3 * il
            if o.accidental_shown or ink_there:
                phantoms[n.measure].append((n, o))
                removed.add(n.id)
                break
    if not removed:
        return []
    saved = {n.id: n.to_dict() for n in score.notes if n.id in removed}
    score.notes = [n for n in score.notes if n.id not in removed]
    issues = []
    for mi, pairs in sorted(phantoms.items()):
        what = ", ".join(f"{n.name()} (es la alteración del {P.note_name(o.step, 0, o.octave).rstrip('0123456789-')} "
                         f"que tiene a su derecha)" for n, o in pairs)
        issues.append(Issue(
            "aviso", "imagen", f"Compás {score.measures[mi].number}: alteración leída como nota",
            f"El motor leyó como nota lo que en la imagen es la alteración escrita delante de otra nota del mismo "
            f"acorde: {what}. En ese sitio no hay ninguna cabeza de nota, así que se quitó. Si de verdad hay dos "
            "notas, vuelve a ponerla con la corrección.", measure=mi, note_ids=[o.id for _n, o in pairs],
            evidence="comprobado", code="alteracion_como_nota",
            fix=Fix("Volver a poner la nota quitada", "restore_notes",
                    {"notes": [saved[n.id] for n, _o in pairs]})))
    return issues


def _head_at(heads: list[Head], cx: float, cy: float) -> Head | None:
    best = None
    for h in heads:
        if abs(h.cx - cx) < 0.6 * h.w and abs(h.cy - cy) < 0.3 * h.h:
            if best is None or abs(h.cy - cy) < abs(best.cy - cy):
                best = h
    return best


def _octave_shift_at(score: Score, n: Note) -> int:
    """+1 si la nota está bajo una 8va aplicada (suena una octava más alta que lo escrito), −1 si 8vb."""
    active = 0
    for d in sorted((d for d in score.directions if d.kind == "octave_shift" and d.part == n.part
                     and d.source == "texto" and d.staff in (None, n.staff)), key=lambda d: (d.measure, d.onset)):
        if (d.measure, d.onset) > (n.measure, n.onset):
            break
        if d.value == "down":
            active = 1 if (d.size or 8) == 8 else 2
        elif d.value == "up":
            active = -1 if (d.size or 8) == 8 else -2
        elif d.value == "stop" and (d.measure, d.onset) <= (n.measure, n.onset):   # el fin no incluye lo que empieza ahí
            active = 0
    return active


def _pitch_issue(score: Score, n: Note, step: str, octave: int) -> Issue:
    fifths = score.key_at(n.part, n.measure, n.staff)
    alter = P.key_alterations(fifths)[step] if not n.accidental_shown else n.alter
    midi = P.midi_from(step, alter, octave)
    target = P.note_name(step, alter, octave)
    return Issue("aviso", "altura", f"Compás {score.measures[n.measure].number}: altura dudosa ({n.name()})",
                 f"El motor leyó {n.name()}, pero la cabeza está en la imagen a la altura de {target}. Suele pasar con "
                 "notas sobre líneas adicionales. Escúchalo y compáralo con la imagen.",
                 measure=n.measure, note_ids=[n.id], evidence="comprobado", code="altura_imagen",
                 fix=Fix(f"Cambiar a {target}", "set_pitch", {"note_id": n.id, "midi": midi}))


def _relocate(score: Score, pages: dict[int, _Page], by_page: dict[int, list[Head]]) -> int:
    """Asigna una cabeza detectada a cada nota de posición aproximada (mismo compás, pentagrama y altura)."""
    count = 0
    groups: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for n in score.notes:
        if n.rest or n.grace:
            continue
        if n.position is None or n.position.approximate:
            groups[(n.measure, n.staff)].append(n)
    for (mi, staff_no), notes in groups.items():
        m = score.measures[mi]
        if m.position is None or m.position.page not in pages:
            continue
        pg = pages[m.position.page]
        staff = _measure_staff(score, pg, mi, staff_no)
        if staff is None:
            continue
        x0, y0, x1, y1 = pg.to_proc(m.position)
        il = pg.geom.interline
        cands = [h for h in by_page[pg.rec.index] if not h.used and x0 - il <= h.cx <= x1 + il
                 and staff.top - 5 * il <= h.cy <= staff.bottom + 5 * il]
        last_x = x0 - il
        for n in sorted(notes, key=lambda n: (n.onset, -n.midi)):
            want = P.diatonic_index(n.step, n.octave) - 7 * _octave_shift_at(score, n)
            options = []
            for h in cands:
                if h.used or h.score < STRONG_MATCH or h.cls != head_class(n) or h.cx < last_x - 1.5 * h.w:
                    continue
                if written_diatonic(score, n.part, staff_no, mi, n.onset, staff, h.cx, h.cy) == want:
                    options.append(h)
            if not options:
                continue
            h = min(options, key=lambda h: h.cx)
            h.used = True
            last_x = h.cx
            n.position = pg.to_norm(h.cx - h.w / 2, h.cy - h.h / 2, h.cx + h.w / 2, h.cy + h.h / 2)
            n.image_verified = True
            count += 1
    return count + _relocate_by_anchors(score, pages, by_page)


def expected_x(score: Score, pg: _Page, n: Note, lengths=None) -> float | None:
    """x donde debería estar la nota según las demás notas localizadas del compás (cualquier pentagrama):
    la de otra nota del mismo instante o, si no la hay, interpolando entre los instantes vecinos."""
    from note_cascade.music_model.timeline import measure_lengths
    from note_cascade.omr.positions import x_for_onset
    m = score.measures[n.measure]
    if m.position is None or m.position.page != pg.rec.index:
        return None
    by_onset: dict[float, list[float]] = defaultdict(list)
    for o in score.notes_in_measure(n.measure, include_rests=True):
        if o is n or o.grace or o.position is None or o.position.approximate or o.position.page != pg.rec.index:
            continue
        x0, _y0, x1, _y1 = pg.to_proc(o.position)
        by_onset[float(o.onset)].append((x0 + x1) / 2)
    if not by_onset:
        return None
    anchors = sorted((o, sorted(xs)[len(xs) // 2]) for o, xs in by_onset.items())
    lengths = lengths or measure_lengths(score)
    mx0, _a, mx1, _b = pg.to_proc(m.position)
    il = pg.geom.interline
    return x_for_onset(anchors, [(0.0, mx0 + 2 * il), (float(lengths[n.measure]), mx1 - il)], float(n.onset))


def _relocate_by_anchors(score: Score, pages: dict[int, _Page], by_page: dict[int, list[Head]]) -> int:
    """Segunda pasada para las notas que siguen sin situar: si en el instante que marcan las demás notas del
    compás hay una cabeza a la altura exacta de la nota, es ella aunque la coincidencia con el modelo sea
    menor (p. ej. una cabeza colgada de una línea adicional o pegada a un puntillo)."""
    from note_cascade.music_model.timeline import measure_lengths
    lengths = measure_lengths(score)
    count = 0
    for n in sorted(score.notes, key=lambda n: (n.measure, n.onset, n.staff, -n.midi)):
        if n.rest or n.grace or not (n.position is None or n.position.approximate):
            continue
        m = score.measures[n.measure]
        if m.position is None or m.position.page not in pages:
            continue
        pg = pages[m.position.page]
        staff = _measure_staff(score, pg, n.measure, n.staff)
        ex = expected_x(score, pg, n, lengths)
        if staff is None or ex is None:
            continue
        il = pg.geom.interline
        want = P.diatonic_index(n.step, n.octave) - 7 * _octave_shift_at(score, n)
        options = [h for h in by_page[pg.rec.index]
                   if not h.used and h.cls == head_class(n) and abs(h.cx - ex) <= 2 * il
                   and staff.top - 5 * il <= h.cy <= staff.bottom + 5 * il
                   and written_diatonic(score, n.part, n.staff, n.measure, n.onset, staff, h.cx, h.cy) == want]
        if not options:
            continue
        h = min(options, key=lambda h: abs(h.cx - ex))
        h.used = True
        n.position = pg.to_norm(h.cx - h.w / 2, h.cy - h.h / 2, h.cx + h.w / 2, h.cy + h.h / 2)
        n.image_verified = True
        count += 1
    return count


def _add_missing(score: Score, pages: dict[int, _Page], by_page: dict[int, list[Head]]):
    added: list[Note] = []
    proposals: list[Issue] = []
    known = [n for n in score.notes if not n.rest and not n.grace and n.position is not None
             and not n.position.approximate]
    for page_index, heads in by_page.items():
        pg = pages[page_index]
        page_notes = [n for n in known if n.position.page == page_index]
        boxes = [(n, pg.to_proc(n.position)) for n in page_notes]
        for h in heads:
            if h.used or h.score < STRONG_MATCH:
                continue
            # cabeza pegada a una nota conocida (acorde): misma columna o desplazada una cabeza (segundas)
            partners = []
            for n, (x0, y0, x1, y1) in boxes:
                ncx, ncy = (x0 + x1) / 2, (y0 + y1) / 2
                if abs(ncx - h.cx) <= 1.25 * h.w and abs(ncy - h.cy) <= 3.2 * pg.geom.interline \
                        and head_class(n) == h.cls:
                    partners.append((abs(ncy - h.cy) + abs(ncx - h.cx), n))
            if not partners:
                mi = _measure_at(score, pg, h)
                if mi is not None:
                    proposals.append(Issue(
                        "aviso", "imagen", f"Compás {score.measures[mi].number}: posible nota sin reconocer",
                        "En la imagen hay una cabeza de nota que ningún motor leyó y no forma acorde con otra nota "
                        "reconocida, así que no se añade sola. Mira el compás y añádela en el editor si hace falta.",
                        measure=mi, page=page_index, evidence="sospecha", code="cabeza_sin_nota"))
                continue
            partner = min(partners)[1]
            staff = _measure_staff(score, pg, partner.measure, partner.staff)
            if staff is None:
                continue
            px0, py0, px1, py1 = pg.to_proc(partner.position)
            d_partner = written_diatonic(score, partner.part, partner.staff, partner.measure, partner.onset, staff,
                                         (px0 + px1) / 2, (py0 + py1) / 2)
            d_head = written_diatonic(score, partner.part, partner.staff, partner.measure, partner.onset, staff,
                                      h.cx, h.cy)
            if d_partner is None or d_head is None or d_head == d_partner:
                continue
            # La altura escrita del compañero debe cuadrar con su nota (si no, el cálculo no es fiable aquí)
            octs = _octave_shift_at(score, partner)
            if P.diatonic_index(partner.step, partner.octave) - d_partner != 7 * octs:
                continue
            if any(n.measure == partner.measure and n.staff == partner.staff and n.onset == partner.onset
                   and P.diatonic_index(n.step, n.octave) - 7 * octs == d_head for n in score.notes if not n.rest):
                continue  # ya existe esa nota (posición aproximada)
            step, octave = P.from_diatonic_index(d_head + 7 * octs)
            alter = _alter_in_context(score, partner, step, octave)
            new = Note(new_id(), partner.part, partner.staff, partner.voice, partner.measure, partner.onset,
                       partner.duration, step=step, alter=alter, octave=octave, midi=P.midi_from(step, alter, octave),
                       type_name=partner.type_name, dots=partner.dots, tuplet=partner.tuplet, source="imagen")
            new.position = pg.to_norm(h.cx - h.w / 2, h.cy - h.h / 2, h.cx + h.w / 2, h.cy + h.h / 2)
            new.image_verified = True
            h.used = True
            score.notes.append(new)
            boxes.append((new, (h.cx - h.w / 2, h.cy - h.h / 2, h.cx + h.w / 2, h.cy + h.h / 2)))
            added.append(new)
    return added, proposals


def _alter_in_context(score: Score, ref: Note, step: str, octave: int) -> int:
    """Alteración vigente: la última alteración escrita en el compás para esa nota, si no la armadura."""
    alter = P.key_alterations(score.key_at(ref.part, ref.measure, ref.staff))[step]
    for n in sorted(score.notes_in_measure(ref.measure, include_rests=False), key=lambda n: n.onset):
        if n.onset > ref.onset:
            break
        if n.staff == ref.staff and n.step == step and n.octave == octave and n.accidental_shown:
            alter = n.alter
    return alter


def _measure_at(score: Score, pg: _Page, h: Head) -> int | None:
    for m in score.measures:
        if m.position is None or m.position.page != pg.rec.index:
            continue
        x0, y0, x1, y1 = pg.to_proc(m.position)
        if x0 <= h.cx <= x1 and y0 - 4 * pg.geom.interline <= h.cy <= y1 + 4 * pg.geom.interline:
            return m.index
    return None


# ---------------------------------------------------------------------------
# Alteraciones
# ---------------------------------------------------------------------------

ACC_ALTER = {"sharp": 1, "flat": -1, "natural": 0}
ACC_NAME = {"sharp": "♯", "flat": "♭", "natural": "♮"}


def _accidental_box(pg: _Page, cx: float, cy: float, x0: float) -> tuple[int, int, int, int] | None:
    """Mancha de tinta justo a la izquierda de una cabeza: la alteración de esa nota."""
    il = pg.geom.interline
    xa, xb = int(max(0, x0 - 2.8 * il)), int(max(0, x0 - 0.05 * il))
    ya, yb = int(max(0, cy - 2.2 * il)), int(min(pg.ink.shape[0], cy + 2.2 * il))
    if xb - xa < 3 or yb - ya < 3:
        return None
    region = (pg.ink[ya:yb, xa:xb] > 0).astype(np.uint8)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(region, 8)
    best = None
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if h < 1.4 * il or h > 4.0 * il or w > 1.6 * il or area < 0.3 * il * il:
            continue
        right = xa + x + w
        if best is None or right > best[0] + best[2]:
            best = (xa + x, ya + y, w, h)
    return best


def check_accidentals(score: Score, pages: dict[int, _Page], by_page: dict[int, list[Head]]) -> list[Issue]:
    samples: dict[str, list[np.ndarray]] = defaultdict(list)
    shapes: dict[str, list[tuple[float, float, float]]] = defaultdict(list)   # (w/il, h/il, dy/il)
    for n in score.notes:
        acc = n.accidental_shown
        if acc not in ACC_ALTER or n.rest or n.position is None or n.position.approximate:
            continue
        pg = pages.get(n.position.page)
        if pg is None:
            continue
        x0, y0, x1, y1 = pg.to_proc(n.position)
        cy = (y0 + y1) / 2
        box = _accidental_box(pg, (x0 + x1) / 2, cy, x0)
        if box is None:
            continue
        bx, by, bw, bh = box
        il = pg.geom.interline
        shapes[acc].append((bw / il, bh / il, (cy - (by + bh / 2)) / il))
        samples[acc].append(cv2.resize(pg.ink[by:by + bh, bx:bx + bw], (16, 40), interpolation=cv2.INTER_AREA))
    templates = {}
    for acc, v in samples.items():
        if len(v) >= MIN_EXAMPLES:
            sh = np.median(np.array(shapes[acc]), axis=0)
            templates[acc] = (np.median(np.stack(v), axis=0).astype(np.float32), float(sh[0]), float(sh[1]),
                              float(sh[2]))
    if not templates:
        return []
    # Detectar alteraciones y asociarlas a la cabeza que tienen a su derecha
    known = [n for n in score.notes if not n.rest and n.position is not None and not n.position.approximate]
    found: dict[str, str] = {}
    for page_index, pg in pages.items():
        il = pg.geom.interline
        boxes = [(n, pg.to_proc(n.position)) for n in known if n.position.page == page_index]
        dets = []
        for acc, (tpl, rw, rh, dy) in templates.items():
            tw, th = max(4, int(round(rw * il))), max(6, int(round(rh * il)))
            t = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_LINEAR)
            if t.std() < 0.05 or pg.ink.shape[0] < th or pg.ink.shape[1] < tw:
                continue
            res = cv2.matchTemplate(pg.ink, t, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= STRONG_MATCH)
            for k in np.argsort(-res[ys, xs]):
                x, y = int(xs[k]), int(ys[k])
                dets.append((float(res[y, x]), acc, x, y, tw, th, dy))
        dets.sort(key=lambda d: -d[0])
        taken: list[tuple[float, float, float, float]] = []
        for sc, acc, x, y, tw, th, dy in dets:
            cx, cy = x + tw / 2, y + th / 2
            if any(abs(cx - a) < 0.6 * tw and abs(cy - b) < 0.5 * th for a, b, _w, _h in taken):
                continue
            taken.append((cx, cy, tw, th))
            head_y = cy + dy * il
            best = None
            for n, (nx0, ny0, nx1, ny1) in boxes:
                ncy = (ny0 + ny1) / 2
                gap = nx0 - (x + tw)
                if -0.2 * il <= gap <= 4.0 * il and abs(ncy - head_y) <= 0.3 * il:
                    if best is None or gap < best[0]:
                        best = (gap, n)
            if best is not None and best[1].id not in found:
                found[best[1].id] = acc
    # Corregir las notas cuya alteración escrita no coincide con la que leyó el motor
    by_id = {n.id: n for n in score.notes}
    changed: dict[int, list[str]] = defaultdict(list)
    ids: dict[int, list[str]] = defaultdict(list)
    for nid, acc in found.items():
        n = by_id[nid]
        alter = ACC_ALTER[acc]
        if n.alter == alter and n.accidental_shown in (acc, None):
            n.accidental_shown = acc
            continue
        before = n.name()
        _set_alter(n, alter, acc)
        changed[n.measure].append(f"{before} → {n.name()}")
        ids[n.measure].append(n.id)
        # las notas iguales que siguen en el compás (mismo pentagrama) llevan la misma alteración
        for m in score.notes_in_measure(n.measure, include_rests=False):
            if m.id != n.id and m.staff == n.staff and m.step == n.step and m.octave == n.octave \
                    and m.onset > n.onset and not (m.id in found) and m.alter != alter:
                before = m.name()
                _set_alter(m, alter, None)
                changed[n.measure].append(f"{before} → {m.name()}")
                ids[n.measure].append(m.id)
    issues = []
    for mi, items in sorted(changed.items()):
        issues.append(Issue(
            "aviso", "altura", f"Compás {score.measures[mi].number}: alteración leída en la imagen",
            f"En la imagen hay una alteración delante de la nota que el motor no aplicó: {', '.join(items)}. "
            "Escúchalo para confirmarlo.", measure=mi, note_ids=ids[mi], evidence="comprobado",
            code="alteracion_imagen"))
    return issues


def _set_alter(n: Note, alter: int, shown: str | None) -> None:
    old = n.midi
    n.midi += alter - n.alter
    n.alter = alter
    log_pitch_change(n, old, "alteración leída en la imagen")
    if shown is not None:
        n.accidental_shown = shown


# ---------------------------------------------------------------------------
# Ligaduras
# ---------------------------------------------------------------------------

def _arc_coverage(pg: _Page, x0: float, x1: float, cy: float) -> float:
    """Fracción de columnas entre x0 y x1 con tinta cerca de la altura de la cabeza (un arco continuo)."""
    il = pg.geom.interline
    xa, xb = int(max(0, x0)), int(min(pg.ink.shape[1], x1))
    ya, yb = int(max(0, cy - 1.1 * il)), int(min(pg.ink.shape[0], cy + 1.1 * il))
    if xb - xa < max(4, int(0.8 * il)) or yb <= ya:
        return 0.0
    cols = (pg.ink[ya:yb, xa:xb] > 0).any(axis=0)
    return float(cols.mean())


def detect_ties(score: Score, pages: dict[int, _Page]) -> list[Issue]:
    from note_cascade.music_model.timeline import measure_lengths
    lengths = measure_lengths(score)
    by_key: dict[tuple, list[Note]] = defaultdict(list)
    for n in score.notes:
        if not n.rest and not n.grace and n.position is not None and not n.position.approximate:
            by_key[(n.part, n.staff, n.midi)].append(n)
    found: list[tuple[Note, Note]] = []
    for notes in by_key.values():
        notes.sort(key=lambda n: (n.measure, n.onset))
        starts = {(n.measure, n.onset): n for n in notes}
        for a in notes:
            if a.tie_start:
                continue
            end = a.onset + a.duration
            if end == lengths[a.measure]:
                b = starts.get((a.measure + 1, Fraction(0)))
            else:
                b = starts.get((a.measure, end))
            if b is None or b.tie_stop:
                continue
            pa = pages.get(a.position.page)
            pb = pages.get(b.position.page)
            if pa is None or pb is None:
                continue
            ax0, ay0, ax1, ay1 = pa.to_proc(a.position)
            bx0, by0, bx1, by1 = pb.to_proc(b.position)
            il = pa.geom.interline
            acy = (ay0 + ay1) / 2
            same_line = a.position.page == b.position.page and abs((by0 + by1) / 2 - acy) < 0.5 * il and bx0 > ax1
            if same_line:
                gap = bx0 - ax1
                if gap < 1.5 * il:
                    continue  # demasiado juntas para distinguir un arco de otros signos
                cov = _arc_coverage(pa, ax1 + 0.2 * gap, bx0 - 0.2 * gap, acy)
                if cov >= 0.85:
                    found.append((a, b))
            elif score.measures[b.measure].new_system or score.measures[b.measure].new_page:
                # la ligadura cruza de renglón: arco desde la nota hasta el final del pentagrama
                staff = _measure_staff(score, pa, a.measure, a.staff)
                if staff is None:
                    continue
                right = staff.x1 - 0.3 * il
                if right - ax1 < 1.5 * il:
                    continue
                cov = _arc_coverage(pa, ax1 + 0.4 * il, right, acy)
                if cov >= 0.85:
                    found.append((a, b))
    if not found:
        return []
    for a, b in found:
        a.tie_start = True
        b.tie_stop = True
    by_measure: dict[int, list[tuple[Note, Note]]] = defaultdict(list)
    for a, b in found:
        by_measure[a.measure].append((a, b))
    issues = []
    for mi, pairs in sorted(by_measure.items()):
        names = ", ".join(sorted({a.name() for a, _ in pairs}))
        ids = [x.id for pair in pairs for x in pair]
        issues.append(Issue(
            "aviso", "ritmo", f"Compás {score.measures[mi].number}: ligadura leída en la imagen",
            f"En la imagen hay una ligadura ({names}) que el motor no vio: la nota se mantiene en vez de volver a "
            "tocarse. Escúchalo.", measure=mi, note_ids=ids, evidence="comprobado", code="ligadura_imagen",
            fix=Fix("Quitar la ligadura", "remove_tie", {"note_ids": ids})))
    return issues


def _unlocated_notes(score: Score, pages: dict, by_page: dict[int, list[Head]] | None = None) -> list[Issue]:
    """Notas que el motor dio sin posición y que no aparecen en la imagen, en compases donde las demás sí
    están localizadas: probablemente no existen (p. ej. el extremo de una plica o parte de una clave leídos
    como nota). Solo se propone quitarlas cuando en ese instante no hay ninguna cabeza sin explicar; si la
    hay, lo dudoso es la altura o la figura, no que la nota exista."""
    from note_cascade.music_model.timeline import measure_lengths
    lengths = measure_lengths(score)
    issues = []
    for m in score.measures:
        if m.position is None or m.position.page not in pages:
            continue
        notes = [n for n in score.notes_in_measure(m.index, include_rests=False) if not n.grace]
        lost = [n for n in notes if n.position is None or n.position.approximate]
        if not lost or len(lost) > 2 or len(notes) - len(lost) < 3:
            continue
        pg = pages[m.position.page]
        for n in lost:
            nearby = _unexplained_heads_at(score, pg, by_page, n, lengths) if by_page else []
            if nearby:
                issues.append(_head_elsewhere_issue(score, pg, n, nearby))
                continue
            if any(o is not n and o.staff == n.staff and o.onset == n.onset and o.position is not None
                   and not o.position.approximate for o in notes):
                kind = "en un acorde cuyas otras notas sí están en la imagen"
            else:
                kind = "y en su lugar no hay ninguna cabeza de nota"
            issues.append(Issue(
                "aviso", "imagen", f"Compás {m.number}: {n.name()} no aparece en la imagen",
                f"El motor leyó {n.name()} (pentagrama {n.staff}, tiempo {float(n.onset) + 1:g}) pero no se pudo situar "
                f"en la imagen {kind}. Puede que no exista (el extremo de una plica o un trozo de otro signo leídos como "
                "nota). Escucha el compás y quítala si sobra.", measure=m.index, note_ids=[n.id], evidence="sospecha",
                code="nota_sin_imagen", fix=Fix(f"Quitar {n.name()}", "delete_notes", {"note_ids": [n.id]})))
    return issues[:30]


def _unexplained_heads_at(score: Score, pg: _Page, by_page: dict[int, list[Head]], n: Note, lengths) -> list[Head]:
    """Cabezas de la imagen que ninguna nota explica, en el instante de la nota y en su pentagrama."""
    staff = _measure_staff(score, pg, n.measure, n.staff)
    ex = expected_x(score, pg, n, lengths)
    if staff is None or ex is None:
        return []
    il = pg.geom.interline
    return [h for h in by_page.get(pg.rec.index, []) if not h.used and abs(h.cx - ex) <= 2 * il
            and staff.top - 5 * il <= h.cy <= staff.bottom + 5 * il]


def _head_elsewhere_issue(score: Score, pg: _Page, n: Note, heads: list[Head]) -> Issue:
    staff = _measure_staff(score, pg, n.measure, n.staff)
    h = min(heads, key=lambda h: abs(h.cy - (staff.top + staff.bottom) / 2))
    num = score.measures[n.measure].number
    d = written_diatonic(score, n.part, n.staff, n.measure, n.onset, staff, h.cx, h.cy)
    fix = None
    target = ""
    if d is not None:
        step, octave = P.from_diatonic_index(d + 7 * _octave_shift_at(score, n))
        fifths = score.key_at(n.part, n.measure, n.staff)
        alter = P.key_alterations(fifths)[step]
        target = P.note_name(step, alter, octave)
        midi = P.midi_from(step, alter, octave)
        if midi != n.midi:
            fix = Fix(f"Cambiar a {target}", "set_pitch", {"note_id": n.id, "midi": midi})
    where = f" a la altura de {target}" if target else ""
    figure = "" if h.cls == head_class(n) else f" con forma de {h.cls}"
    return Issue(
        "aviso", "imagen", f"Compás {num}: {n.name()} sin confirmar en la imagen",
        f"El motor leyó {n.name()} (pentagrama {n.staff}, tiempo {float(n.onset) + 1:g}). En ese instante la imagen "
        f"tiene una cabeza de nota{where}{figure} que ninguna otra nota explica: la nota existe, pero su altura "
        "o su figura pueden estar mal leídas. Compáralo con la imagen; no se ha cambiado nada.",
        measure=n.measure, note_ids=[n.id], evidence="sospecha", code="nota_sin_confirmar", fix=fix)
