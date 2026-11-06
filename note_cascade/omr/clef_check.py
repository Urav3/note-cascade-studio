"""Cambios de clave dentro de un pentagrama que el motor no leyó.

Comprobado con «Sinner's Finale» (página 4, compás 58): la mano izquierda pasa a clave de sol a mitad de
compás y Audiveris sigue leyendo en clave de fa, así que esas notas salen con una altura que no corresponde a
su posición; además toma el rizo inferior de la clave por una nota.

El programa aprende la forma de cada clave de las que hay al principio de los pentagramas (su clave se
conoce), las busca dentro de los compases (los cambios de clave se dibujan más pequeños) y, donde la clave
de la imagen no coincide con la que usó el motor:

* inserta el cambio de clave en el modelo;
* recalcula la altura **escrita** de las notas siguientes de ese pentagrama a partir de la posición de su
  cabeza (hasta el siguiente cambio de clave), conservando la armadura y las alteraciones del compás;
* quita las «notas» que en realidad son parte del dibujo de la clave.

Todo queda como aviso con su explicación.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from fractions import Fraction

import cv2
import numpy as np

from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import ClefChange, Note, Score, log_pitch_change
from note_cascade.music_validation.issues import Fix, Issue

log = logging.getLogger(__name__)

SCALES = (0.6, 0.68, 0.76, 0.85, 1.0)
MATCH = 0.72
CLEF_NAMES = {"G": "clave de sol", "F": "clave de fa", "C": "clave de do"}


def _staff_start_clef(pg, staff):
    """Mancha de tinta de la clave al principio de un pentagrama: (x0, y0, x1, y1) o None."""
    il = staff.interline
    xa, xb = int(max(0, staff.x0)), int(min(pg.ink.shape[1], staff.x0 + 5 * il))
    ya, yb = int(max(0, staff.top - 3.5 * il)), int(min(pg.ink.shape[0], staff.bottom + 3.5 * il))
    region = (pg.ink[ya:yb, xa:xb] > 0).astype(np.uint8)
    if region.size == 0:
        return None
    region = cv2.dilate(region, np.ones((3, 3), np.uint8))   # une los trozos que cortó el borrado de líneas
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(region, 8)
    best = None
    for k in range(1, n):
        x, y, w, h, _a = stats[k]
        if h < 2.5 * il or w < 1.2 * il or w > 4.5 * il:
            continue
        if best is None or h * w > best[2] * best[3]:
            best = (x, y, w, h)
    if best is None:
        return None
    x, y, w, h = best
    return xa + x, ya + y, xa + x + w, ya + y + h


def learn_clefs(score: Score, pages: dict, measure_sys: dict) -> dict[str, tuple[np.ndarray, float, float, float]]:
    """Plantilla de cada clave: (imagen, ancho/il, alto/il, posición del borde superior respecto de la línea
    superior del pentagrama en interlineados)."""
    from note_cascade.omr.image_check import _measure_staff
    samples: dict[str, list] = defaultdict(list)
    first_of_system: dict[tuple[int, int], int] = {}
    for mi, ps in sorted(measure_sys.items()):
        first_of_system.setdefault(ps, mi)
    for (page, _si), mi in first_of_system.items():
        pg = pages[page]
        for staff_no in range(1, (score.parts[0].staves if score.parts else 2) + 1):
            clef = score.clef_at(0, staff_no, mi, Fraction(0))
            staff = _measure_staff(score, pg, mi, staff_no)
            if clef is None or staff is None:
                continue
            box = _staff_start_clef(pg, staff)
            if box is None:
                continue
            x0, y0, x1, y1 = box
            il = staff.interline
            patch = pg.ink[y0:y1, x0:x1]
            samples[clef.sign].append((cv2.resize(patch, (24, 48), interpolation=cv2.INTER_AREA),
                                       (x1 - x0) / il, (y1 - y0) / il, (y0 - staff.top) / il))
    out = {}
    for sign, items in samples.items():
        if len(items) >= 2:
            tpl = np.median(np.stack([i[0] for i in items]), axis=0).astype(np.float32)
            out[sign] = (tpl, float(np.median([i[1] for i in items])), float(np.median([i[2] for i in items])),
                         float(np.median([i[3] for i in items])))
    return out


def _find_clefs_in_staff(pg, staff, templates) -> list[tuple[str, float, float, float, float, float]]:
    """Claves dentro del pentagrama (sin contar el principio del sistema): (signo, x0, y0, x1, y1, puntuación)."""
    il = staff.interline
    found = []
    for sign, (tpl, rw, rh, top) in templates.items():
        for s in SCALES:
            tw, th = max(6, int(round(rw * il * s))), max(10, int(round(rh * il * s)))
            t = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_LINEAR)
            if t.std() < 0.05:
                continue
            # la clave reducida conserva su posición respecto de las líneas: se escala alrededor del centro
            center = staff.top + (top + rh / 2) * il
            ya = int(max(0, center - th / 2 - 0.8 * il))
            yb = int(min(pg.ink.shape[0], center + th / 2 + 0.8 * il))
            xa = int(staff.x0 + 6 * il)
            xb = int(min(pg.ink.shape[1], staff.x1 + 1 * il))
            region = pg.ink[ya:yb, xa:xb]
            if region.shape[0] < th or region.shape[1] < tw:
                continue
            res = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= MATCH)
            for k in np.argsort(-res[ys, xs]):
                x, y = xa + int(xs[k]), ya + int(ys[k])
                found.append((sign, float(x), float(y), float(x + tw), float(y + th), float(res[ys[k], xs[k]])))
    found.sort(key=lambda f: -f[5])
    kept = []
    for f in found:
        if any(min(f[3], k[3]) - max(f[1], k[1]) > 0 for k in kept):
            continue
        kept.append(f)
    return kept


def check_clefs(score: Score, project) -> list[Issue]:
    from note_cascade.omr import image_check as IC
    from note_cascade.omr.octave_lines import _measure_systems
    if len(score.parts) != 1 or not project.pages:
        return []
    pages = {}
    for rec in project.pages:
        try:
            pg = IC._load_page(project, rec)
        except Exception:
            pg = None
        if pg is not None:
            pages[rec.index] = pg
    if not pages:
        return []
    measure_sys = _measure_systems(score, pages)
    templates = learn_clefs(score, pages, measure_sys)
    if not templates:
        return []
    issues: list[Issue] = []
    engine_clefs = list(score.clefs)   # las del motor, antes de añadir ninguna
    by_sys: dict[tuple[int, int], list[int]] = defaultdict(list)
    for mi, ps in measure_sys.items():
        by_sys[ps].append(mi)
    for (page, si), mis in sorted(by_sys.items()):
        pg = pages[page]
        for staff_no in range(1, score.parts[0].staves + 1):
            staff = IC._measure_staff(score, pg, mis[0], staff_no)
            if staff is None:
                continue
            # de izquierda a derecha: cada clave rige hasta la siguiente
            for sign, x0, y0, x1, y1, sc in sorted(_find_clefs_in_staff(pg, staff, templates), key=lambda f: f[1]):
                issue = _apply_clef(score, pg, staff, staff_no, sorted(mis), sign, (x0, y0, x1, y1), sc, engine_clefs)
                if issue is not None:
                    issues.append(issue)
    return issues


def _apply_clef(score: Score, pg, staff, staff_no: int, mis: list[int], sign: str, box, sc: float,
                engine_clefs: list[ClefChange]) -> Issue | None:
    from note_cascade.omr import image_check as IC
    x0, y0, x1, y1 = box
    il = staff.interline
    # compás y momento: la primera nota de ese pentagrama a la derecha de la clave
    notes = []
    for mi in mis:
        for n in score.notes_in_measure(mi):
            if n.staff != staff_no or n.position is None or n.position.approximate:
                continue
            nx0, ny0, nx1, ny1 = pg.to_proc(n.position)
            notes.append((n, (nx0 + nx1) / 2, (ny0 + ny1) / 2))
    after = sorted((t for t in notes if t[1] > x1 - 0.2 * il), key=lambda t: (t[0].measure, t[0].onset))
    if after:
        mi, onset = after[0][0].measure, after[0][0].onset
    else:
        last = mis[-1]
        if last + 1 >= len(score.measures):
            return None
        mi, onset = last + 1, Fraction(0)   # clave al final del sistema: rige desde el compás siguiente
    current = score.clef_at(0, staff_no, mi, onset)
    if current is not None and current.sign == sign:
        return None
    if any(c.staff == staff_no and c.sign == sign and (c.measure, c.onset) >= (mi, onset)
           and c.measure <= mi + 1 for c in score.clefs):
        return None    # el motor ya la tiene (o en el compás siguiente)
    line = 2 if sign == "G" else 4 if sign == "F" else 3
    new = ClefChange(0, staff_no, mi, onset, sign, line)
    # «notas» dentro del dibujo de la clave: no existen
    ghosts = [n for n, cx, cy in notes if x0 - 0.2 * il <= cx <= x1 + 0.2 * il and y0 <= cy <= y1
              and not n.image_verified]
    old_ref = current
    nxt = min(((c.measure, c.onset) for c in score.clefs if c.staff == staff_no and (c.measure, c.onset) > (mi, onset)),
              default=None)
    score.clefs.append(new)
    changed = []
    for n in score.notes:
        if n.rest or n.staff != staff_no or n in ghosts:
            continue
        if (n.measure, n.onset) < (mi, onset) or (nxt is not None and (n.measure, n.onset) >= nxt):
            continue
        old = n.midi
        d_new = None
        if n.position is not None and not n.position.approximate and n.position.page == pg.rec.index:
            nx0, ny0, nx1, ny1 = pg.to_proc(n.position)
            d_new = IC.written_diatonic(score, n.part, n.staff, n.measure, n.onset, staff, (nx0 + nx1) / 2,
                                        (ny0 + ny1) / 2)
        if d_new is None and old_ref is not None:
            d_new = P.diatonic_index(n.step, n.octave) + (
                P.diatonic_index(*P.clef_middle_line(sign, line)) - P.diatonic_index(*P.clef_middle_line(old_ref.sign, old_ref.line)))
        if d_new is None:
            continue
        step, octave = P.from_diatonic_index(d_new)
        if (step, octave) == (n.step, n.octave):
            continue
        alter = n.alter if n.accidental_shown else P.key_alterations(score.key_at(n.part, n.measure, n.staff))[step]
        n.step, n.octave, n.alter = step, octave, alter
        n.midi = P.midi_from(step, alter, octave)
        log_pitch_change(n, old, f"{CLEF_NAMES.get(sign, sign)} leída en la imagen (compás {score.measures[mi].number})")
        changed.append(n)
    if ghosts:
        score.notes = [n for n in score.notes if n not in ghosts]
    if not changed and not ghosts:
        score.clefs.remove(new)
        return None
    engine_sign = _sign_at(engine_clefs, staff_no, mi, onset)
    if engine_sign == sign and not ghosts:
        score.sort_notes()
        return None   # vuelve a la clave con la que ya leía el motor: no es un error suyo
    score.sort_notes()
    msg = (f"En la imagen hay un cambio a {CLEF_NAMES.get(sign, sign)} en el pentagrama {staff_no} que el motor no "
           f"leyó. Se recalcularon {len(changed)} nota(s) desde la posición de su cabeza")
    if ghosts:
        msg += f" y se quitaron {len(ghosts)} nota(s) que eran parte del dibujo de la clave"
    return Issue("aviso", "altura", f"Compás {score.measures[mi].number}: {CLEF_NAMES.get(sign, sign)} leída en la imagen",
                 msg + ". Escúchalo y compáralo con la partitura.", measure=mi,
                 note_ids=[n.id for n in changed], evidence="comprobado", code="clave_imagen",
                 fix=Fix(f"Volver a {CLEF_NAMES.get(old_ref.sign, 'la clave anterior') if old_ref else 'la clave anterior'}",
                         "set_clef", {"part": 0, "staff": staff_no, "measure": mi,
                                      "sign": old_ref.sign if old_ref else "F", "line": old_ref.line if old_ref else 4})
                 if old_ref else None)


def _sign_at(clefs: list[ClefChange], staff_no: int, mi: int, onset) -> str | None:
    found = None
    for c in sorted((c for c in clefs if c.staff == staff_no), key=lambda c: (c.measure, c.onset)):
        if (c.measure, c.onset) <= (mi, onset):
            found = c.sign
    return found
