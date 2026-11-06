"""Regresión: por qué una nota sale «revisar», acordes arpegiados y escritura al mover un semitono.

Casos reales de «In The Pool»:
* c. 14: acorde arpegiado (línea ondulada) que Audiveris no vio y HOMR sí. Las cinco notas añadidas por
  HOMR se comprobaron en la imagen, pero salían «revisar» sin decir por qué.
* c. 17–21: las 46 notas de una «8va» con rótulo y línea medidos en la imagen salían «revisar».
* Al bajar un Mi un semitono en Mi mayor salía «Mi♭» en vez de «Re♯» (que está en la armadura).
"""

from __future__ import annotations

from fractions import Fraction as F

import pytest

import synthetic as S
from note_cascade.music_model import edits
from note_cascade.music_model.arpeggio import (ARPEGGIO, ROLL_STEP_S, arpeggiated_chords, is_arpeggiated,
                                               set_arpeggio)
from note_cascade.music_model.model import ClefChange, KeyChange, Measure, PartInfo, Score, TempoMark
from note_cascade.music_model.timeline import PerformanceOptions, build_performance
from note_cascade.music_validation.explain import count_text, note_reasons, selection_reasons
from note_cascade.music_validation.issues import Fix, Issue
from note_cascade.music_validation.validator import validate


def _score(fifths: int = 0, bpm: float = 60.0, ts=(4, 4), measures: int = 2) -> Score:
    s = Score()
    s.parts.append(PartInfo("P1", "Piano", "Pno.", 2, 0, True))
    for i in range(measures):
        s.measures.append(Measure(i, str(i + 1), ts, time_sig_change=i == 0))
    s.keys.append(KeyChange(0, 0, fifths))
    s.clefs.append(ClefChange(0, 1, 0, F(0), "G", 2))
    s.clefs.append(ClefChange(0, 2, 0, F(0), "F", 4))
    s.tempos.append(TempoMark(0, F(0), bpm, per_minute=bpm))
    return s


def _chord(s: Score, names, dur=4, measure=0, onset=0, staff=1, arp_on=None, **kw):
    notes = [S._note(s, measure, onset, dur, nm, staff, 1 if staff == 1 else 5, **kw) for nm in names]
    if arp_on is not None:
        notes[arp_on].articulations.append(ARPEGGIO)   # como HOMR: el signo en una sola nota del acorde
    s.sort_notes()
    return notes


def _starts(perf, ids) -> list[float]:
    by_id = {pn.note_ids[0]: pn for pn in perf.notes}
    return [round(by_id[i].start_s, 4) for i in ids]


def _ends(perf, ids) -> list[float]:
    by_id = {pn.note_ids[0]: pn for pn in perf.notes}
    return [round(by_id[i].end_s, 4) for i in ids]


# ---------------------------------------------------------------------------- arpegio al tocar

def test_arpeggiated_chord_rolls_from_bottom_to_top_and_keeps_the_end():
    s = _score(bpm=60)
    notes = _chord(s, ["C4", "E4", "G4"], dur=4, arp_on=2)          # redonda, signo solo en la nota de arriba
    perf = build_performance(s)
    ids = [n.id for n in notes]
    assert _starts(perf, ids) == [0.0, round(ROLL_STEP_S, 4), round(2 * ROLL_STEP_S, 4)]
    assert _ends(perf, ids) == [4.0, 4.0, 4.0], "cada nota se mantiene hasta el final del acorde"
    # sin el signo, las tres a la vez
    for n in notes:
        n.articulations = []
    assert _starts(build_performance(s), ids) == [0.0, 0.0, 0.0]


def test_arpeggio_respects_the_articulation_setting_and_short_chords():
    s = _score(bpm=240)
    notes = _chord(s, ["C4", "E4", "G4", "C5"], dur=F(1, 2), arp_on=0)   # corchea a 240: 0,125 s
    ids = [n.id for n in notes]
    starts = _starts(build_performance(s), ids)
    assert starts[-1] <= 0.4 * 0.125 + 1e-6, "el arpegio no ocupa más del 40 % de una figura corta"
    assert starts == sorted(starts) and starts[0] == 0.0
    off = build_performance(s, PerformanceOptions(articulations=False))
    assert _starts(off, ids) == [0.0, 0.0, 0.0, 0.0]


