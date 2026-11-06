"""Reparaciones del resultado del motor con evidencia clara, registradas siempre como aviso.

* Acordes ligados sobre la barra de compás cuya continuación perdió las alteraciones.
* Notas que un motor no vio y el otro sí (complemento entre motores).
* Indicación de compás que solo leyó el segundo motor.
"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction

from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import Note, PagePosition, Score, log_pitch_change, new_id
from note_cascade.music_model.timeline import measure_lengths
from note_cascade.music_validation.issues import Fix, Issue


# ---------------------------------------------------------------------------
# Ligaduras entre compases
# ---------------------------------------------------------------------------

def repair_tied_accidentals(score: Score) -> list[Issue]:
    """Una nota ligada conserva su altura: si el acorde de llegada tiene las mismas notas escritas
    (mismo nombre y octava) pero el motor les quitó la alteración —porque en el compás nuevo no se
    vuelve a escribir—, se les devuelve la del acorde de salida.

    Solo se hace si hay una ligadura reconocida en ese punto (en cualquiera de los pentagramas)."""
    issues: list[Issue] = []
    lengths = measure_lengths(score)
    for mi in range(len(score.measures) - 1):
        end = lengths[mi]
        ending = [n for n in score.notes_in_measure(mi, include_rests=False) if n.onset + n.duration == end]
        starting = [n for n in score.notes_in_measure(mi + 1, include_rests=False) if n.onset == 0]
        if not ending or not starting:
            continue
        if not any(n.tie_start for n in ending) and not any(n.tie_stop for n in starting):
            continue
        groups_a: dict[tuple, list[Note]] = defaultdict(list)
        groups_b: dict[tuple, list[Note]] = defaultdict(list)
        for n in ending:
            groups_a[(n.part, n.staff)].append(n)
        for n in starting:
            groups_b[(n.part, n.staff)].append(n)
        for key, a in groups_a.items():
            b = groups_b.get(key)
            if not b:
                continue
            by_name = {(n.step, n.octave): n for n in a}
            if len(by_name) != len(a) or {(n.step, n.octave) for n in b} != set(by_name):
                continue
            changed = []
            for n in b:
                src = by_name[(n.step, n.octave)]
                if n.alter != src.alter and not n.accidental_shown:
                    old = n.midi
                    n.alter = src.alter
                    n.midi = src.midi
                    log_pitch_change(n, old, "alteración de la nota ligada")
                    changed.append(n)
                src.tie_start = True
                n.tie_stop = True
            if changed:
                names = ", ".join(n.name() for n in changed)
                issues.append(Issue(
                    "aviso", "altura", f"Compás {score.measures[mi + 1].number}: alteraciones de la ligadura",
                    f"El acorde del compás {score.measures[mi].number} sigue ligado en el siguiente; el motor leyó "
                    f"la continuación sin sus alteraciones. Se mantiene la altura de la nota ligada ({names}).",
                    measure=mi + 1, note_ids=[n.id for n in changed], evidence="comprobado",
                    code="ligadura_alteracion"))
    return issues


# ---------------------------------------------------------------------------
# Complemento entre motores
# ---------------------------------------------------------------------------

def _staff_events(score: Score, mi: int, staff: int) -> set[tuple[Fraction, int]]:
    return {(n.onset, n.midi) for n in score.notes_in_measure(mi, include_rests=False)
            if n.staff == staff and n.part == 0}


def _staff_complete(score: Score, mi: int, staff: int, length: Fraction) -> bool:
    """Cada voz del pentagrama ocupa exactamente el compás (notas y silencios sin huecos ni sobrantes)."""
    voices: dict[int, list[Note]] = defaultdict(list)
    for n in score.notes_in_measure(mi):
        if n.part == 0 and n.staff == staff and not n.grace:
            voices[n.voice].append(n)
    if not voices:
        return False
    for notes in voices.values():
        starts = sorted({n.onset for n in notes})
        t = Fraction(0)
        for s in starts:
            if s != t:
                return False
            t = s + max(n.duration for n in notes if n.onset == s)
        if t != length:
            return False
    return True


def complement_from_secondary(primary: Score, secondary: Score, pairs: list[tuple[int | None, int | None]],
                              engine: str, geometry: dict | None = None) -> list[Issue]:
    """Añade al resultado principal las notas que solo vio el segundo motor, compás a compás y
    pentagrama a pentagrama, cuando hay evidencia clara:

    * el segundo motor contiene **todas** las notas que vio el principal en ese pentagrama (misma
      altura y mismo momento), y además otras;
    * su versión del pentagrama es rítmicamente completa (las voces llenan el compás).

    Si no, no se mezcla nada (queda la propuesta de «usar la versión del otro motor»)."""
    if len(primary.parts) != 1 or len(secondary.parts) != 1:
        return []
    lengths_a = measure_lengths(primary)
    lengths_b = measure_lengths(secondary)
    issues: list[Issue] = []
    for i, j in pairs:
        if i is None or j is None or lengths_a[i] != lengths_b[j]:
            continue
        added: list[Note] = []
        for staff in (1, 2):
            a = _staff_events(primary, i, staff)
            b_notes = [n for n in secondary.notes_in_measure(j, include_rests=False) if n.staff == staff and n.part == 0]
            b = {(n.onset, n.midi) for n in b_notes}
            if not b or not a <= b or a == b:
                continue
            if not _staff_complete(secondary, j, staff, lengths_b[j]):
                continue
            for n in b_notes:
                if (n.onset, n.midi) in a:
                    continue
                new = Note(new_id(), 0, staff, n.voice, i, n.onset, n.duration, step=n.step, alter=n.alter,
                           octave=n.octave, midi=n.midi, type_name=n.type_name, dots=n.dots, tuplet=n.tuplet,
                           tie_start=n.tie_start, tie_stop=n.tie_stop, articulations=list(n.articulations),
                           source=engine, engine_midi=n.engine_midi)
                new.accidental_shown = n.accidental_shown
                new.position = _estimate_position(primary, i, new) or (
                    PagePosition(n.position.page, n.position.x, n.position.y, n.position.w, n.position.h, True)
                    if n.position else None)
                primary.notes.append(new)
                a.add((n.onset, n.midi))
                added.append(new)
        if added:
            m = primary.measures[i]
            names = ", ".join(n.name() for n in sorted(added, key=lambda n: (n.onset, n.midi)))
            issues.append(Issue(
                "aviso", "comparacion", f"Compás {m.number}: notas añadidas por {engine}",
                f"El motor principal no vio {len(added)} nota(s) que {engine} sí reconoció ({names}). Se añadieron "
                f"porque {engine} confirma todas las demás notas del compás y su ritmo es completo. Escúchalo y "
                "compáralo con la imagen.",
                measure=i, note_ids=[n.id for n in added], evidence="comprobado", code="complemento_motor"))
    if any(i for i in issues):
        primary.sort_notes()
    return issues


def _estimate_position(score: Score, mi: int, note: Note) -> PagePosition | None:
    """Posición aproximada en la página: x de otra nota del mismo instante, y por la distancia en
    grados a otra nota del mismo pentagrama cuya posición se conoce."""
    same_time = [n for n in score.notes_in_measure(mi, include_rests=False)
                 if n.position is not None and not n.position.approximate and n.onset == note.onset]
    same_staff = [n for n in score.notes_in_measure(mi, include_rests=False)
                  if n.position is not None and not n.position.approximate and n.staff == note.staff]
    if not same_staff:
        return None
    ref_x = next((n for n in same_time if n.staff == note.staff), None) or (same_time[0] if same_time else None)
    ref_y = min(same_staff, key=lambda n: abs(P.diatonic_index(n.step, n.octave) - P.diatonic_index(note.step, note.octave)))
    half = ref_y.position.h / 2 * 0.95  # medio interlineado ≈ media altura de una cabeza
    steps = P.diatonic_index(note.step, note.octave) - P.diatonic_index(ref_y.step, ref_y.octave)
    if ref_x is None:
        mp = score.measures[mi].position
        if mp is None:
            return None
        x = mp.x + mp.w * 0.08
    else:
        x = ref_x.position.x
    rp = ref_y.position
    return PagePosition(rp.page, x, rp.y - steps * half, rp.w, rp.h, approximate=False)


# ---------------------------------------------------------------------------
# Indicación de compás
# ---------------------------------------------------------------------------

def adopt_time_signature(primary: Score, secondary: Score, pairs, engine: str) -> list[Issue]:
    """Si el principal no leyó ninguna indicación de compás y el segundo motor sí, se copian las del
    segundo motor compás a compás (con sus cambios, p. ej. un 3/8 en medio de un 12/8), siempre que
    la duración real más frecuente de los compases del principal coincida con la indicación inicial."""
    if any(m.time_sig for m in primary.measures) or not pairs:
        return []
    from note_cascade.music_validation.checks import inferred_measure_length
    first = next((j for i, j in pairs if i == 0 and j is not None), None)
    if first is None or not secondary.measures[first].time_sig:
        return []
    ts0 = secondary.measures[first].time_sig
    mode = inferred_measure_length(primary)
    if not mode or Fraction(ts0[0] * 4, ts0[1]) != mode:
        return []
    current = ts0
    changes = []
    for i, j in pairs:
        if i is None:
            continue
        ts = secondary.measures[j].time_sig if j is not None and secondary.measures[j].time_sig else current
        m = primary.measures[i]
        m.time_sig = ts
        m.time_sig_change = i == 0 or ts != current
        if ts != current:
            changes.append(f"{ts[0]}/{ts[1]} en el compás {m.number}")
        current = ts
    extra = f" Cambios: {', '.join(changes[:8])}." if changes else ""
    return [Issue("info", "estructura", f"Compás {ts0[0]}/{ts0[1]} tomado de {engine}",
                  f"El motor principal no leyó la indicación de compás; {engine} leyó {ts0[0]}/{ts0[1]}, que coincide "
                  f"con la duración real de los compases.{extra}", measure=0, evidence="comprobado",
                  code="compas_motor")]


# ---------------------------------------------------------------------------
# Silencios de compás completo y voces desbordadas
# ---------------------------------------------------------------------------

def _nominal_lengths(score: Score) -> list[Fraction]:
    """Duración que marca la indicación de compás (la real si no hay indicación)."""
    played = measure_lengths(score)
    return [Fraction(m.time_sig[0] * 4, m.time_sig[1]) if m.time_sig else played[m.index] for m in score.measures]


def repair_measure_rests(score: Score) -> list[Issue]:
    """Un pentagrama que en todo el compás solo tiene un silencio de redonda es un silencio de compás
    completo (en 12/8 dura 6 negras, no 4) y empieza al principio del compás."""
    lengths = _nominal_lengths(score)
    fixed: list[int] = []
    for m in score.measures:
        length = lengths[m.index]
        by_staff: dict[tuple[int, int], list[Note]] = defaultdict(list)
        for n in score.notes_in_measure(m.index):
            by_staff[(n.part, n.staff)].append(n)
        changed = False
        for notes in by_staff.values():
            if len(notes) != 1 or not notes[0].rest:
                continue
            r = notes[0]
            if r.measure_rest and r.onset == 0 and r.duration == length:
                continue
            if r.type_name not in (None, "whole") and not r.measure_rest:
                continue
            if r.onset == 0 and r.duration == length:
                continue
            r.onset, r.duration, r.measure_rest = Fraction(0), length, True
            changed = True
        if changed:
            fixed.append(m.index)
    if not fixed:
        return []
    nums = ", ".join(score.measures[i].number for i in fixed[:12]) + ("…" if len(fixed) > 12 else "")
    return [Issue("info", "ritmo", "Silencios de compás completo",
                  f"El motor leyó como silencio de 4 tiempos el silencio de compás completo de los compases {nums}; "
                  "ahora ocupan el compás entero.", measure=fixed[0], evidence="comprobado", code="silencio_compas")]


def repair_voice_overflow(score: Score) -> list[Issue]:
    """Acorde largo que ocupa todo el compás y, en la misma voz, una melodía «después» de él que se
    sale del compás: la melodía es en realidad otra voz que suena encima del acorde y termina con el
    compás (el motor no separó las voces)."""
    lengths = _nominal_lengths(score)
    issues: list[Issue] = []
    for m in score.measures:
        length = lengths[m.index]
        if length <= 0:
            continue
        notes = score.notes_in_measure(m.index)
        for (part, staff, voice) in {(n.part, n.staff, n.voice) for n in notes}:
            v = [n for n in notes if (n.part, n.staff, n.voice) == (part, staff, voice) and not n.grace]
            held = [n for n in v if n.onset == 0 and n.duration == length and not n.rest]
            tail = sorted((n for n in v if n.onset >= length), key=lambda n: n.onset)
            if not held or not tail or len(held) + len(tail) != len(v) or tail[0].onset != length:
                continue
            end = max(n.end for n in tail)
            span = end - length
            if span > length or any(n.position is None for n in tail if not n.rest):
                continue
            # las posiciones en la página deben estar a la derecha del acorde (si se conocen)
            hx = min((n.position.x for n in held if n.position), default=None)
            if hx is not None and any(n.position is not None and n.position.x <= hx for n in tail):
                continue
            used = {n.voice for n in notes if n.part == part}
            new_voice = next(k for k in range(voice + 1, voice + 20) if k not in used)
            for n in tail:
                n.onset -= span
                n.voice = new_voice
            issues.append(Issue(
                "aviso", "ritmo", f"Compás {m.number}: voz separada",
                "El motor puso la melodía después del acorde largo, en la misma voz, y el compás se salía de su "
                "duración. Se ha separado en otra voz que suena sobre el acorde y termina con el compás. "
                "Escúchalo para confirmarlo.", measure=m.index, note_ids=[n.id for n in tail if not n.rest],
                evidence="sospecha", code="voz_separada"))
    if issues:
        score.sort_notes()
    return issues


def repair_missing_dots(score: Score) -> list[Issue]:
    """Pentagrama con un único acorde que empieza el compás y ocupa justo 2/3 de él: le falta el
    puntillo (p. ej. redonda sin puntillo en 12/8 o blanca sin puntillo en 6/8 o 3/4)."""
    lengths = _nominal_lengths(score)
    fixed: list[Note] = []
    for m in score.measures:
        length = lengths[m.index]
        by_staff: dict[tuple[int, int], list[Note]] = defaultdict(list)
        for n in score.notes_in_measure(m.index):
            if not n.grace:
                by_staff[(n.part, n.staff)].append(n)
        for notes in by_staff.values():
            if any(n.rest for n in notes) or len({(n.onset, n.duration) for n in notes}) != 1:
                continue
            d = notes[0].duration
            if notes[0].onset != 0 or d * Fraction(3, 2) != length or any(n.dots for n in notes):
                continue
            for n in notes:
                n.duration = length
                n.dots = 1
            fixed += notes
    if not fixed:
        return []
    ms = sorted({n.measure for n in fixed})
    nums = ", ".join(score.measures[i].number for i in ms[:12])
    return [Issue("aviso", "ritmo", "Puntillo añadido",
                  f"En los compases {nums} un acorde solo ocupaba 2/3 del compás: le falta el puntillo (el motor no "
                  "lo vio). Ahora dura el compás entero.", measure=ms[0], note_ids=[n.id for n in fixed],
                  evidence="comprobado", code="puntillo")]


def repair_voice_alignment(score: Score) -> list[Issue]:
    """Una voz que se sale del compás porque el motor la empezó tarde: se adelanta lo que sobra si,
    con el nuevo ritmo, el orden de las notas en el tiempo sigue coincidiendo con su orden de izquierda
    a derecha en la imagen (comparando con todas las notas del compás)."""
    lengths = _nominal_lengths(score)
    issues: list[Issue] = []
    for m in score.measures:
        length = lengths[m.index]
        notes = [n for n in score.notes_in_measure(m.index) if not n.grace]
        if not notes:
            continue
        for key in sorted({(n.part, n.staff, n.voice) for n in notes}):
            voice = sorted((n for n in notes if (n.part, n.staff, n.voice) == key), key=lambda n: n.onset)
            end = max(n.end for n in voice)
            over = end - length
            if over <= 0 or over > length / 2:
                continue
            # sufijo que se puede adelantar sin pisar la nota anterior de la misma voz
            seg = None
            for k in range(len(voice)):
                prev_end = max((n.end for n in voice[:k]), default=Fraction(0))
                if voice[k].onset - over >= prev_end:
                    seg = voice[k:]
                    break
            if not seg:
                continue
            if not _x_order_ok(seg, [n for n in notes if n not in seg], -over):
                continue
            for n in seg:
                n.onset -= over
            issues.append(Issue(
                "aviso", "ritmo", f"Compás {m.number}: ritmo de una voz ajustado",
                f"Una voz se salía del compás {float(over):g} tiempo(s) de negra porque el motor la empezó tarde. Se "
                "adelantó, ya que así el orden de las notas coincide con su posición en la imagen. Escúchalo.",
                measure=m.index, note_ids=[n.id for n in seg if not n.rest], evidence="sospecha",
                code="voz_ajustada"))
    if issues:
        score.sort_notes()
    return issues


def _x_order_ok(seg: list[Note], others: list[Note], shift: Fraction) -> bool:
    """Con el desplazamiento, ¿cada nota del segmento queda en el mismo orden (tiempo ↔ x) que las demás?
    Exige al menos una comparación real y ninguna contradicción."""
    def agreement(sh: Fraction) -> tuple[int, int]:
        good = bad = 0
        for a in seg:
            if a.position is None or a.position.approximate:
                continue
            ta = a.onset + sh
            ax = a.position.x + a.position.w / 2
            for b in others:
                if b.position is None or b.position.approximate or b.position.page != a.position.page:
                    continue
                bx = b.position.x + b.position.w / 2
                tol = max(a.position.w, b.position.w) * 0.6
                if ta < b.onset:
                    ok = ax < bx + tol
                elif ta > b.onset:
                    ok = ax > bx - tol
                else:
                    ok = abs(ax - bx) < 2.5 * tol
                good, bad = (good + 1, bad) if ok else (good, bad + 1)
        return good, bad
    g1, b1 = agreement(shift)
    return g1 > 0 and b1 == 0


def repair_duration_by_spacing(score: Score) -> list[Issue]:
    """Una voz que se sale del compás por figuras leídas demasiado largas.

    En una partitura impresa, cuanto más dura una figura más espacio deja hasta la siguiente. Se usan dos
    criterios y solo se actúa si la explicación es única:

    1. Una sola figura deja claramente menos espacio que las demás (proporción lineal) y, acortándola en lo
       que sobra, su espacio pasa a parecerse al de las otras.
    2. Si no, varias figuras sospechosas (espacio ∝ duración^0,6, el modelo habitual de grabado) cuyos
       acortamientos suman exactamente lo que sobra; se elige la combinación con menos figuras.
    """
    from itertools import combinations, product

    from note_cascade.music_model.model import notation_for_duration
    lengths = _nominal_lengths(score)
    issues: list[Issue] = []
    for m in score.measures:
        length = lengths[m.index]
        notes = [n for n in score.notes_in_measure(m.index) if not n.grace]
        for key in sorted({(n.part, n.staff, n.voice) for n in notes}):
            voice = [n for n in notes if (n.part, n.staff, n.voice) == key]
            over = max(n.end for n in voice) - length
            if over <= 0 or over > length / 2:
                continue
            gaps = _figure_gaps(m, voice)
            if gaps is None or len(gaps) < 3:
                continue
            chosen = _single_figure(gaps, over) or _several_figures(gaps, over, combinations, product)
            if not chosen:
                continue
            changed_ids = []
            for a, cut, newdur, group in sorted(chosen, key=lambda x: -x[0]):
                t_name, dots, tup = notation_for_duration(newdur)
                for n in group:
                    n.duration, n.type_name, n.dots, n.tuplet = newdur, t_name, dots, tup
                    changed_ids.append(n.id)
                for n in voice:
                    if n.onset > a:
                        n.onset -= cut
            where = ", ".join(f"{float(a) + 1:g}" for a, *_ in sorted(chosen, key=lambda x: x[0]))
            issues.append(Issue(
                "aviso", "ritmo", f"Compás {m.number}: figura(s) acortada(s)",
                f"El compás se salía {float(over):g} tiempo(s) de negra. En el pentagrama {key[1]}, la(s) figura(s) "
                f"del tiempo {where} ocupan en la imagen el espacio de figuras más cortas: se acortaron y lo que "
                "sigue se adelantó. Escúchalo.", measure=m.index,
                note_ids=[i for i in changed_ids if not score.note_by_id(i).rest], evidence="sospecha",
                code="figura_espaciado"))
    if issues:
        score.sort_notes()
    return issues


def _figure_gaps(m, voice: list[Note]):
    """(espacio en la página, duración, inicio, figuras) de cada figura de la voz hasta la siguiente."""
    onsets = sorted({n.onset for n in voice})
    if len(onsets) < 3:
        return None
    xs = {}
    for t in onsets:
        pos = [n.position for n in voice if n.onset == t and n.position is not None and not n.position.approximate]
        if not pos:
            return None
        xs[t] = min(p.x for p in pos)
    # la última figura se mide hasta la barra de compás (su final escrito es el final del compás)
    mp = m.position
    last = onsets[-1]
    last_end = max(n.end for n in voice if n.onset == last)
    bounds = list(onsets)
    if mp is not None and not mp.approximate and last_end > last:
        xs[last_end] = mp.x + mp.w
        bounds.append(last_end)
    gaps = []
    for a, b in zip(bounds, bounds[1:]):
        group = [n for n in voice if n.onset == a]
        dur = b - a
        if any(n.duration != dur for n in group):
            continue  # figura que no llena el hueco hasta la siguiente: no se puede medir así
        gaps.append((xs[b] - xs[a], dur, a, group))
    return gaps


def _single_figure(gaps, over):
    from note_cascade.music_model.model import notation_for_duration
    ratios = [g / float(d) for g, d, _a, _gr in gaps]
    med = sorted(ratios)[len(ratios) // 2]
    if med <= 0:
        return None
    cands = []
    for (g, d, a, group), r in zip(gaps, ratios):
        if d <= over:
            continue
        new = d - over
        if notation_for_duration(new) is None:
            continue
        r2 = r * float(d) / float(new)
        if r < 0.7 * med and 0.7 * med <= r2 <= 1.45 * med:
            cands.append((a, over, new, group))
    return cands if len(cands) == 1 else None


def _several_figures(gaps, over, combinations, product):
    from note_cascade.music_model.model import notation_for_duration
    unit = sorted(g / float(d) ** 0.6 for g, d, _a, _gr in gaps)[len(gaps) // 2]
    if unit <= 0:
        return None
    cands = []
    for g, d, a, group in gaps:
        if g / (unit * float(d) ** 0.6) >= 0.75:
            continue
        spaced = (g / unit) ** (1 / 0.6)          # duración que corresponde al espacio que ocupa
        options = [o for o in (Fraction(d) / 2, Fraction(d) / 4, Fraction(d) * 3 / 4)
                   if o > 0 and notation_for_duration(o) is not None]
        if options:
            cands.append((a, d, group, spaced, options))
    if not cands or len(cands) > 8:
        return None
    best = None   # (número de figuras, desajuste, elección)
    tie = False
    for k in range(1, len(cands) + 1):
        for subset in combinations(cands, k):
            for choice in product(*[c[4] for c in subset]):
                if sum(c[1] - o for c, o in zip(subset, choice)) != over:
                    continue
                misfit = sum(abs(float(o) - c[3]) for c, o in zip(subset, choice))
                key = (k, misfit)
                if best is None or key < best[0]:
                    best, tie = (key, [(c[0], c[1] - o, o, c[2]) for c, o in zip(subset, choice)]), False
                elif best is not None and k == best[0][0] and abs(misfit - best[0][1]) < 1e-9:
                    tie = True
        if best is not None:
            break
    if best is None or tie:
        return None
    return best[1]

def repair_measure_time_signatures(score: Score) -> list[Issue]:
    """Compás en el que todos los pentagramas están completos (sin huecos) y coinciden en una duración
    distinta de la indicada: es un cambio de compás que el motor no leyó (p. ej. un 3/8 en medio de un
    12/8). Sin esto se oiría un silencio que no está escrito."""
    issues: list[Issue] = []
    for m in score.measures:
        if m.index == 0 or not m.time_sig:
            continue  # el primer compás puede ser una anacrusa
        nominal = Fraction(m.time_sig[0] * 4, m.time_sig[1])
        staves = sorted({(n.part, n.staff) for n in score.notes_in_measure(m.index)})
        if not staves:
            continue
        lengths = set()
        rests_only = []
        for part, staff in staves:
            notes = [n for n in score.notes_in_measure(m.index) if (n.part, n.staff) == (part, staff) and not n.grace]
            if len(notes) == 1 and notes[0].rest and notes[0].measure_rest:
                rests_only.append(notes[0])  # un silencio de compás completo se adapta a la duración real
                continue
            ends = {n.end for n in notes}
            if part != 0 or not _staff_complete(score, m.index, staff, max(ends)):
                lengths = set()
                break
            lengths.add(max(ends))
        if len(lengths) != 1:
            continue
        real = lengths.pop()
        if real == nominal or real <= 0:
            continue
        den = m.time_sig[1]
        num = real * den / 4
        if num.denominator != 1 or not 1 <= num <= 16:
            continue
        old = m.time_sig
        for r in rests_only:
            r.duration = real
        m.time_sig = (int(num), den)
        m.time_sig_change = True
        nxt = score.measures[m.index + 1] if m.index + 1 < len(score.measures) else None
        if nxt is not None and nxt.time_sig == m.time_sig:
            nxt.time_sig = old
            nxt.time_sig_change = True
        elif nxt is not None and nxt.time_sig == old:
            nxt.time_sig_change = True
        issues.append(Issue(
            "aviso", "estructura", f"Compás {m.number}: {int(num)}/{den}",
            f"Todos los pentagramas de este compás duran {float(real):g} tiempos de negra en lugar de "
            f"{float(nominal):g}: es un compás de {int(num)}/{den} que el motor no leyó. Se corrigió para que no "
            "suene un silencio que no está escrito.", measure=m.index, evidence="comprobado", code="cambio_compas"))
    return issues


def repair_bogus_endings(score: Score) -> list[Issue]:
    """Casillas de 1.ª/2.ª vez sin ninguna barra de repetición que las justifique: el motor tomó por
    casilla otro corchete (normalmente la línea «8va - - -┐»). Se quitan para que no se salten compases."""
    from note_cascade.music_model.timeline import _ending_ranges
    valid = {k for start, end, _ in _ending_ranges(score) for k in range(start, end + 1)}
    bogus = [m for m in score.measures if (m.ending_numbers or m.ending_type) and m.index not in valid]
    if not bogus:
        return []
    for m in bogus:
        m.ending_numbers = []
        m.ending_type = ""
    nums = ", ".join(m.number for m in bogus[:12])
    return [Issue("aviso", "estructura", "Casillas de repetición descartadas",
                  f"El motor marcó casillas de 1.ª/2.ª vez en los compases {nums}, pero no hay ninguna barra de "
                  "repetición que las acompañe (suele ser una línea «8va - - -» confundida con una casilla). Se "
                  "quitaron para que esos compases suenen.", measure=bogus[0].index, evidence="comprobado",
                  code="casillas_falsas")]


def arpeggios_from_secondary(primary: Score, secondary: Score, pairs, engine: str) -> list[Issue]:
    """Arpegio (línea ondulada delante del acorde) que solo vio el segundo motor.

    Se copia al acorde del motor principal que empieza en el mismo momento del mismo pentagrama y comparte
    al menos dos alturas con el acorde arpegiado del otro motor (es el mismo acorde). Solo cambia cómo se toca
    (de abajo arriba), no las notas; queda una nota informativa con la corrección para quitarlo."""
    from note_cascade.music_model.arpeggio import ARPEGGIO, arpeggiated_chords, chord_of, set_arpeggio
    to_primary = {j: i for i, j in pairs if i is not None and j is not None}
    issues: list[Issue] = []
    for chord in arpeggiated_chords(secondary):
        head = chord[0]
        i = to_primary.get(head.measure)
        if i is None:
            continue
        mine = [n for n in primary.notes_in_measure(i, include_rests=False)
                if n.part == 0 and n.staff == head.staff and n.onset == head.onset and not n.grace]
        if not mine or any(ARPEGGIO in n.articulations for n in mine):
            continue
        if len({n.midi for n in mine} & {n.midi for n in chord}) < 2:
            continue
        set_arpeggio(primary, [mine[0].id], True)
        notes = chord_of(primary, mine[0])
        names = ", ".join(n.name() for n in notes)
        issues.append(Issue(
            "info", "ritmo", f"Compás {primary.measures[i].number}: acorde arpegiado",
            f"{engine} vio la línea ondulada delante del acorde ({names}, pentagrama {head.staff}) que el motor "
            "principal no marcó. Se toca arpegiado: de la nota más grave a la más aguda, muy seguidas, y cada una "
            "se mantiene hasta el final del acorde.", measure=i, note_ids=[n.id for n in notes],
            evidence="comprobado", code="arpegio_motor",
            fix=Fix("Quitar el arpegio (tocar el acorde a la vez)", "remove_articulation",
                    {"note_ids": [n.id for n in notes], "articulation": ARPEGGIO})))
    return issues


def repair_fermatas_with_secondary(primary: Score, secondary: Score, pairs, engine: str) -> list[Issue]:
    """Calderón que solo vio el motor principal, cuando el segundo motor coincide con él en los demás.

    Un calderón falso alarga el compás al reproducir. Si el segundo motor encuentra los mismos calderones
    que el principal en al menos dos compases (es decir, sabe verlos) pero en ese compás no ve ninguno,
    se quita (con un aviso que permite volver a ponerlo)."""
    to_secondary = {i: j for i, j in pairs if i is not None and j is not None}

    def fermata_measures(score: Score) -> set[int]:
        return {n.measure for n in score.notes if "fermata" in n.articulations}

    mine = fermata_measures(primary)
    theirs = fermata_measures(secondary)
    agreed = {i for i in mine if to_secondary.get(i) in theirs}
    if len(agreed) < 2:
        return []
    issues = []
    for i in sorted(mine - agreed):
        if i not in to_secondary:
            continue
        notes = [n for n in primary.notes_in_measure(i) if "fermata" in n.articulations]
        for n in notes:
            n.articulations = [a for a in n.articulations if a != "fermata"]
        issues.append(Issue(
            "aviso", "ritmo", f"Compás {primary.measures[i].number}: calderón descartado",
            f"El motor principal leyó un calderón que {engine} no ve, aunque ambos coinciden en los demás "
            f"calderones de la pieza ({len(agreed)}). Se quitó para que el compás no se alargue al reproducir.",
            measure=i, note_ids=[n.id for n in notes], evidence="comprobado", code="calderon_descartado",
            fix=Fix("Volver a poner el calderón", "add_articulation",
                    {"note_ids": [n.id for n in notes], "articulation": "fermata"})))
    return issues
