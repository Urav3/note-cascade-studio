"""Aplicación de correcciones (propuestas por la validación o hechas en el editor)."""

from __future__ import annotations

from fractions import Fraction

from note_cascade.music_model import pitch as P
from note_cascade.music_model.hands import assign_hands
from note_cascade.music_model.model import ClefChange, KeyChange, Note, Score, TempoMark, frac, log_pitch_change, new_id
from note_cascade.music_validation.issues import Fix


def respell(n: Note, score: Score, prefer: int | None = None, key_first: bool = False) -> None:
    fifths = score.key_at(n.part, n.measure, n.staff)
    part = score.parts[n.part] if n.part < len(score.parts) else None
    written = n.midi - ((part.transpose_chromatic + 12 * part.transpose_octave) if part else 0)
    n.step, n.alter, n.octave = P.spell_midi(written, fifths, prefer, key_first)


def set_midi(n: Note, score: Score, midi: int, prefer: int | None = None, reason: str = "corrección") -> None:
    old = n.midi
    n.midi = int(max(0, min(127, midi)))
    log_pitch_change(n, old, reason)
    respell(n, score, prefer)
    n.accidental_shown = None
    n.user_modified = True


def apply_fix(score: Score, fix: Fix) -> str:
    a, p = fix.action, fix.params
    if a == "set_time_signature":
        start = int(p.get("from_measure", 0))
        end = int(p.get("to_measure", len(score.measures) - 1))
        ts = (int(p["num"]), int(p["den"]))
        for m in score.measures[start:end + 1]:
            m.time_sig = ts
        score.measures[start].time_sig_change = True
        # Ajuste de la unidad de pulso de las marcas metronómicas sin unidad explícita
        for t in score.tempos:
            if t.source == "texto" and t.per_minute and t.beat_unit == "quarter" and ts[1] == 8 and ts[0] % 3 == 0:
                t.beat_unit, t.beat_dots = "quarter", 1
                t.bpm_quarter = t.per_minute * 1.5
        return f"Compás {ts[0]}/{ts[1]} aplicado."
    if a == "set_tempo":
        m = int(p.get("measure", 0))
        onset = frac(p.get("onset", "0"))
        bpm = float(p["bpm"])
        # la figura escrita y el número cambian con el tempo: si no, el MusicXML diría «♩. = 65» y sonaría ♩ = 65
        unit = p.get("unit", "quarter")
        dots = int(p.get("dots", 0))
        per_minute = float(p.get("per_minute", bpm))
        existing = [t for t in score.tempos if t.measure == m and t.onset == onset]
        if existing:
            for t in existing:
                t.bpm_quarter = bpm
                t.beat_unit, t.beat_dots, t.per_minute = unit, dots, per_minute
                t.source = "usuario"
        else:
            score.tempos.append(TempoMark(m, onset, bpm, text=p.get("text", ""), beat_unit=unit, beat_dots=dots,
                                          per_minute=per_minute, source="usuario"))
        return f"Tempo de {bpm:g} negras por minuto aplicado."
    if a == "transpose_notes":
        ids = set(p["note_ids"])
        semis = int(p["semitones"])
        for n in score.notes:
            if n.id in ids and not n.rest:
                set_midi(n, score, n.midi + semis, prefer=1 if n.alter > 0 else (-1 if n.alter < 0 else None))
        return f"{len(ids)} nota(s) movidas {semis:+d} semitonos."
    if a == "set_alter":
        n = score.note_by_id(p["note_id"])
        if n is None:
            return "La nota ya no existe."
        new_alter = int(p["alter"])
        old = n.midi
        n.midi += new_alter - n.alter
        n.alter = new_alter
        log_pitch_change(n, old, "corrección de alteración")
        n.user_modified = True
        return f"Alteración corregida: {n.name()}."
    if a == "set_pitch":
        n = next((x for x in score.notes if x.id == p["note_id"]), None)
        if n is None:
            return "La nota ya no existe."
        set_midi(n, score, int(p["midi"]))
        return f"Altura corregida: {n.name()}."
    if a == "restore_notes":
        have = {n.id for n in score.notes}
        back = [Note.from_dict(d) for d in p.get("notes", []) if d.get("id") not in have]
        score.notes.extend(back)
        score.sort_notes()
        return f"{len(back)} nota(s) repuestas."
    if a == "delete_notes":
        ids = set(p["note_ids"])
        before = len(score.notes)
        score.notes = [n for n in score.notes if n.id not in ids]
        return f"{before - len(score.notes)} figura(s) eliminadas."
    if a == "add_articulation":
        ids = set(p["note_ids"])
        for n in score.notes:
            if n.id in ids and p["articulation"] not in n.articulations:
                n.articulations.append(p["articulation"])
        return "Signo restaurado."
    if a == "remove_articulation":
        ids = set(p["note_ids"])
        for n in score.notes:
            if n.id in ids and p["articulation"] in n.articulations:
                n.articulations = [x for x in n.articulations if x != p["articulation"]]
        return "Signo quitado."
    if a == "remove_tie":
        ids = set(p["note_ids"])
        for n in score.notes:
            if n.id in ids:
                n.tie_start = n.tie_stop = False
                n.user_modified = True
        return "Ligadura eliminada."
    if a == "set_key":
        return set_key(score, int(p.get("part", 0)), int(p.get("from_measure", 0)),
                       p.get("to_measure"), int(p["fifths"]), p.get("staff"))
    if a == "set_clef":
        return set_clef(score, int(p.get("part", 0)), int(p["staff"]), int(p.get("measure", 0)),
                        p["sign"], int(p["line"]))
    if a == "replace_measure":
        mi = int(p["measure"])
        score.notes = [n for n in score.notes if n.measure != mi]
        for d in p.get("notes", []):
            n = Note.from_dict(d)
            n.id = new_id()
            n.measure = mi
            n.position = None
            n.source = p.get("engine", n.source)
            n.user_modified = True
            if not p.get("keep_hands"):
                n.hand = None
            score.notes.append(n)
        assign_hands(score)
        score.sort_notes()
        src = p.get('engine', 'otro motor')
        if src == "tu corrección":
            return f"Compás {score.measures[mi].number}: aplicada la misma corrección."
        return f"Compás {score.measures[mi].number} sustituido por la versión de {src}."
    if a == "set_hand":
        ids = set(p["note_ids"])
        for n in score.notes:
            if n.id in ids:
                n.hand = p["hand"]
                n.user_modified = True
        return "Mano cambiada."
    if a == "octave_shift":
        part, staff = int(p["part"]), int(p["staff"])
        m0, m1 = int(p["from_measure"]), int(p["to_measure"])
        semis = int(p["semitones"])
        only = set(p["note_ids"]) if p.get("note_ids") else None
        count = 0
        for n in score.notes:
            if only is not None and n.id not in only:
                continue
            if n.part == part and n.staff == staff and m0 <= n.measure <= m1 and not n.rest:
                set_midi(n, score, n.midi + semis, reason=f"indicación de octava {semis:+d}")
                count += 1
        if p.get("remove_directions"):
            score.directions = [d for d in score.directions if not (
                d.kind == "octave_shift" and d.source == "texto" and d.part == part and d.staff == staff
                and m0 <= d.measure <= m1)]
        return f"{count} nota(s) desplazadas {semis:+d} semitonos (indicación de octava)."
    if a == "delete_measures":
        m0, m1 = int(p["from_measure"]), int(p["to_measure"])
        return delete_measures(score, m0, m1)
    raise ValueError(f"Acción de corrección desconocida: {a}")