def test_each_hand_rolls_its_own_chord_and_tied_notes_are_not_struck_again():
    s = _score(bpm=60)
    rh = _chord(s, ["E4", "G#4"], dur=4, arp_on=0)
    lh = _chord(s, ["E2", "B2", "G#3"], dur=4, staff=2, arp_on=1)
    perf = build_performance(s)
    assert _starts(perf, [n.id for n in rh])[0] == 0.0 and _starts(perf, [n.id for n in lh])[0] == 0.0
    assert len(arpeggiated_chords(s)) == 2
    # acorde ligado al compás siguiente: la continuación no se vuelve a arpegiar
    s2 = _score(bpm=60)
    first = _chord(s2, ["C4", "E4", "G4"], dur=4, arp_on=0, tie_start=True)
    second = _chord(s2, ["C4", "E4", "G4"], dur=4, measure=1, arp_on=0, tie_stop=True)
    perf2 = build_performance(s2)
    by_id = {i: pn for pn in perf2.notes for i in pn.note_ids}
    for a, b in zip(first, second):
        assert by_id[a.id] is by_id[b.id], "la ligadura se mantiene: una sola nota sostenida"
        assert round(by_id[a.id].end_s, 4) == 8.0


def test_toggle_arpeggio_on_the_whole_chord():
    s = _score()
    notes = _chord(s, ["C4", "E4", "G4"])
    single = S._note(s, 0, 0, 4, "C3", 2, 5)
    assert not is_arpeggiated(s, notes[1])
    assert set_arpeggio(s, [notes[1].id], True) == 1
    assert all(ARPEGGIO in n.articulations for n in notes) and is_arpeggiated(s, notes[0])
    assert set_arpeggio(s, [single.id], True) == 0, "una nota sola no se arpegia"
    assert set_arpeggio(s, [notes[0].id], False) == 1
    assert not any(ARPEGGIO in n.articulations for n in notes)


def test_arpeggio_is_kept_in_musicxml():
    from note_cascade.musicxml.reader import read_musicxml_bytes
    s = _score()
    _chord(s, ["C4", "E4", "G4"], arp_on=1)
    back = read_musicxml_bytes(S.to_musicxml_bytes(s)).score
    chord = next(c for c in arpeggiated_chords(back))
    assert [n.midi for n in chord] == [60, 64, 67]


def test_arpeggio_from_the_second_engine_is_copied_to_the_same_chord():
    from note_cascade.music_validation.repairs import arpeggios_from_secondary
    a, b = _score(), _score()
    mine = _chord(a, ["D#4", "E4", "G#4"], dur=4)
    _chord(b, ["D#4", "E4", "G#4"], dur=4, arp_on=2)
    issues = arpeggios_from_secondary(a, b, [(0, 0), (1, 1)], "HOMR")
    assert [i.code for i in issues] == ["arpegio_motor"] and issues[0].severity == "info"
    assert all(ARPEGGIO in n.articulations for n in mine)
    assert arpeggios_from_secondary(a, b, [(0, 0), (1, 1)], "HOMR") == [], "no se repite"


# ---------------------------------------------------------------------------- por qué «revisar»

def test_notes_added_by_the_second_engine_and_seen_in_the_image_are_not_marked_review():
    s = _score(fifths=4, ts=(12, 8))
    notes = _chord(s, ["D#4", "E4", "G#4"], dur=6, fifths=4)
    for n in notes:
        n.source = "HOMR"
    added = Issue("aviso", "comparacion", "Compás 1: notas añadidas por HOMR", "El motor principal no vio…",
                  measure=0, note_ids=[n.id for n in notes], evidence="comprobado", code="complemento_motor")
    # una de las tres no se encontró en la imagen: sigue el aviso
    notes[0].image_verified = notes[1].image_verified = True
    rep = validate(s, extra_issues=[added])
    assert added.severity == "aviso" and rep.note_status[notes[2].id] == "revisar"
    # las tres en la imagen: pasa a información y no se marcan
    notes[2].image_verified = True
    rep = validate(s, extra_issues=[added])
    assert added.severity == "info" and "comprobaron en la imagen" in added.message
    assert {rep.note_status[n.id] for n in notes} == {"ok"}
    validate(s, extra_issues=[added])
    assert added.message.count("comprobaron en la imagen") == 1


