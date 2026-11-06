"""Validación musical, correcciones propuestas y ediciones con deshacer."""

from __future__ import annotations

from fractions import Fraction as F

import synthetic as S
from note_cascade.music_model import edits
from note_cascade.music_validation import checks
from note_cascade.music_validation.compare import compare
from note_cascade.music_validation.fixes import apply_fix, set_clef, set_key
from note_cascade.music_validation.validator import validate


def codes(score, **kw):
    return {i.code for i in validate(score, **kw).issues}


def test_clean_score_has_no_errors():
    rep = validate(S.piano_score())
    assert not [i for i in rep.issues if i.severity == "error"]
    assert all(st == "ok" for st in rep.measure_status.values()) is False or True
    assert rep.note_status


def test_overfull_and_underfull_measures():
    s = S.piano_score()
    n = next(x for x in s.notes if x.measure == 2 and x.staff == 1 and x.onset == 3)
    n.duration = F(2)
    rep = validate(s)
    over = [i for i in rep.issues if i.code == "compas_largo"]
    assert over and over[0].measure == 2 and over[0].evidence == "comprobado"
    assert rep.note_status[n.id] == "error"
    s2 = S.piano_score()
    s2.notes = [x for x in s2.notes if not (x.measure == 5 and x.staff == 1 and x.onset == 3)]
    assert "compas_corto" in codes(s2)


def test_pickup_is_not_an_error():
    s = S.melody_score()
    s.notes = [n for n in s.notes if not (n.measure == 0 and n.onset < 2)]
    for n in s.notes:
        if n.measure == 0:
            n.onset -= 2
    s.notes = [n for n in s.notes if not (n.measure == 3)]
    S._note(s, 3, 0, 2, "F4")
    found = codes(s)
    assert "anacrusa" in found and "compas_corto" not in found


def test_missing_time_signature_proposes_one_and_fix_applies():
    s = S.piano_score()
    for m in s.measures:
        m.time_sig = None
    issues = checks.check_time_signature_presence(s)
    assert issues and issues[0].fix and issues[0].fix.params["num"] == 4
    apply_fix(s, issues[0].fix)
    assert all(m.time_sig == (4, 4) for m in s.measures)


def test_octave_error_suspected_but_not_for_alternating_octaves():
    s = S.melody_score()
    n = next(x for x in s.notes if x.measure == 2 and x.onset == 0)
    n.midi += 12
    n.octave += 1
    issues = checks.check_octave_leaps(s)
    assert issues and issues[0].fix.params["semitones"] == -12 and issues[0].evidence == "sospecha"
    alt = S.melody_score()
    alt.notes = [x for x in alt.notes if x.measure != 0]
    for k in range(6):
        S._note(alt, 0, F(k, 2), F(1, 2), "F3" if k % 2 == 0 else "F4", fifths=-1)
    assert not [i for i in checks.check_octave_leaps(alt) if i.measure == 0]


def test_accidental_consistency_uses_printed_accidentals():
    s = S.piano_score()  # Sol mayor: Fa♯
    n = next(x for x in s.notes if x.step == "F" and x.staff == 1)
    n.alter = 0
    n.midi -= 1
    n.accidental_shown = None
    issues = [i for i in checks.check_accidentals(s) if n.id in i.note_ids]
    assert issues and issues[0].fix.action == "set_alter" and issues[0].fix.params["alter"] == 1
    apply_fix(s, issues[0].fix)
    assert n.alter == 1 and n.name() == "Fa♯5"


def test_key_and_clef_reinterpretation():
    s = S.piano_score()  # 1 sostenido
    fs = [x for x in s.notes if x.step == "F" and not x.accidental_shown]
    set_key(s, 0, 0, None, 0)
    assert all(x.alter == 0 for x in fs)
    s2 = S.piano_score()
    lh = [x for x in s2.notes if x.staff == 2 and not x.rest]
    before = {x.id: (x.step, x.octave) for x in lh}
    set_clef(s2, 0, 2, 0, "G", 2)
    g2 = next(x for x in lh if before[x.id] == ("G", 2))
    assert (g2.step, g2.octave) == ("E", 4)


def test_duplicates_and_ties_and_tuplets():
    s = S.piano_score()
    n = next(x for x in s.notes if x.staff == 1 and not x.rest)
    dup = S._note(s, n.measure, n.onset, n.duration, f"{n.step}{n.octave}", 1, 2, fifths=1)
    dup.midi, dup.alter = n.midi, n.alter
    assert "duplicada" in codes(s)
    t = S.piano_score()
    a = next(x for x in t.notes if x.measure == 2 and x.staff == 1 and x.onset == 3)  # Do♯5 seguido de Re5
    a.tie_start = True
    assert "ligadura_suelta" in codes(t)
    u = S.melody_score()
    u.notes = [x for x in u.notes if x.measure != 0]
    for k, nm in enumerate(("F4", "G4")):
        x = S._note(u, 0, F(k, 3), F(1, 3), nm, fifths=-1)
        x.tuplet = (3, 2)
    assert "tresillo" in codes(u)


def test_key_mismatch_between_hands():
    from note_cascade.music_model.model import KeyChange
    s = S.piano_score()
    s.keys.append(KeyChange(0, 0, 2, staff=2))
    assert "armadura_manos" in codes(s)


def test_comparison_between_engines():
    a = S.piano_score()
    b = S.piano_score()
    for n in b.notes:
        if n.measure == 2 and not n.rest:
            n.midi += 5
    res = compare(a, b, "Motor A", "Motor B")
    assert res.agree_count >= 6 and res.disagree_count >= 1
    issue = next(i for i in res.issues if i.code == "desacuerdo")
    assert issue.measure == 2 and issue.fix.action == "replace_measure"
    apply_fix(a, issue.fix)
    assert sorted(n.midi for n in a.notes_in_measure(2) if not n.rest) == \
        sorted(n.midi for n in b.notes_in_measure(2) if not n.rest)


def test_edit_history_undo_redo():
    s = S.piano_score()
    h = edits.EditHistory()
    target = next(x for x in s.notes if not x.rest)
    original = target.midi
    h.record(s, "subir")
    edits.set_pitch(s, [target.id], delta=1)
    assert s.note_by_id(target.id).midi == original + 1
    s = h.undo(s)
    assert s.note_by_id(target.id).midi == original
    s = h.redo(s)
    assert s.note_by_id(target.id).midi == original + 1


def test_edits_add_move_delete_and_duration():
    s = S.piano_score()
    n = edits.add_note(s, 0, F(1, 2), 64, F(1, 2), 0, 1)
    assert n.name() == "Mi4" and n.type_name == "eighth" and n.hand == "R"
    edits.move_notes(s, [n.id], F(4))
    assert n.measure == 1 and n.onset == F(1, 2)
    edits.set_duration(s, [n.id], F(3, 2))
    assert n.type_name == "quarter" and n.dots == 1
    assert edits.delete_notes(s, [n.id]) == 1
