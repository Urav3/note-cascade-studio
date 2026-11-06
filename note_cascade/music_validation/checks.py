"""Comprobaciones musicales sobre el modelo reconocido.

Cada comprobación produce avisos con:
* gravedad (error probable / revisar / información),
* tipo de evidencia: «comprobado» (incoherencia demostrable, p. ej. un compás
  cuyas figuras suman más que su indicación de compás) o «sospecha»
  (p. ej. un salto de octava aislado),
* y, cuando es posible, una corrección propuesta que el usuario acepta o
  rechaza. Nada se corrige en silencio.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
from typing import Iterable

from note_cascade.music_model import pitch as P
from note_cascade.music_model.hands import LEFT, RIGHT
from note_cascade.music_model.model import Note, Score, describe_duration
from note_cascade.music_validation.issues import Fix, Issue


def _mnum(score: Score, idx: int | None) -> str:
    if idx is None or idx >= len(score.measures) or idx < 0:
        return "?"
    return score.measures[idx].number or str(idx + 1)


def _beats(q: Fraction) -> str:
    v = float(q)
    return f"{v:g} tiempo{'s' if v != 1 else ''} de negra"


def _power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


# ---------------------------------------------------------------------------
# Ritmo
# ---------------------------------------------------------------------------

def inferred_measure_length(score: Score) -> Fraction | None:
    lengths = Counter()
    for m in score.measures:
        c = score.measure_content_duration(m.index)
        if c > 0:
            lengths[c] += 1
    if not lengths:
        return None
    return lengths.most_common(1)[0][0]


def check_time_signature_presence(score: Score) -> list[Issue]:
    if any(m.time_sig for m in score.measures) or not score.measures:
        return []
    mode = inferred_measure_length(score)
    issue = Issue("aviso", "ritmo", "No se reconoció la indicación de compás",
                  "El motor no encontró ninguna indicación de compás. No afecta a la duración de las notas "
                  "en el MIDI, pero impide comprobar si cada compás está completo y afecta a la unidad de pulso "
                  "de las indicaciones metronómicas.", measure=0, evidence="comprobado", code="sin_compas")
    if mode:
        num, den = _guess_time_signature(score, mode)
        issue.message += (f" La mayoría de los compases dura {_beats(mode)}, lo que corresponde a {num}/{den}.")
        issue.fix = Fix(f"Usar compás {num}/{den} en toda la pieza", "set_time_signature",
                        {"from_measure": 0, "num": num, "den": den})
    return [issue]


def _guess_time_signature(score: Score, length: Fraction) -> tuple[int, int]:
    eighths = sum(1 for n in score.notes if not n.rest and n.duration in (Fraction(1, 2), Fraction(3, 2), Fraction(3)))
    quarters = sum(1 for n in score.notes if not n.rest and n.duration in (Fraction(1), Fraction(2), Fraction(4)))
    in_eighths = length * 2
    if in_eighths.denominator == 1 and int(in_eighths) % 3 == 0 and int(in_eighths) >= 6 and eighths > quarters:
        return int(in_eighths), 8
    if length.denominator == 1:
        return int(length), 4
    if in_eighths.denominator == 1:
        return int(in_eighths), 8
    sixteenths = length * 4
    return int(sixteenths), 16


def check_measure_durations(score: Score) -> list[Issue]:
    issues: list[Issue] = []
    by_ms: dict[tuple[int, int, int], list[Note]] = defaultdict(list)
    for n in score.notes:
        if not n.grace:
            by_ms[(n.measure, n.part, n.staff)].append(n)
    inferred = None
    if not any(m.time_sig for m in score.measures):
        inferred = inferred_measure_length(score)
    last_index = len(score.measures) - 1
    first_len = None
    for m in score.measures:
        expected = m.expected_duration() or inferred
        if expected is None:
            continue
        for (mi, part, staff), notes in sorted(by_ms.items()):
            if mi != m.index:
                continue
            end = max(n.end for n in notes)
            voices = defaultdict(list)
            for n in notes:
                voices[n.voice].append(n)
            if end > expected:
                worst = max(voices.values(), key=lambda vs: max(x.end for x in vs))
                issues.append(Issue(
                    "error", "ritmo", f"Compás {m.number}: sobran {_beats(end - expected)}",
                    f"En el pentagrama {staff} las figuras llegan hasta {_beats(end)}, pero el compás "
                    f"{_ts(m, inferred)} solo tiene {_beats(expected)}. Probablemente se leyó mal una figura, un "
                    "puntillo, un tresillo o se mezclaron dos voces.",
                    measure=m.index, note_ids=[x.id for x in worst if x.end > expected],
                    evidence="comprobado", code="compas_largo"))
            elif end < expected:
                if m.index == 0:
                    first_len = end
                    continue
                if m.index == last_index and first_len is not None and first_len + end == expected:
                    continue
                if m.implicit:
                    continue
                issues.append(Issue(
                    "aviso", "ritmo", f"Compás {m.number}: faltan {_beats(expected - end)}",
                    f"En el pentagrama {staff} las figuras y silencios suman {_beats(end)}, pero el compás "
                    f"{_ts(m, inferred)} necesita {_beats(expected)}. Puede faltar una nota o un silencio; en el "
                    "MIDI se respeta la duración del compás.",
                    measure=m.index, evidence="comprobado", code="compas_corto"))
    if first_len is not None and score.measures:
        expected0 = score.measures[0].expected_duration() or inferred
        last_len = score.measure_content_duration(last_index)
        if expected0 and first_len + last_len == expected0:
            issues.append(Issue("info", "ritmo", "Anacrusa detectada",
                                f"El primer compás dura {_beats(first_len)} y el último lo completa: es una anacrusa "
                                "intencionada, no un error.", measure=0, evidence="comprobado", code="anacrusa"))
        elif expected0 and first_len < expected0:
            issues.append(Issue("info", "ritmo", "Primer compás incompleto (posible anacrusa)",
                                f"El primer compás dura {_beats(first_len)} de {_beats(expected0)}. Se interpreta como "
                                "anacrusa.", measure=0, evidence="sospecha", code="anacrusa_posible"))
    return issues


def _ts(m, inferred) -> str:
    if m.time_sig:
        return f"de {m.time_sig[0]}/{m.time_sig[1]}"
    return "(compás deducido)" if inferred else ""


def check_durations(score: Score) -> list[Issue]:
    issues = []
    bad = [n for n in score.notes if not n.grace and n.duration <= 0]
    if bad:
        issues.append(Issue("error", "ritmo", f"{len(bad)} figuras sin duración",
                            "Hay notas o silencios con duración nula o negativa (no son notas de adorno).",
                            measure=bad[0].measure, note_ids=[n.id for n in bad], evidence="comprobado",
                            code="duracion_nula",
                            fix=Fix("Eliminar esas figuras", "delete_notes", {"note_ids": [n.id for n in bad]})))
    for m in score.measures:
        exp = m.expected_duration()
        if not exp:
            continue
        for n in score.notes_in_measure(m.index):
            if n.duration > exp * 4:
                issues.append(Issue("error", "ritmo", f"Compás {m.number}: duración imposible",
                                    f"Una figura dura {_beats(n.duration)}, más de cuatro compases.",
                                    measure=m.index, note_ids=[n.id], evidence="comprobado", code="duracion_absurda"))
    return issues


def check_tuplets(score: Score) -> list[Issue]:
    issues = []
    groups: dict[tuple, list[Note]] = defaultdict(list)
    for n in score.notes:
        if n.tuplet and not n.grace:
            groups[(n.measure, n.part, n.staff, n.voice, n.tuplet)].append(n)
    for (mi, part, staff, voice, tup), notes in groups.items():
        onsets = {}
        for n in notes:
            onsets.setdefault(n.onset, n)
        total = sum((n.duration for n in onsets.values()), Fraction(0))
        if not _power_of_two(total.denominator):
            issues.append(Issue("aviso", "ritmo", f"Compás {_mnum(score, mi)}: grupo irregular incompleto",
                                f"Las figuras de {tup[0]}:{tup[1]} del pentagrama {staff} no forman grupos completos "
                                f"(suman {float(total):.3f} tiempos). Puede faltar una nota del tresillo o sobrar una.",
                                measure=mi, note_ids=[n.id for n in notes], evidence="comprobado", code="tresillo"))
    return issues


def check_ties(score: Score) -> list[Issue]:
    issues = []
    pitched = sorted([n for n in score.notes if not n.rest and not n.grace],
                     key=lambda n: (n.part, n.measure, n.onset))
    by_part_midi: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for n in pitched:
        by_part_midi[(n.part, n.midi)].append(n)
    from note_cascade.music_model.timeline import measure_lengths
    lengths = measure_lengths(score)
    starts = [Fraction(0)]
    for L in lengths:
        starts.append(starts[-1] + L)

    def abs_on(n: Note) -> Fraction:
        return starts[n.measure] + n.onset if n.measure < len(starts) else n.onset

    for n in pitched:
        if n.tie_start:
            end = abs_on(n) + n.duration
            nxt = [c for c in by_part_midi[(n.part, n.midi)] if c is not n and abs(abs_on(c) - end) <= Fraction(1, 32)]
            if not nxt:
                same_time_other_pitch = [c for c in pitched if c.part == n.part and c.staff == n.staff and
                                         c.tie_stop and abs(abs_on(c) - end) <= Fraction(1, 32)]
                if same_time_other_pitch:
                    issues.append(Issue("aviso", "ritmo", f"Compás {_mnum(score, n.measure)}: ligadura entre notas distintas",
                                        f"La nota {n.name()} está ligada a una nota de otra altura: probablemente es una "
                                        "ligadura de expresión leída como de prolongación. No debe alargar la nota.",
                                        measure=n.measure, note_ids=[n.id] + [c.id for c in same_time_other_pitch],
                                        evidence="comprobado", code="ligadura_distinta",
                                        fix=Fix("Quitar la ligadura de prolongación", "remove_tie",
                                                {"note_ids": [n.id] + [c.id for c in same_time_other_pitch]})))
                else:
                    issues.append(Issue("aviso", "ritmo", f"Compás {_mnum(score, n.measure)}: ligadura sin continuación",
                                        f"La nota {n.name()} tiene una ligadura de prolongación pero no hay una nota igual "
                                        "a continuación. En el MIDI suena solo su duración escrita.",
                                        measure=n.measure, note_ids=[n.id], evidence="comprobado", code="ligadura_suelta",
                                        fix=Fix("Quitar la ligadura", "remove_tie", {"note_ids": [n.id]})))
    return issues


def check_voice_overlaps(score: Score) -> list[Issue]:
    issues = []
    groups: dict[tuple, list[Note]] = defaultdict(list)
    for n in score.notes:
        if not n.grace:
            groups[(n.measure, n.part, n.staff, n.voice)].append(n)
    for (mi, part, staff, voice), notes in groups.items():
        onsets = sorted({n.onset for n in notes})
        by_onset = defaultdict(list)
        for n in notes:
            by_onset[n.onset].append(n)
        for a, b in zip(onsets, onsets[1:]):
            a_end = min(x.end for x in by_onset[a])
            if a_end > b:
                issues.append(Issue("aviso", "ritmo", f"Compás {_mnum(score, mi)}: voces superpuestas",
                                    f"En la voz {voice} del pentagrama {staff} una figura empieza antes de que termine la "
                                    "anterior. Suele indicar dos voces mezcladas; el sonido no cambia, pero la partitura "
                                    "exportada puede verse rara.", measure=mi,
                                    note_ids=[x.id for x in by_onset[a] + by_onset[b]], evidence="comprobado",
                                    code="voces_superpuestas"))
                break
    return issues


# ---------------------------------------------------------------------------
# Alturas
# ---------------------------------------------------------------------------

def check_range(score: Score, low: int, high: int) -> list[Issue]:
    bad = [n for n in score.notes if not n.rest and not (low <= n.midi <= high)]
    if not bad:
        return []
    by_measure = defaultdict(list)
    for n in bad:
        by_measure[n.measure].append(n)
    issues = []
    for mi, notes in sorted(by_measure.items()):
        names = ", ".join(sorted({n.name() for n in notes}))
        issues.append(Issue("aviso", "altura", f"Compás {_mnum(score, mi)}: nota fuera del rango",
                            f"{names} está fuera del rango configurado ({P.midi_name(low)}–{P.midi_name(high)}). "
                            "No se elimina: compruébalo (puede ser una línea adicional mal contada).",
                            measure=mi, note_ids=[n.id for n in notes], evidence="comprobado", code="rango"))
    return issues


def check_octave_leaps(score: Score) -> list[Issue]:
    issues = []
    lines: dict[tuple, list[Note]] = defaultdict(list)
    for n in score.notes:
        if not n.rest and not n.grace:
            lines[(n.part, n.staff, n.voice)].append(n)
    for key, notes in lines.items():
        notes.sort(key=lambda n: (n.measure, n.onset, -n.midi))
        # una nota por instante (la más aguda en la voz superior, la más grave en la inferior)
        seq: list[Note] = []
        last_key = None
        for n in notes:
            k = (n.measure, n.onset)
            if k != last_key:
                seq.append(n)
                last_key = k
        for k, (a, b, c) in enumerate(zip(seq, seq[1:], seq[2:]), start=1):
            if abs(b.midi - a.midi) >= 12 and abs(c.midi - b.midi) >= 12 and abs(c.midi - a.midi) <= 5 \
                    and (b.midi - a.midi) * (c.midi - b.midi) < 0:
                window = seq[max(0, k - 4):k + 5]
                # Patrones de octavas alternas (bajos de vals, octavas quebradas) son intencionados:
                # la nota «aislada» se repite en la vecindad.
                if sum(1 for x in window if abs(x.midi - b.midi) <= 2) > 1:
                    continue
                delta = 12 if a.midi > b.midi else -12
                chord = [x.id for x in notes if x.measure == b.measure and x.onset == b.onset]
                issues.append(Issue(
                    "aviso", "altura", f"Compás {_mnum(score, b.measure)}: posible error de octava",
                    f"{b.name()} salta más de una octava respecto de las notas vecinas ({a.name()} y {c.name()}) y "
                    "vuelve enseguida. Suele deberse a líneas adicionales mal contadas.",
                    measure=b.measure, note_ids=chord, evidence="sospecha", code="octava",
                    fix=Fix(f"Mover {'una octava arriba' if delta > 0 else 'una octava abajo'}", "transpose_notes",
                            {"note_ids": chord, "semitones": delta})))
    return issues


def check_accidentals(score: Score) -> list[Issue]:
    """Coherencia entre altura, armadura y alteraciones impresas dentro del compás."""
    if not any(n.accidental_shown for n in score.notes if not n.rest):
        return []  # el motor no informa de las alteraciones impresas: no hay evidencia
    issues = []
    groups: dict[tuple, list[Note]] = defaultdict(list)
    for n in score.notes:
        if not n.rest and not n.grace:
            groups[(n.measure, n.part, n.staff)].append(n)
    for (mi, part, staff), notes in sorted(groups.items()):
        key_alt = P.key_alterations(score.key_at(part, mi, staff))
        state: dict[tuple[str, int], int] = {}
        for n in sorted(notes, key=lambda x: (x.onset, x.midi)):
            k = (n.step, n.octave)
            expected = state.get(k, key_alt[n.step])
            if n.accidental_shown:
                acc_alter = P.ACCIDENTAL_TO_ALTER.get(n.accidental_shown)
                if acc_alter is not None and acc_alter != n.alter:
                    issues.append(Issue("error", "altura", f"Compás {_mnum(score, mi)}: alteración contradictoria",
                                        f"{n.name()} tiene escrita una alteración «{n.accidental_shown}» que no "
                                        "corresponde a su altura.", measure=mi, note_ids=[n.id], evidence="comprobado",
                                        code="alteracion_contradictoria",
                                        fix=Fix("Aplicar la alteración escrita", "set_alter",
                                                {"note_id": n.id, "alter": acc_alter})))
                state[k] = n.alter
            elif n.alter != expected and not n.tie_stop:
                exp_name = P.note_name(n.step, expected, n.octave)
                issues.append(Issue(
                    "aviso", "altura", f"Compás {_mnum(score, mi)}: alteración sin justificar",
                    f"{n.name()} no tiene alteración escrita, pero por la armadura y las alteraciones anteriores del "
                    f"compás debería ser {exp_name}. La armadura no se usa para cambiar notas con alteración escrita; "
                    "aquí no la hay, por eso se propone corregir.",
                    measure=mi, note_ids=[n.id], evidence="comprobado", code="alteracion_sin_justificar",
                    fix=Fix(f"Cambiar a {exp_name}", "set_alter", {"note_id": n.id, "alter": expected})))
                state[k] = n.alter
    return issues


def check_key_consistency(score: Score) -> list[Issue]:
    issues = []
    for pidx, part in enumerate(score.parts):
        if part.staves < 2:
            continue
        for m in score.measures:
            keys = {score.key_at(pidx, m.index, s) for s in range(1, part.staves + 1)}
            if len(keys) > 1:
                first = score.key_at(pidx, m.index, 1)
                issues.append(Issue("error", "altura", f"Compás {m.number}: armaduras distintas entre manos",
                                    "Los pentagramas de una misma parte tienen armaduras diferentes "
                                    f"({', '.join(P.key_signature_text(k) for k in sorted(keys))}).",
                                    measure=m.index, evidence="comprobado", code="armadura_manos",
                                    fix=Fix(f"Usar {P.key_signature_text(first)} en ambos pentagramas", "set_key",
                                            {"part": pidx, "from_measure": m.index, "fifths": first})))
                break
    # Cambios de armadura aislados (aparecen en un sistema y desaparecen en el siguiente)
    for pidx, _part in enumerate(score.parts):
        changes = sorted({k.measure for k in score.keys if k.part == pidx and k.measure > 0})
        for mi in changes:
            before = score.key_at(pidx, mi - 1)
            now = score.key_at(pidx, mi)
            if before == now:
                continue
            nxt = [c for c in changes if c > mi]
            if nxt:
                after = score.key_at(pidx, nxt[0])
                span = nxt[0] - mi
                if after == before and span <= 6 and score.measures[mi].new_system:
                    issues.append(Issue(
                        "aviso", "altura", f"Compás {_mnum(score, mi)}: cambio de armadura dudoso",
                        f"La armadura cambia de {P.key_signature_text(before)} a {P.key_signature_text(now)} al comienzo "
                        f"de un sistema y vuelve a la anterior {span} compases después. Es típico de un error de lectura "
                        "de la armadura en un sistema.", measure=mi, measure_end=nxt[0] - 1, evidence="sospecha",
                        code="armadura_aislada",
                        fix=Fix(f"Mantener {P.key_signature_text(before)}", "set_key",
                                {"part": pidx, "from_measure": mi, "to_measure": nxt[0] - 1, "fifths": before})))
    return issues


def check_clefs(score: Score) -> list[Issue]:
    issues = []
    for pidx, part in enumerate(score.parts):
        for staff in range(1, part.staves + 1):
            notes = [n for n in score.notes if n.part == pidx and n.staff == staff and not n.rest]
            if len(notes) < 12:
                continue
            clef = score.clef_at(pidx, staff, 0)
            if clef is None:
                continue
            high = sum(1 for n in notes if n.midi >= 74) / len(notes)
            low = sum(1 for n in notes if n.midi <= 45) / len(notes)
            if clef.sign == "F" and high > 0.7 and not any(c.part == pidx and c.staff == staff and c.sign == "G"
                                                          for c in score.clefs):
                issues.append(Issue("aviso", "altura", f"Pentagrama {staff}: ¿clave de fa correcta?",
                                    "Casi todas las notas de este pentagrama quedan muy agudas para la clave de fa. "
                                    "Puede que la clave sea de sol.", measure=0, evidence="sospecha", code="clave"))
            if clef.sign == "G" and low > 0.7 and not any(c.part == pidx and c.staff == staff and c.sign == "F"
                                                         for c in score.clefs):
                issues.append(Issue("aviso", "altura", f"Pentagrama {staff}: ¿clave de sol correcta?",
                                    "Casi todas las notas de este pentagrama quedan muy graves para la clave de sol. "
                                    "Puede que la clave sea de fa.", measure=0, evidence="sospecha", code="clave"))
    return issues


def check_duplicates(score: Score) -> list[Issue]:
    issues = []
    seen: dict[tuple, Note] = {}
    dups: dict[int, list[tuple[Note, Note]]] = defaultdict(list)
    for n in sorted(score.notes, key=lambda x: (x.measure, x.onset, x.voice)):
        if n.rest or n.grace:
            continue
        k = (n.measure, n.part, n.staff, n.onset, n.midi)
        if k in seen and seen[k].duration == n.duration:
            dups[n.measure].append((seen[k], n))
        else:
            seen[k] = n
    for mi, pairs in dups.items():
        remove = []
        for a, b in pairs:
            ca = a.confidence if a.confidence is not None else 1.0
            cb = b.confidence if b.confidence is not None else 1.0
            remove.append(b.id if cb <= ca else a.id)
        issues.append(Issue("aviso", "altura", f"Compás {_mnum(score, mi)}: notas duplicadas",
                            f"Hay {len(pairs)} nota(s) repetidas en el mismo instante, pentagrama y altura (en voces "
                            "distintas). En el MIDI suenan una sola vez; puede tratarse de un unísono escrito o de una "
                            "lectura doble.", measure=mi, note_ids=[x.id for p in pairs for x in p],
                            evidence="comprobado", code="duplicada",
                            fix=Fix("Eliminar la copia duplicada", "delete_notes", {"note_ids": remove})))
    return issues


def check_hand_spans(score: Score) -> list[Issue]:
    issues = []
    for pidx, part in enumerate(score.parts):
        if not part.is_piano:
            continue
        groups: dict[tuple, list[Note]] = defaultdict(list)
        for n in score.notes:
            if n.part == pidx and not n.rest and not n.grace and n.hand:
                groups[(n.measure, n.onset, n.hand)].append(n)
        reported = set()
        for (mi, onset, hand), notes in sorted(groups.items()):
            if len(notes) < 2 or mi in reported:
                continue
            span = max(n.midi for n in notes) - min(n.midi for n in notes)
            if span > 16:
                reported.add(mi)
                issues.append(Issue("info", "piano", f"Compás {_mnum(score, mi)}: acorde muy abierto para una mano",
                                    f"La {'mano derecha' if hand == RIGHT else 'mano izquierda'} tendría que tocar a la vez "
                                    f"notas separadas {span} semitonos. Puede que parte del acorde sea de la otra mano o "
                                    "que haya un error de octava (también puede ser un arpegio).",
                                    measure=mi, note_ids=[n.id for n in notes], evidence="sospecha", code="mano_abierta"))
    return issues


def check_hand_unisons(score: Score) -> list[Issue]:
    """La misma tecla escrita a la vez en las dos manos: en el piano suena una sola vez."""
    issues = []
    groups: dict[tuple, list[Note]] = defaultdict(list)
    for n in score.notes:
        if not n.rest and not n.grace and n.hand in (RIGHT, LEFT):
            groups[(n.part, n.measure, n.onset, n.midi)].append(n)
    by_measure: dict[int, list[Note]] = defaultdict(list)
    for (part, mi, onset, midi), notes in groups.items():
        if {n.hand for n in notes} == {RIGHT, LEFT}:
            by_measure[mi].extend(notes)
    for mi, notes in sorted(by_measure.items()):
        names = ", ".join(sorted({n.name() for n in notes}))
        issues.append(Issue("aviso", "piano", f"Compás {_mnum(score, mi)}: misma tecla en las dos manos",
                            f"{names} aparece a la vez en la mano derecha y en la izquierda. Al reproducir se toca "
                            "una sola vez. Puede estar así en la partitura (unísono entre manos) o ser una nota "
                            "asignada a la mano equivocada: compruébalo y cambia la mano si hace falta.",
                            measure=mi, note_ids=[n.id for n in notes], evidence="comprobado", code="unisono_manos"))
    return issues


# ---------------------------------------------------------------------------
# Estructura
# ---------------------------------------------------------------------------

def check_empty_measures(score: Score) -> list[Issue]:
    issues = []
    has = Counter(n.measure for n in score.notes)
    for m in score.measures[1:-1]:
        if has[m.index] == 0:
            issues.append(Issue("aviso", "estructura", f"Compás {m.number}: vacío",
                                "El compás no contiene notas ni silencios: puede faltar música reconocida.",
                                measure=m.index, evidence="comprobado", code="compas_vacio"))
    return issues


def check_duplicate_systems(score: Score) -> list[Issue]:
    """Detecta bloques de compases idénticos repetidos en un salto de sistema/página."""
    sig = []
    for m in score.measures:
        notes = sorted((n.part, n.staff, n.onset, n.duration, n.midi, n.rest) for n in score.notes_in_measure(m.index))
        sig.append(tuple(notes))
    issues = []
    n = len(sig)
    for i, m in enumerate(score.measures):
        if not (m.new_system or m.new_page) or i == 0:
            continue
        for length in range(min(8, i, n - i), 1, -1):
            if sig[i - length:i] == sig[i:i + length] and any(sig[i:i + length]):
                if any(score.measures[k].repeat_end or score.measures[k].repeat_start
                       for k in range(i - length, i + length)):
                    break
                issues.append(Issue("aviso", "estructura", f"Compases {_mnum(score, i)}–{_mnum(score, i + length - 1)}: "
                                    "posible sistema duplicado",
                                    f"Los {length} compases que empiezan en el compás {_mnum(score, i)} son idénticos a los "
                                    f"{length} anteriores, justo en un cambio de sistema. Puede ser música repetida a "
                                    "propósito o el mismo sistema procesado dos veces.",
                                    measure=i, measure_end=i + length - 1, evidence="sospecha", code="sistema_duplicado",
                                    fix=Fix("Eliminar los compases duplicados", "delete_measures",
                                            {"from_measure": i, "to_measure": i + length - 1})))
                break
    return issues


def check_repeats(score: Score, perf_notes: Iterable[str] = ()) -> list[Issue]:
    issues = []
    for text in perf_notes:
        if "unísono" in text:
            continue  # se informa en check_hand_unisons
        if any(k in text for k in ("D.S", "Coda", "repetic", "segno", "ciclo")):
            issues.append(Issue("aviso", "estructura", "Repetición o salto ambiguo", text, evidence="comprobado",
                                code="repeticion_ambigua"))
        elif "sin tempo de destino" in text:
            issues.append(Issue("info", "estructura", "Cambio de tempo sin destino", text, evidence="comprobado",
                                code="tempo_sin_destino"))
        elif "se sueltan justo antes" in text:
            issues.append(Issue("info", "piano", "Notas que se vuelven a tocar", text, evidence="comprobado",
                                code="rearticulacion"))
        else:
            issues.append(Issue("info", "piano", "Interpretación", text, evidence="comprobado",
                                code="interpretacion"))
    starts = [m.index for m in score.measures if m.repeat_start]
    ends = [m.index for m in score.measures if m.repeat_end]
    if ends and not starts:
        issues.append(Issue("info", "estructura", "Repetición desde el principio",
                            f"Hay una barra de repetición final en el compás {_mnum(score, ends[0])} sin barra inicial: "
                            "se repite desde el comienzo de la pieza.", measure=ends[0], evidence="comprobado",
                            code="repeticion_inicio"))
    endings = [m for m in score.measures if m.ending_numbers]
    if endings and not ends:
        issues.append(Issue("aviso", "estructura", "Casillas de repetición sin barra de repetición",
                            "Se reconocieron casillas de 1.ª/2.ª vez pero ninguna barra de repetición. Revisa el "
                            "compás indicado.", measure=endings[0].index, evidence="comprobado", code="casillas"))
    return issues


def check_tempo(score: Score, default_bpm: float) -> list[Issue]:
    issues = []
    with_bpm = [t for t in score.tempos if t.bpm_quarter]
    if not with_bpm:
        words = [t for t in score.tempos if t.text]
        if words:
            from note_cascade.music_model import terms
            tw = terms.tempo_word_bpm(words[0].text)
            msg = (f"La partitura indica «{words[0].text}» pero sin valor metronómico. Se usará el tempo "
                   f"predeterminado de {default_bpm:g} negras por minuto, que puedes cambiar antes de exportar.")
            fix = None
            if tw:
                msg += f" Un valor habitual para «{tw[0]}» es {tw[1]:g}."
                fix = Fix(f"Usar {tw[1]:g} negras por minuto (valor típico de «{tw[0]}»)", "set_tempo",
                          {"measure": words[0].measure, "onset": "0", "bpm": tw[1], "text": words[0].text})
            issues.append(Issue("info", "estructura", "Tempo sin valor numérico", msg, measure=words[0].measure,
                                evidence="comprobado", code="tempo_palabra", fix=fix))
        else:
            issues.append(Issue("info", "estructura", "Sin indicación de tempo",
                                f"No se reconoció ninguna indicación de tempo. Se usará {default_bpm:g} negras por "
                                "minuto (ajustable en Exportar / Opciones).", evidence="comprobado", code="sin_tempo"))
    return issues


def check_hands(score: Score) -> list[Issue]:
    issues = []
    for pidx, part in enumerate(score.parts):
        if part.is_piano and part.staves == 1:
            issues.append(Issue("info", "piano", "Piano en un solo pentagrama",
                                "La parte de piano tiene un solo pentagrama: todas las notas se asignan a la mano "
                                "derecha. Puedes cambiar la mano de cualquier nota en la revisión.",
                                evidence="comprobado", code="piano_un_pentagrama"))
        if part.is_piano and part.staves > 2:
            issues.append(Issue("info", "piano", f"Piano con {part.staves} pentagramas",
                                "El pentagrama superior se asigna a la mano derecha y los demás a la izquierda; "
                                "revisa la asignación si no es correcta.", evidence="comprobado", code="piano_varios"))
    return issues