def test_octave_line_measured_in_the_image_does_not_mark_its_notes():
    s = _score()
    notes = _chord(s, ["G5", "B5"], dur=4)
    old = Issue("aviso", "altura", "Indicación «8va» aplicada (compás 1)",
                "Se desplazaron 2 nota(s) del pentagrama 1 para que suenen una octava más agudas (rótulo y línea "
                "medidos en la imagen). Escúchalo; si no corresponde, deshazlo con la corrección.",
                measure=0, note_ids=[n.id for n in notes], evidence="comprobado", code="octava_texto")
    engine_only = Issue("aviso", "altura", "Indicación «8va» aplicada (compás 2)",
                        "Se desplazaron 2 nota(s)… (inicio marcado por el motor y línea medida en la imagen).",
                        measure=1, note_ids=[], evidence="comprobado", code="octava_texto")
    rep = validate(s, extra_issues=[old, engine_only])
    assert old.severity == "info" and engine_only.severity == "aviso"
    assert {rep.note_status[n.id] for n in notes} != {"revisar"}


def test_every_review_mark_comes_with_its_reason():
    s = _score()
    a, b = _chord(s, ["C4", "E4"], dur=4)
    a.confidence = b.confidence = 0.97
    rhythm = Issue("aviso", "ritmo", "Compás 1: ritmo de una voz ajustado", "Una voz se salía…", measure=0,
                   note_ids=[a.id], code="voz_ajustada")
    octave = Issue("info", "altura", "Indicación «8va» aplicada (compás 1)", "…", measure=0,
                   note_ids=[a.id, b.id], code="octava_texto")
    rep = validate(s, extra_issues=[rhythm, octave])
    st_a, st_b = rep.note_status[a.id], rep.note_status[b.id]
    assert st_a == "revisar" and st_b == "ok"
    ra = note_reasons(s, rep.issues, a, st_a)
    assert ra[0].level == "revisar" and ra[0].issue is rhythm, "el aviso que la marca, para abrirlo"
    assert any(r.issue is octave and r.level == "info" for r in ra)
    rb = note_reasons(s, rep.issues, b, st_b)
    assert rb[0].level == "ok" and "seguridad" in rb[0].text
    # poca confianza y sin aviso: también se dice
    b.confidence = 0.3
    rep = validate(s, extra_issues=[octave])
    rb = note_reasons(s, rep.issues, b, rep.note_status[b.id])
    assert rep.note_status[b.id] == "revisar" and "poca seguridad" in rb[0].text
    # varias notas: el mismo aviso una sola vez
    counts, reasons = selection_reasons(s, rep.issues, [a, b], lambda i: rep.note_status[i])
    assert sum(counts.values()) == 2 and len([r for r in reasons if r.issue is octave]) == 1
    assert count_text({"ok": 3}) == "3 verificadas" and count_text({"revisar": 1, "ok": 1}) == "1 para revisar · 1 verificada"


# ---------------------------------------------------------------------------- escritura de la altura

@pytest.mark.parametrize("fifths,start,delta,expected", [
    (4, "E4", -1, "D#4"),   # Mi mayor: bajar Mi da Re♯ (de la armadura), no Mi♭
    (4, "A4", -1, "G#4"),   # bajar La da Sol♯
    (0, "G4", -1, "Gb4"),   # Do mayor: fuera de la armadura, bemol al bajar
    (0, "F4", 1, "F#4"),    # y sostenido al subir
    (-1, "A4", 1, "Bb4"),   # Fa mayor: subir La da Si♭ (de la armadura)
])
def test_semitone_edits_follow_the_key_signature(fifths, start, delta, expected):
    s = _score(fifths=fifths)
    n = S._note(s, 0, 0, 4, start, 1, 1, fifths=fifths)
    edits.set_pitch(s, [n.id], delta=delta)
    assert f"{n.step}{ {1: '#', -1: 'b', 0: ''}[n.alter] }{n.octave}".replace(" ", "") == expected


def test_octave_shift_keeps_the_written_accidental():
    from note_cascade.music_validation.fixes import apply_fix
    s = _score(fifths=4)
    n = S._note(s, 0, 0, 4, "Eb4", 1, 1, fifths=4)   # un Mi♭ escrito en Mi mayor (con su bemol)
    apply_fix(s, Fix("8va", "transpose_notes", {"note_ids": [n.id], "semitones": 12}))
    assert (n.step, n.alter, n.octave) == ("E", -1, 5)