def set_key(score: Score, part: int, from_measure: int, to_measure, fifths: int, staff=None) -> str:
    """Cambia la armadura y reinterpreta las notas sin alteración escrita."""
    end = int(to_measure) if to_measure is not None else len(score.measures) - 1
    old_by_measure = {mi: score.key_at(part, mi, staff) for mi in range(from_measure, end + 1)}
    after = score.key_at(part, end + 1) if end + 1 < len(score.measures) else None
    score.keys = [k for k in score.keys if not (k.part == part and from_measure <= k.measure <= end and
                                                (staff is None or k.staff in (None, staff)))]
    score.keys.append(KeyChange(part, from_measure, fifths, staff=staff))
    if after is not None and end + 1 < len(score.measures) and after != fifths and \
            not any(k.part == part and k.measure == end + 1 for k in score.keys):
        score.keys.append(KeyChange(part, end + 1, after, staff=staff))
    new_alt = P.key_alterations(fifths)
    changed = 0
    for n in score.notes:
        if n.part != part or n.rest or not (from_measure <= n.measure <= end):
            continue
        if staff is not None and n.staff != staff:
            continue
        if n.accidental_shown:
            continue
        old_alt = P.key_alterations(old_by_measure[n.measure])
        if n.alter == old_alt[n.step] and new_alt[n.step] != n.alter:
            old = n.midi
            n.midi += new_alt[n.step] - n.alter
            log_pitch_change(n, old, "cambio de armadura")
            n.alter = new_alt[n.step]
            changed += 1
    return f"Armadura cambiada a {P.key_signature_text(fifths)}; {changed} nota(s) reinterpretadas."


