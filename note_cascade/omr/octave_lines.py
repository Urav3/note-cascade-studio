"""Indicaciones de octava (8va, 8vb, 15ma, 15mb) resueltas con la imagen como prueba.

Por qué no basta con lo que dice el motor (comprobado con Audiveris 5.11 y «Sinner's Finale»):

* Audiveris escribe ``<octave-shift>`` solo al **inicio** de cada línea, nunca el final, y deja las notas
  con su **altura escrita** (el estándar MusicXML pide la sonora). Aplicarlo tal cual dejaría la octava
  activa hasta el final de la pieza; no aplicarlo deja el pasaje una octava por debajo.
* A veces no ve una línea (o la de la mano izquierda) y a veces inventa una.

Por eso el alcance se mide en la imagen: se buscan las líneas discontinuas encima y debajo de cada
pentagrama; una línea es de octava si tiene a su izquierda el rótulo («8va», «8vb», «15ma»…; da igual cómo
lo haya clasificado el lector de texto), si el motor marcó ahí una octava o si continúa una octava abierta
del sistema anterior (también de la página anterior). Una 8va afecta al pentagrama que tiene debajo y una
8vb al que tiene encima.

Antes de transponer se comprueba con la imagen si el motor entregó alturas escritas o sonoras: si la
cabeza de la nota en la imagen ya corresponde a la altura del motor, la altura es la escrita y hay que
transponer; si la altura del motor está una octava por encima de la escrita, ya estaba aplicada. Cada nota
se transpone como mucho una vez y el cambio queda registrado en la nota.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from note_cascade.image_processing import transforms as T
from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import Direction, Note, Score, log_pitch_change
from note_cascade.music_validation.issues import Fix, Issue

log = logging.getLogger(__name__)

LABEL_RE = re.compile(r"^\(?\s*(8|15)\s*(va|vb|ma|mb|a|b)?\s*(bassa|alta)?\s*\)?[\s\-–.·_]*$", re.I)


def label_kind(text: str) -> int:
    """Semitonos que indica un rótulo de octava («8va» → +12, «8vb»/«8va bassa» → −12, «15ma» → +24…)."""
    m = LABEL_RE.match(text.strip())
    if not m:
        return 0
    size = 12 if m.group(1) == "8" else 24
    suffix = (m.group(2) or "").lower()
    if m.group(3) and m.group(3).lower() == "bassa":
        return -size
    if suffix in ("vb", "mb", "b"):
        return -size
    if suffix in ("va", "ma", "a"):
        return size
    return 0   # un «8» suelto no basta


@dataclass
class OctaveLine:
    """Un tramo de línea de octava dentro de un sistema (coordenadas normalizadas de la página original)."""
    page: int
    system: int
    staff: int                 # pentagrama dentro del sistema (1 = el de arriba)
    semis: int                 # +12 (8va), −12 (8vb), +24 (15ma), −24 (15mb)
    x0: float
    x1: float
    reaches_end: bool = False  # llega al final del sistema (puede seguir en el siguiente)
    starts_at_start: bool = False
    labeled: bool = False
    engine: bool = False
    label: str = ""


@dataclass
class OctaveChain:
    lines: list[OctaveLine] = field(default_factory=list)

    @property
    def semis(self) -> int:
        return self.lines[0].semis

    @property
    def staff(self) -> int:
        return self.lines[0].staff


# ---------------------------------------------------------------------------
# Detección en la imagen
# ---------------------------------------------------------------------------

def _dash_runs(row_ink: np.ndarray, il: float) -> list[tuple[int, int]]:
    """Trazos cortos (guiones) de una franja horizontal: devuelve las secuencias de guiones."""
    cols = row_ink.any(axis=0).astype(np.int8)
    d = np.diff(np.concatenate(([0], cols, [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    dashes = [(s, e) for s, e in zip(starts, ends) if 0.15 * il <= e - s <= 2.2 * il]
    seqs, cur = [], []

    def close(run):
        # guiones de longitud y separación regulares (las líneas adicionales de notas seguidas no lo son)
        if len(run) < 4:
            return
        lens = np.array([e - s for s, e in run], dtype=float)
        gaps = np.array([b[0] - a[1] for a, b in zip(run, run[1:])], dtype=float)
        if lens.std() > 0.35 * lens.mean() or gaps.std() > 0.35 * max(1.0, gaps.mean()):
            return
        seqs.append((run[0][0], run[-1][1], len(run)))

    for s, e in dashes:
        if cur and s - cur[-1][1] > 2.6 * il:
            close(cur)
            cur = []
        cur.append((s, e))
    close(cur)
    return seqs


def _dashed_lines(pg, staff, side: str) -> list[tuple[float, float, float, int]]:
    """Líneas discontinuas encima (side='above') o debajo ('below') de un pentagrama: (y, x0, x1, guiones)."""
    il = staff.interline
    ink = pg.ink
    h, w = ink.shape
    if side == "above":
        ys = np.arange(staff.top - 7 * il, staff.top - 0.9 * il, 0.25 * il)
    else:
        ys = np.arange(staff.bottom + 0.9 * il, staff.bottom + 7 * il, 0.25 * il)
    xa, xb = int(max(0, staff.x0 - il)), int(min(w, staff.x1 + 1.5 * il))
    found = []
    for y in ys:
        ya, yb = int(max(0, y - 0.18 * il)), int(min(h, y + 0.18 * il + 1))
        if yb <= ya:
            continue
        for s, e, n in _dash_runs(ink[ya:yb, xa:xb] > 0, il):
            found.append((float(y), float(xa + s), float(xa + e), n))
    # una sola línea por tramo horizontal: la de más guiones (las vecinas en y son la misma línea)
    found.sort(key=lambda f: -f[3])
    kept: list[tuple[float, float, float, int]] = []
    for f in found:
        if any(abs(f[0] - k[0]) < 0.8 * il and min(f[2], k[2]) - max(f[1], k[1]) > 0 for k in kept):
            continue
        kept.append(f)
    return kept


def detect_octave_lines(score: Score, project, pages: dict, labels: list[tuple[int, str, float, float, float, float]],
                        engine_starts: list[tuple[int, int, int]]) -> list[OctaveLine]:
    """Tramos de octava en todas las páginas.

    ``labels``: (página, texto, x0, y0, x1, y1) en coordenadas normalizadas de la página original.
    ``engine_starts``: (compás, pentagrama, semitonos) de las octavas que marcó el motor.
    """
    lines: list[OctaveLine] = []
    measure_sys = _measure_systems(score, pages)
    for page_index, pg in sorted(pages.items()):
        geom = pg.geom
        w, h = pg.size
        page_labels = []
        for page, text, x0, y0, x1, y1 in labels:
            if page != page_index or not label_kind(text):
                continue
            ax, ay = T.apply_matrix(pg.m, x0 * w, y0 * h)
            bx, by = T.apply_matrix(pg.m, x1 * w, y1 * h)
            page_labels.append((label_kind(text), text, min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))
        used: set = set()
        for si, sysobj in enumerate(geom.systems):
            for k, staff_index in enumerate(sysobj.staves, start=1):
                staff = geom.staves[staff_index]
                il = staff.interline
                for side in ("above", "below"):
                    for y, x0, x1, _n in _dashed_lines(pg, staff, side):
                        lab = next((lb for lb in page_labels
                                    if lb[2] <= x0 + 2 * il and lb[4] >= x0 - 3.5 * il
                                    and abs((lb[3] + lb[5]) / 2 - y) < 1.4 * il), None)
                        if lab is not None:
                            used.add(lab)
                        semis = lab[0] if lab else 0
                        if semis and (semis > 0) != (side == "above"):
                            continue   # 8va va encima de su pentagrama y 8vb debajo
                        start_x = lab[2] if lab else x0
                        engine = _engine_at(engine_starts, measure_sys, page_index, si, k, start_x, pg, score,
                                            side)
                        if not semis and engine:
                            semis = engine
                        line = OctaveLine(page_index, si, k, semis, *_norm_x(pg, start_x, x1, y),
                                          reaches_end=x1 >= staff.x1 - 2.5 * il,
                                          starts_at_start=start_x <= staff.x0 + 16 * il,
                                          labeled=lab is not None, engine=bool(engine),
                                          label=lab[1] if lab else "")
                        if semis or line.starts_at_start:
                            lines.append(line)
        # rótulo sin línea (una sola nota o grupo corto): alcance = lo que está debajo del rótulo
        for si, sysobj in enumerate(geom.systems):
            for k, staff_index in enumerate(sysobj.staves, start=1):
                staff = geom.staves[staff_index]
                il = staff.interline
                for lb in page_labels:
                    if lb in used:
                        continue
                    above = lb[0] > 0
                    cy = (lb[3] + lb[5]) / 2
                    ok = (staff.top - 7 * il <= cy <= staff.top - 0.5 * il) if above else \
                        (staff.bottom + 0.5 * il <= cy <= staff.bottom + 7 * il)
                    if ok and staff.x0 - il <= lb[2] <= staff.x1:
                        if _nearest_staff(geom, sysobj, cy, above) != k:
                            continue
                        used.add(lb)
                        lines.append(OctaveLine(page_index, si, k, lb[0], *_norm_x(pg, lb[2], lb[4] + 1.0 * il, cy),
                                                labeled=True, label=lb[1]))
    # una línea entre dos pentagramas puede aparecer como «debajo del 1» y «encima del 2»: se queda la que
    # concuerda con su rótulo; un tramo sin rótulo ni motor solo sirve como continuación
    return _dedupe(lines)


def _nearest_staff(geom, sysobj, y: float, above: bool) -> int:
    """Pentagrama al que afecta una indicación a la altura y: el de debajo (8va) o el de encima (8vb)."""
    staves = [geom.staves[i] for i in sysobj.staves]
    if above:
        below = [(k, s) for k, s in enumerate(staves, start=1) if s.top > y]
        return min(below, key=lambda ks: ks[1].top - y)[0] if below else len(staves)
    over = [(k, s) for k, s in enumerate(staves, start=1) if s.bottom < y]
    return min(over, key=lambda ks: y - ks[1].bottom)[0] if over else 1


def _norm_x(pg, x0: float, x1: float, y: float) -> tuple[float, float]:
    w, _h = pg.size
    a = T.apply_matrix(pg.minv, x0, y)
    b = T.apply_matrix(pg.minv, x1, y)
    return a[0] / w, b[0] / w


def _dedupe(lines: list[OctaveLine]) -> list[OctaveLine]:
    out: list[OctaveLine] = []
    for ln in sorted(lines, key=lambda l: (not l.labeled, not l.engine)):
        dup = next((o for o in out if o.page == ln.page and o.system == ln.system
                    and min(o.x1, ln.x1) - max(o.x0, ln.x0) > 0.3 * min(o.x1 - o.x0, ln.x1 - ln.x0)
                    and (o.staff == ln.staff or (o.semis and ln.semis == 0))), None)
        if dup is None:
            out.append(ln)
    return out


def _measure_systems(score: Score, pages: dict) -> dict[int, tuple[int, int]]:
    """Compás → (página, sistema de la geometría de esa página)."""
    res = {}
    for m in score.measures:
        if m.position is None or m.position.page not in pages:
            continue
        pg = pages[m.position.page]
        x0, y0, x1, y1 = pg.to_proc(m.position)
        cy = (y0 + y1) / 2
        for si, s in enumerate(pg.geom.systems):
            if s.y0 - pg.geom.interline <= cy <= s.y1 + pg.geom.interline:
                res[m.index] = (m.position.page, si)
                break
    return res


def _engine_at(engine_starts, measure_sys, page, system, staff_no, x, pg, score, side) -> int:
    for mi, staff, semis in engine_starts:
        if measure_sys.get(mi) != (page, system) or staff != staff_no or (semis > 0) != (side == "above"):
            continue
        m = score.measures[mi]
        x0, _y0, x1, _y1 = pg.to_proc(m.position)
        if x0 - 3 * pg.geom.interline <= x <= x1:
            return semis
    return 0


# ---------------------------------------------------------------------------
# Encadenar tramos (saltos de sistema y de página)
# ---------------------------------------------------------------------------

def chain_lines(lines: list[OctaveLine], system_order: list[tuple[int, int]]) -> list[OctaveChain]:
    """Une los tramos de una misma octava que siguen en el sistema siguiente (aunque cambie de página)."""
    pos = {ps: i for i, ps in enumerate(system_order)}
    ordered = sorted((ln for ln in lines if (ln.page, ln.system) in pos),
                     key=lambda ln: (pos[(ln.page, ln.system)], ln.staff, ln.x0))
    chains: list[OctaveChain] = []
    open_by_staff: dict[tuple[int, int], OctaveChain] = {}   # (pentagrama, signo) → cadena abierta
    for ln in ordered:
        cont = None
        for (staff, sign), ch in list(open_by_staff.items()):
            last = ch.lines[-1]
            if staff == ln.staff and pos[(last.page, last.system)] + 1 == pos[(ln.page, ln.system)] \
                    and last.reaches_end and ln.starts_at_start and (ln.semis == 0 or ln.semis == ch.semis):
                cont = ch
                break
        if cont is not None:
            ln.semis = cont.semis
            cont.lines.append(ln)
        elif ln.semis:
            cont = OctaveChain([ln])
            chains.append(cont)
        else:
            continue   # línea discontinua sin rótulo que no continúa nada: rit., cresc., etc.
        # una cadena solo sigue abierta si este tramo llega al final del sistema
        open_by_staff = {key: ch for key, ch in open_by_staff.items()
                         if pos[(ch.lines[-1].page, ch.lines[-1].system)] >= pos[(ln.page, ln.system)] - 1
                         and ch is not cont}
        if ln.reaches_end:
            open_by_staff[(ln.staff, 1 if cont.semis > 0 else -1)] = cont
    return chains


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

def notes_under(score: Score, chain: OctaveChain, measure_sys: dict[int, tuple[int, int]]) -> list[Note]:
    sel = []
    spans = {(ln.page, ln.system): ln for ln in chain.lines}
    for n in score.notes:
        if n.rest or n.staff != chain.staff:
            continue
        where = measure_sys.get(n.measure)
        ln = spans.get(where) if where else None
        if ln is None:
            continue
        if n.position is None or n.position.approximate:
            m = score.measures[n.measure].position
            inside = m is not None and m.x >= ln.x0 - 0.01 and m.x + m.w <= ln.x1 + 0.02
        else:
            cx = n.position.x + n.position.w / 2
            inside = ln.x0 - 0.008 <= cx <= ln.x1 + 0.012
        if inside:
            sel.append(n)
    return sel


def already_sounding(notes: list[Note], semis: int, written_diatonic: dict[str, int]) -> bool | None:
    """¿Las alturas del motor ya incluyen la octava? Se compara con la cabeza en la imagen.

    Devuelve True/False según la mayoría, o None si no hay pruebas."""
    votes = Counter()
    for n in notes:
        d = written_diatonic.get(n.id)
        if d is None:
            continue
        diff = P.diatonic_index(n.step, n.octave) - d
        if diff == 0:
            votes["escrita"] += 1
        elif diff == 7 * (semis // 12):
            votes["sonora"] += 1
    if not votes:
        return None
    return votes["sonora"] > votes["escrita"]


def apply_chains(score: Score, chains: list[OctaveChain], measure_sys: dict[int, tuple[int, int]],
                 written_diatonic: dict[str, int], already: set[str] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    done = already if already is not None else set()
    kinds = {12: "8va", -12: "8vb", 24: "15ma", -24: "15mb"}
    for ch in chains:
        notes = [n for n in notes_under(score, ch, measure_sys) if n.id not in done]
        if not notes:
            continue
        name = kinds.get(ch.semis, f"{ch.semis:+d}")
        m0 = min(n.measure for n in notes)
        m1 = max(n.measure for n in notes)
        span = f"compás {score.measures[m0].number}" if m0 == m1 else \
            f"compases {score.measures[m0].number}–{score.measures[m1].number}"
        sounding = already_sounding(notes, ch.semis, written_diatonic)
        first = min(notes, key=lambda n: (n.measure, n.onset))
        last = max(notes, key=lambda n: (n.measure, n.onset + n.duration))
        score.directions.append(Direction("octave_shift", first.measure, first.onset, first.part, ch.staff,
                                          "down" if ch.semis > 0 else "up", size=8 if abs(ch.semis) == 12 else 15,
                                          text=name, source="texto"))
        score.directions.append(Direction("octave_shift", last.measure, last.onset + last.duration, last.part,
                                          ch.staff, "stop", size=8 if abs(ch.semis) == 12 else 15, source="texto"))
        done.update(n.id for n in notes)
        if sounding:
            issues.append(Issue("info", "altura", f"«{name}» ya aplicada por el motor ({span})",
                                f"Las notas del pentagrama {ch.staff} bajo la línea de {name} ya tenían su altura real: "
                                "no se vuelven a transponer.", measure=m0, measure_end=m1, evidence="comprobado",
                                code="octava_texto"))
            continue
        for n in notes:
            old = n.midi
            n.midi = int(max(0, min(127, n.midi + ch.semis)))
            n.step, n.alter, n.octave = P.spell_midi(n.midi, score.key_at(n.part, n.measure, n.staff),
                                                     1 if n.alter > 0 else -1 if n.alter < 0 else None)
            log_pitch_change(n, old, f"indicación «{name}» del pentagrama {ch.staff}")
        pieces = len(ch.lines)
        labeled = ch.lines[0].labeled
        how = "rótulo y línea medidos en la imagen" if labeled else "inicio marcado por el motor y línea medida en la imagen"
        # Con el rótulo leído y la línea medida en la imagen, la indicación está comprobada: es información
        # (no marca las notas «revisar»). Si el inicio solo lo dio el motor, queda como aviso.
        issues.append(Issue(
            "info" if labeled else "aviso", "altura", f"Indicación «{name}» aplicada ({span})",
            f"Se desplazaron {len(notes)} nota(s) del pentagrama {ch.staff} para que suenen "
            f"{'una octava' if abs(ch.semis) == 12 else 'dos octavas'} {'más agudas' if ch.semis > 0 else 'más graves'} "
            f"({how}{', continúa en ' + str(pieces - 1) + ' sistema(s) más' if pieces > 1 else ''}). "
            "Escúchalo; si no corresponde, deshazlo con la corrección.", measure=m0, measure_end=m1,
            note_ids=sorted(n.id for n in notes), evidence="comprobado", code="octava_texto",
            fix=Fix(f"Quitar la indicación {name} aplicada", "octave_shift",
                    {"part": 0, "staff": ch.staff, "from_measure": m0, "to_measure": m1, "semitones": -ch.semis,
                     "note_ids": sorted(n.id for n in notes), "remove_directions": True})))
    return issues


# ---------------------------------------------------------------------------
# Proceso completo (con las imágenes del proyecto)
# ---------------------------------------------------------------------------

def resolve_octaves(score: Score, project) -> list[Issue]:
    """Detecta y aplica las indicaciones de octava; sustituye las marcas del motor por las medidas."""
    from note_cascade.omr import image_check as IC

    engine_dirs = [d for d in score.directions if d.kind == "octave_shift" and d.source != "texto"]
    engine_starts = [(d.measure, d.staff or 1, (12 if (d.size or 8) == 8 else 24) * (1 if d.value == "down" else -1))
                     for d in engine_dirs if d.value in ("down", "up")]
    pages = {}
    for rec in project.pages:
        try:
            pg = IC._load_page(project, rec)
        except Exception as exc:  # auxiliar
            log.warning("No se pudo preparar la página %s para las octavas: %s", rec.index + 1, exc)
            pg = None
        if pg is not None:
            pages[rec.index] = pg
    issues: list[Issue] = []
    if pages:
        labels = [(rec.index, e.text, e.x0, e.y0, e.x1, e.y1) for rec in project.pages for e in rec.text_elements()]
        lines = detect_octave_lines(score, project, pages, labels, engine_starts)
        measure_sys = _measure_systems(score, pages)
        order = sorted({v for v in measure_sys.values()})
        chains = chain_lines(lines, order)
        written = _written_from_image(score, pages)
        issues += apply_chains(score, chains, measure_sys, written)
    # tramos aplicados (pentagrama, compás inicial, compás final)
    applied = []
    starts = sorted((d for d in score.directions if d.kind == "octave_shift" and d.source == "texto"),
                    key=lambda d: (d.staff or 1, d.measure, d.onset))
    open_start = {}
    for d in starts:
        if d.value in ("down", "up"):
            open_start[d.staff or 1] = d.measure
        elif d.value == "stop" and (d.staff or 1) in open_start:
            applied.append((d.staff or 1, open_start.pop(d.staff or 1), d.measure))
    # marcas del motor que la imagen no confirma: aviso para revisar, sin aplicar nada
    for d in engine_dirs:
        if d.value not in ("down", "up"):
            continue
        if any(staff == (d.staff or 1) and a - 1 <= d.measure <= b for staff, a, b in applied):
            continue   # la imagen confirma una octava que cubre esa marca
        semis = (12 if (d.size or 8) == 8 else 24) * (1 if d.value == "down" else -1)
        name = {12: "8va", -12: "8vb", 24: "15ma", -24: "15mb"}[semis]
        m = score.measures[d.measure]
        issues.append(Issue(
            "aviso", "altura", f"Compás {m.number}: posible «{name}» sin confirmar",
            f"El motor marcó una {name} en el pentagrama {d.staff or 1}, pero en la imagen no se encontró la línea "
            "ni el rótulo. No se aplicó: si en la partitura hay una indicación de octava, aplícala en la revisión.",
            measure=d.measure, evidence="sospecha", code="octava_motor_dudosa",
            fix=Fix(f"Aplicar {name} hasta el final del sistema", "octave_shift",
                    {"part": d.part, "staff": d.staff or 1, "from_measure": d.measure,
                     "to_measure": _system_end(score, d.measure), "semitones": semis})))
    # las marcas del motor se sustituyen por las medidas (que tienen principio y fin)
    score.directions = [d for d in score.directions if not (d.kind == "octave_shift" and d.source != "texto")]
    return issues


def _system_end(score: Score, mi: int) -> int:
    b = mi
    while b + 1 < len(score.measures) and not score.measures[b + 1].new_system:
        b += 1
    return b


def _written_from_image(score: Score, pages: dict) -> dict[str, int]:
    from note_cascade.omr import image_check as IC
    res = {}
    for n in score.notes:
        if n.rest or n.position is None or n.position.approximate or n.position.page not in pages:
            continue
        pg = pages[n.position.page]
        staff = IC._measure_staff(score, pg, n.measure, n.staff)
        if staff is None:
            continue
        x0, y0, x1, y1 = pg.to_proc(n.position)
        d = IC.written_diatonic(score, n.part, n.staff, n.measure, n.onset, staff, (x0 + x1) / 2, (y0 + y1) / 2)
        if d is not None:
            res[n.id] = d
    return res