def set_clef(score: Score, part: int, staff: int, measure: int, sign: str, line: int) -> str:
    """Cambia la clave desde ``measure`` y reinterpreta las alturas por su posición en el pentagrama."""
    old = score.clef_at(part, staff, measure)
    if old is None:
        return "No había clave que cambiar."
    old_mid = P.clef_middle_line(old.sign, old.line, old.octave_change)
    new_mid = P.clef_middle_line(sign, line, 0)
    shift = P.diatonic_index(*new_mid) - P.diatonic_index(*old_mid)
    next_changes = [c.measure for c in score.clefs if c.part == part and c.staff == staff and c.measure > measure]
    end = min(next_changes) - 1 if next_changes else len(score.measures) - 1
    score.clefs = [c for c in score.clefs if not (c.part == part and c.staff == staff and c.measure == measure)]
    score.clefs.append(ClefChange(part, staff, measure, Fraction(0), sign.upper(), line, 0))
    count = 0
    for n in score.notes:
        if n.part == part and n.staff == staff and measure <= n.measure <= end and not n.rest:
            idx = P.diatonic_index(n.step, n.octave) + shift
            step, octave = P.from_diatonic_index(idx)
            key_alt = P.key_alterations(score.key_at(part, n.measure, staff))
            alter = n.alter if n.accidental_shown else key_alt[step]
            n.step, n.octave, n.alter = step, octave, alter
            old = n.midi
            n.midi = P.midi_from(step, alter, octave)
            log_pitch_change(n, old, "cambio de clave")
            n.user_modified = True
            count += 1
    return f"Clave cambiada a {P.clef_description(sign, line)}; {count} nota(s) reinterpretadas."


def delete_measures(score: Score, m0: int, m1: int) -> str:
    count = m1 - m0 + 1
    score.notes = [n for n in score.notes if not (m0 <= n.measure <= m1)]
    for n in score.notes:
        if n.measure > m1:
            n.measure -= count
    for coll in (score.directions, score.tempos, score.chord_symbols, score.clefs, score.keys):
        keep = []
        for x in coll:
            if m0 <= x.measure <= m1:
                if getattr(x, "measure", 0) == m0 and isinstance(x, (ClefChange, KeyChange)):
                    x.measure = m0
                    keep.append(x)
                continue
            if x.measure > m1:
                x.measure -= count
            keep.append(x)
        coll[:] = keep
    del score.measures[m0:m1 + 1]
    score.renumber_measures()
    return f"{count} compás(es) eliminados."
