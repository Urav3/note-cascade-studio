"""Pruebas de las correcciones de reproducción: líneas de octava, tempos, unísonos,
seguimiento de la partitura y robustez del reproductor."""

from __future__ import annotations

from fractions import Fraction as F

import numpy as np
import pytest

import synthetic as S
from note_cascade.layout.integrate import integrate_text
from note_cascade.layout.text_classifier import TextElement
from note_cascade.music_model.model import PagePosition
from note_cascade.music_model.timeline import build_performance
from note_cascade.music_validation.fixes import apply_fix


def _placed_score():
    """Partitura de 8 compases en dos sistemas de 4, con posición de compases y notas en la página."""
    s = S.piano_score()
    for m in s.measures:
        m.position = PagePosition(0, 0.08 + (m.index % 4) * 0.21, 0.2 + (m.index // 4) * 0.3, 0.21, 0.12)
    for n in s.notes:
        mp = s.measures[n.measure].position
        x = mp.x + 0.02 + float(n.onset) / 4 * (mp.w - 0.04)
        y = mp.y + (0.02 if n.staff == 1 else 0.08)
        n.position = PagePosition(0, x, y, 0.01, 0.01)
    return s


def test_tempo_text_goes_to_the_system_it_was_assigned_to():
    s = _placed_score()
    s.tempos = []
    # En el hueco entre sistemas, más cerca del primero, pero el clasificador lo asignó al segundo
    el = TextElement("= 75", 0, 0.10, 0.36, 0.16, 0.38, "ocr", "tempo", value="75", system=1)
    integrate_text(s, [el])
    assert [t.measure for t in s.tempos] == [4]


def test_fermata_does_not_leave_the_tempo_stuck():
    s = S.piano_score()
    s.tempos = s.tempos[:1]
    from note_cascade.music_model.model import TempoMark
    s.tempos.append(TempoMark(4, F(0), 150.0, text="= 150", per_minute=150, source="texto"))
    s.tempos.append(TempoMark(2, F(0), 96.0, text="= 96", per_minute=96, source="texto"))  # desordenado
    held = next(n for n in s.notes if n.measure == 3 and n.staff == 1)
    held.articulations.append("fermata")
    perf = build_performance(s)
    events = perf.tempo_events
    assert [q for q, _ in events] == sorted(q for q, _ in events)
    q4 = 16.0
    bpm_after = [b for q, b in events if q <= q4][-1]
    assert bpm_after == pytest.approx(150.0), "tras el calderón vuelve el tempo indicado"
    assert any(b == pytest.approx(48.0) for _q, b in events), "el calderón alarga la nota"


def test_unison_between_hands_is_shown_in_both_hands_but_sounds_once():
    from note_cascade.music_validation.validator import validate
    s = S.piano_score(measures=4)
    rh = next(n for n in s.notes if n.measure == 0 and n.staff == 1 and not n.rest)
    twin = S._note(s, 0, rh.onset, rh.duration, f"{rh.step}{rh.octave}", staff=2, voice=5)
    s.sort_notes()
    perf = build_performance(s)
    same = [p for p in perf.notes if p.midi == rh.midi and abs(p.start_q - float(rh.onset)) < 1e-6]
    assert {p.hand for p in same} == {"R", "L"}, "cada mano conserva su nota (se ve en su color)"
    assert sum(not p.silent for p in same) == 1, "pero la tecla suena una sola vez"
    from note_cascade.player import synth
    stems, _ = synth.render_stems(perf, 1.0, "piano")
    assert set(stems) == {"R", "L"}
    report = validate(s)
    assert any(i.code == "unisono_manos" and set(i.note_ids) == {rh.id, twin.id} for i in report.issues)


def test_pause_while_preparing_sound_does_not_start_playing():
    from note_cascade.player.audio_engine import PlaybackEngine
    perf = build_performance(S.piano_score(measures=4))
    eng = PlaybackEngine("internal")
    eng._silent = True
    eng.set_performance(perf, keep_position=False)
    assert eng.wait_ready(30)
    eng.play()
    eng.set_speed(2.0)          # vuelve a generar el audio y reanudaría al terminar
    eng.pause()                 # … pero el usuario ha pausado mientras tanto
    assert eng.wait_ready(30)
    assert not eng.is_playing
    eng.play()
    assert eng.is_playing and eng.speed == 2.0
    eng.close()


@pytest.mark.gui
def test_score_follow_window_highlights_sounding_notes(qapp_offscreen):
    import cv2
    from PySide6.QtCore import Qt
    from note_cascade.app.config import AppConfig
    from note_cascade.pipeline.conversion import revalidate_project
    from note_cascade.project.project import PageRecord, Project
    from note_cascade.ui.controller import AppController
    from note_cascade.ui.widgets.score_follow import ScoreFollowWindow

    p = Project.new_workspace("seguimiento")
    p.score = _placed_score()
    p.original_score = p.score.clone()
    p.workspace.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(p.workspace / "pagina.png"), np.full((1400, 1000, 3), 255, np.uint8))
    p.pages = [PageRecord(0, "Página 1", original_file="pagina.png", processed_file="pagina.png",
                          original_size=(1000, 1400), processed_size=(1000, 1400))]
    revalidate_project(p, AppConfig())
    c = AppController(AppConfig())
    c.player._silent = True
    c.set_project(p)
    win = ScoreFollowWindow(c)
    win.setAttribute(Qt.WA_DontShowOnScreen)
    win.show()
    qapp_offscreen.processEvents()
    perf = c.performance
    target = next(n for n in perf.notes if n.measure == 2)
    c.player.seek(target.start_s + 0.01)
    win._tick_inner()
    assert c.score.measures[win._measure].number == "3"
    assert win._active, "las notas que suenan se resaltan"
    assert any(set(n.note_ids) & set(win._shown_ids) for n in perf.notes if n.start_s <= target.start_s + 0.01 < n.end_s)
    win.close()
    c.player.close()
    p.dirty = False


def test_ornate_pedal_sign_misread_by_ocr_is_pedal_not_lyrics():
    from note_cascade.layout.text_classifier import NormStaff, NormSystem, classify_page
    from note_cascade.pdf_processing.pdf_document import TextSpan
    il = 0.008
    staves = [NormStaff(0.08, 0.92, 0.20, 0.20 + 4 * il, il), NormStaff(0.08, 0.92, 0.28, 0.28 + 4 * il, il)]
    systems = [NormSystem([0, 1], 0.08, 0.92, 0.20, 0.28 + 4 * il, [0.5, 0.92])]
    spans = [TextSpan(t, x, 0.33, x + 0.04, 0.345, size=0.015, source="ocr", confidence=0.85)
             for t, x in (("Reo.", 0.12), ("Leo.", 0.40), ("人e0.", 0.70))]
    out = classify_page(spans, staves, systems, 0, piano_like=True)
    assert [e.category for e in out] == ["pedal"] * 3


# ---------------------------------------------------------------------------
# Reparaciones del resultado del motor
# ---------------------------------------------------------------------------

def _two_engine_scores():
    """El motor principal pierde dos notas de un acorde de redondas; el segundo las ve."""
    primary = S.piano_score(measures=4)
    secondary = primary.clone()
    lh = [n for n in primary.notes if n.measure == 3 and n.staff == 2 and not n.rest]
    lost = {n.midi for n in lh[1:]}
    primary.notes = [n for n in primary.notes if n not in lh[1:]]
    return primary, secondary, lost


def test_secondary_engine_completes_missing_chord_notes():
    from note_cascade.music_validation.compare import align
    from note_cascade.music_validation.repairs import complement_from_secondary
    primary, secondary, lost = _two_engine_scores()
    issues = complement_from_secondary(primary, secondary, align(primary, secondary), "HOMR")
    assert [i.code for i in issues] == ["complemento_motor"] and issues[0].measure == 3
    got = {n.midi for n in primary.notes if n.measure == 3 and n.staff == 2 and not n.rest}
    assert lost <= got


def test_secondary_engine_is_not_merged_when_it_contradicts_the_primary():
    from note_cascade.music_validation.compare import align
    from note_cascade.music_validation.repairs import complement_from_secondary
    primary, secondary, _lost = _two_engine_scores()
    kept = next(n for n in primary.notes if n.measure == 3 and n.staff == 2 and not n.rest)
    next(n for n in secondary.notes if n.id == kept.id).midi += 1  # discrepa en una nota que el principal vio
    before = [n.id for n in primary.notes if n.measure == 3]
    issues = complement_from_secondary(primary, secondary, align(primary, secondary), "HOMR")
    assert not [i for i in issues if i.measure == 3]
    assert [n.id for n in primary.notes if n.measure == 3] == before


def test_tied_chord_keeps_its_accidentals_in_the_next_measure():
    from note_cascade.music_validation.repairs import repair_tied_accidentals
    s = S.piano_score(measures=4, fifths=4)
    s.notes = [n for n in s.notes if n.measure not in (2, 3)]
    a = S._note(s, 2, 0, 4, "C4", staff=2, voice=5, fifths=4)       # Do natural escrito (♮)
    a.accidental_shown = "natural"
    a.tie_start = True
    b = S._note(s, 3, 0, 4, "C#4", staff=2, voice=5, fifths=4)      # el motor leyó la armadura
    b.accidental_shown = None
    s.sort_notes()
    issues = repair_tied_accidentals(s)
    assert b.midi == a.midi and b.alter == 0 and b.tie_stop
    assert [i.code for i in issues] == ["ligadura_alteracion"]


def test_untied_note_after_barline_keeps_key_signature():
    from note_cascade.music_validation.repairs import repair_tied_accidentals
    s = S.piano_score(measures=4, fifths=4)
    s.notes = [n for n in s.notes if n.measure not in (2, 3)]
    a = S._note(s, 2, 0, 4, "C4", staff=2, voice=5, fifths=4)
    a.accidental_shown = "natural"
    b = S._note(s, 3, 0, 4, "C#4", staff=2, voice=5, fifths=4)
    b.accidental_shown = None
    s.sort_notes()
    assert repair_tied_accidentals(s) == [] and b.alter == 1, "la barra de compás anula la alteración"


def test_whole_rest_fills_a_compound_measure_and_melody_over_held_chord_becomes_a_voice():
    from note_cascade.music_validation.repairs import repair_measure_rests, repair_voice_overflow
    s = S.piano_score(measures=2)
    for m in s.measures:
        m.time_sig = (12, 8)
    s.notes = []
    r1 = S._note(s, 0, 0, 4, None, staff=1)
    r1.type_name = "whole"
    r2 = S._note(s, 0, 4, 4, None, staff=2, voice=5)
    r2.type_name = "whole"
    chord = [S._note(s, 1, 0, 6, nm, staff=1) for nm in ("G#3", "C#4")]
    for c in chord:
        c.position = PagePosition(0, 0.30, 0.3, 0.01, 0.01)
    tail = [S._note(s, 1, 6, F(1, 2), "G#4", staff=1), S._note(s, 1, F(13, 2), F(1, 2), None, staff=1),
            S._note(s, 1, 7, F(1, 2), None, staff=1), S._note(s, 1, F(15, 2), F(1, 2), "G#4", staff=1)]
    for k, n in enumerate(tail):
        n.position = PagePosition(0, 0.50 + 0.05 * k, 0.25, 0.01, 0.01)
    s.sort_notes()
    assert repair_measure_rests(s)
    assert (r1.onset, r1.duration, r2.onset, r2.duration) == (0, 6, 0, 6)
    assert repair_voice_overflow(s)
    assert [n.onset for n in tail] == [4, F(9, 2), 5, F(11, 2)] and len({n.voice for n in tail}) == 1
    assert tail[0].voice != chord[0].voice


def test_score_follow_uses_each_written_note_hand_and_skips_guessed_positions(qapp_offscreen):
    from note_cascade.music_model.timeline import build_performance
    s = _placed_score()
    rh = next(n for n in s.notes if n.measure == 0 and n.staff == 1 and not n.rest)
    twin = S._note(s, 0, rh.onset, rh.duration, f"{rh.step}{rh.octave}", staff=2, voice=5)
    twin.position = PagePosition(0, rh.position.x, 0.29, 0.01, 0.01)
    s.sort_notes()
    perf = build_performance(s)
    pair = [p for p in perf.notes if rh.id in p.note_ids or twin.id in p.note_ids]
    assert {p.hand for p in pair} == {"R", "L"}


def test_missing_dot_and_hidden_time_signature_change():
    from note_cascade.music_validation.repairs import repair_measure_time_signatures, repair_missing_dots
    s = S.piano_score(measures=3)
    for m in s.measures:
        m.time_sig = (12, 8)
    s.notes = []
    for mi in (0, 2):
        for st, nm in ((1, "E4"), (2, "C3")):
            S._note(s, mi, 0, 6, nm, staff=st, voice=1 if st == 1 else 5)
    for st, nm in ((1, "G4"), (2, "C3")):          # redonda sin puntillo en 12/8
        S._note(s, 1, 0, 4, nm, staff=st, voice=1 if st == 1 else 5)
    s.sort_notes()
    assert repair_missing_dots(s) and all(n.duration == 6 and n.dots == 1 for n in s.notes_in_measure(1))
    # un compás de 3/8 que el motor no leyó: ambos pentagramas completos con 1,5 negras
    s2 = S.piano_score(measures=3)
    for m in s2.measures:
        m.time_sig = (12, 8)
    s2.notes = []
    for mi in (0, 2):
        for st, nm in ((1, "E4"), (2, "C3")):
            S._note(s2, mi, 0, 6, nm, staff=st, voice=1 if st == 1 else 5)
    for k in range(3):
        S._note(s2, 1, F(k, 2), F(1, 2), "A4", staff=1)
    S._note(s2, 1, 0, F(3, 2), "A3", staff=2, voice=5)
    s2.sort_notes()
    issues = repair_measure_time_signatures(s2)
    assert s2.measures[1].time_sig == (3, 8) and s2.measures[2].time_sig == (12, 8)
    assert s2.measures[2].time_sig_change and [i.code for i in issues] == ["cambio_compas"]


def test_figure_read_too_long_is_shortened_by_its_spacing():
    from note_cascade.music_validation.repairs import repair_duration_by_spacing
    s = S.piano_score(measures=1)
    s.measures[0].time_sig = (12, 8)
    s.measures[0].position = PagePosition(0, 0.1, 0.2, 0.62, 0.12)
    s.notes = []
    # doce corcheas igualmente espaciadas; la tercera se leyó como negra (todo lo demás llega tarde)
    t = F(0)
    for k in range(12):
        d = F(1) if k == 2 else F(1, 2)
        n = S._note(s, 0, t, d, "E3", staff=2, voice=5)
        n.position = PagePosition(0, 0.12 + 0.05 * k, 0.3, 0.01, 0.01)
        t += d
    s.sort_notes()
    issues = repair_duration_by_spacing(s)
    assert [i.code for i in issues] == ["figura_espaciado"]
    notes = sorted(s.notes, key=lambda n: n.onset)
    assert [n.onset for n in notes] == [F(k, 2) for k in range(12)] and notes[2].duration == F(1, 2)


def test_user_correction_is_offered_for_measures_the_engine_read_the_same_way():
    from note_cascade.music_validation.fixes import apply_fix
    from note_cascade.music_validation.learning import repeated_correction_proposals
    s = S.piano_score(measures=8)          # el patrón se repite cada 4 compases
    original = s.clone()
    wrong = next(n for n in s.notes if n.measure == 1 and n.staff == 1 and not n.rest)
    wrong.midi += 2
    wrong.user_modified = True              # el usuario corrigió el compás 2
    issues = repeated_correction_proposals(s, original)
    assert [i.measure for i in issues] == [5]   # el compás 6 se leyó igual
    apply_fix(s, issues[0].fix)
    sig = lambda mi: sorted((n.staff, n.onset, n.duration, n.midi) for n in s.notes_in_measure(mi) if not n.rest)
    assert sig(5) == sig(1)


def test_accept_all_of_the_same_kind_never_mixes_apply_and_undo():
    from note_cascade.music_validation import learning
    from note_cascade.music_validation.issues import Fix, Issue
    a = Issue("aviso", "altura", "x", "y", code="octava_texto", measure=1,
              fix=Fix("Desplazar +12 semitonos", "octave_shift", {}))
    b = Issue("aviso", "altura", "x", "y", code="octava_texto", measure=2,
              fix=Fix("Desplazar +12 semitonos", "octave_shift", {}))
    undo = Issue("aviso", "altura", "x", "y", code="octava_texto", measure=3,
                 fix=Fix("Quitar la indicación", "octave_shift", {}))
    closed = Issue("aviso", "altura", "x", "y", code="octava_texto", measure=4, status="aceptada",
                   fix=Fix("Desplazar +12 semitonos", "octave_shift", {}))
    assert learning.same_kind(a, [a, b, undo, closed]) == [a, b]


def test_octave_lines_read_as_endings_do_not_skip_measures():
    from note_cascade.music_validation.repairs import repair_bogus_endings
    s = S.piano_score(measures=8)
    # el motor tomó una línea «8va - - -┐» por casillas de 1.ª/2.ª vez (sin ninguna barra de repetición)
    s.measures[2].ending_numbers, s.measures[2].ending_type = [1], "start-stop"
    s.measures[3].ending_numbers, s.measures[3].ending_type = [2], "start-stop"
    perf = build_performance(s)
    assert [pm.measure for pm in perf.play_measures] == list(range(8)), "el reproductor no salta compases"
    issues = repair_bogus_endings(s)
    assert [i.code for i in issues] == ["casillas_falsas"] and not s.measures[2].ending_numbers


def test_after_a_repeat_the_following_measures_are_first_pass():
    s = S.piano_score(measures=6)
    s.measures[1].repeat_start = True
    s.measures[2].repeat_end = True
    perf = build_performance(s)
    order = [(pm.measure, pm.pass_number) for pm in perf.play_measures]
    assert order == [(0, 1), (1, 1), (2, 1), (1, 2), (2, 2), (3, 1), (4, 1), (5, 1)]


def test_hands_are_separated_only_where_they_meet_and_only_for_listening():
    from note_cascade.app.config import AppConfig
    from note_cascade.export.exporter import performance_options
    s = S.piano_score(measures=8)
    # compases 3 y 4: la izquierda sube hasta tocar la misma altura que la derecha
    for n in s.notes:
        if n.staff == 2 and not n.rest and n.measure in (2, 3):
            n.midi += 24
    cfg = AppConfig()
    cfg.playback.separate_hands = "auto"
    written = build_performance(s, performance_options(cfg, True))
    heard = build_performance(s, performance_options(cfg, True, listening=True))
    before = {p.note_ids[0]: p.midi for p in written.notes}
    for p in heard.notes:
        shift = p.midi - before[p.note_ids[0]]
        expected = -12 if (p.hand == "L" and p.measure in (2, 3)) else 0
        assert shift == expected, (p.measure, p.hand, shift)
    assert any(x.startswith("Manos separadas") for x in heard.notes_info)
    assert not any(x.startswith("Manos separadas") for x in written.notes_info), "lo exportado no cambia"


def test_other_engine_version_is_not_offered_if_it_loses_notes_seen_in_the_image():
    from note_cascade.music_validation.compare import compare
    primary = S.piano_score(measures=4)
    secondary = primary.clone()
    secondary.notes = [n for n in secondary.notes if not (n.measure == 2 and n.staff == 2)]  # no ve la izquierda
    for n in primary.notes:
        n.image_verified = True
    res = compare(primary, secondary, "Audiveris", "HOMR")
    m2 = [i for i in res.issues if i.measure == 2]
    assert m2 and all(i.fix is None and i.code == "desacuerdo_descartado" for i in m2)
    for n in primary.notes:
        n.image_verified = False          # sin confirmación en la imagen se sigue proponiendo
    res = compare(primary, secondary, "Audiveris", "HOMR")
    assert any(i.measure == 2 and i.fix is not None for i in res.issues)


def test_engine_disagreements_cannot_be_accepted_in_bulk():
    from note_cascade.music_validation import learning
    from note_cascade.music_validation.issues import Fix, Issue
    a = Issue("aviso", "comparacion", "x", "y", code="desacuerdo", measure=1,
              fix=Fix("Usar la versión de HOMR para este compás", "replace_measure", {}))
    b = Issue("aviso", "comparacion", "x", "y", code="desacuerdo", measure=2,
              fix=Fix("Usar la versión de HOMR para este compás", "replace_measure", {}))
    assert learning.same_kind(a, [a, b]) == []


@pytest.mark.gui
def test_ctrl_click_on_the_page_adds_notes_without_jumping_back(qapp_offscreen):
    from PySide6.QtCore import Qt
    from note_cascade.app.config import AppConfig
    from note_cascade.pipeline.conversion import revalidate_project
    from note_cascade.project.project import Project
    from note_cascade.ui.controller import AppController
    from note_cascade.ui.main_window import MainWindow
    p = Project.new_workspace("seleccion")
    p.score = S.piano_score(measures=8)
    p.original_score = p.score.clone()
    revalidate_project(p, AppConfig())
    c = AppController(AppConfig())
    c.player._silent = True
    w = MainWindow(c)
    w.setAttribute(Qt.WA_DontShowOnScreen)
    w.resize(900, 700)
    w.show()
    c.set_project(p)
    w._update_steps()
    w.go_step(2)
    qapp_offscreen.processEvents()
    review = w.stack.currentWidget()
    review.cb_window.setCurrentIndex(2)          # 4 compases a la vista
    review.set_focus_measure(0)
    first = next(n for n in p.score.notes if n.measure == 0 and not n.rest)
    far = next(n for n in p.score.notes if n.measure == 5 and not n.rest)
    review._note_clicked_on_page(first.id, False)          # clic en una nota del compás 1
    review._note_clicked_on_page(far.id, True)             # Ctrl+clic en una nota del compás 6
    qapp_offscreen.processEvents()
    assert set(c.selection) == {first.id, far.id}, "las dos notas quedan seleccionadas"
    assert review.editor.m_from <= 5 <= review.editor.m_to, "el editor se queda en el compás de la nota pulsada"
    c.history.clear()
    p.dirty = False
    w.close()


@pytest.mark.gui
def test_page_click_near_a_note_selects_it_and_shift_drag_selects_an_area(qapp_offscreen):
    import cv2
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from note_cascade.app.config import AppConfig
    from note_cascade.pipeline.conversion import revalidate_project
    from note_cascade.project.project import PageRecord, Project
    from note_cascade.ui.controller import AppController
    from note_cascade.ui.main_window import MainWindow
    p = Project.new_workspace("seleccion_pagina")
    p.score = _placed_score()
    p.original_score = p.score.clone()
    p.workspace.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(p.workspace / "pagina.png"), np.full((1400, 1000, 3), 255, np.uint8))
    p.pages = [PageRecord(0, "Página 1", original_file="pagina.png", processed_file="pagina.png",
                          original_size=(1000, 1400), processed_size=(1000, 1400))]
    revalidate_project(p, AppConfig())
    c = AppController(AppConfig())
    c.player._silent = True
    w = MainWindow(c)
    w.setAttribute(Qt.WA_DontShowOnScreen)
    w.resize(1400, 900)
    w.show()
    c.set_project(p)
    w._update_steps()
    w.go_step(2)
    qapp_offscreen.processEvents()
    rv = w.stack.currentWidget()
    view = rv.view
    in_m2 = [n for n in p.score.notes if n.measure == 1 and not n.rest]
    spots = {}
    for n in in_m2:
        spots.setdefault((round(n.position.x, 4), round(n.position.y, 4)), []).append(n)
    notes = [v[0] for v in spots.values() if len(v) == 1][:2]   # notas con un sitio propio en la página
    focus_before = rv.focus_measure
    # clic 4 píxeles por debajo de la nota (fuera de su recuadro): la selecciona, no cambia de compás
    r = view.mapFromScene(view._markers[notes[0].id].sceneBoundingRect()).boundingRect()
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(r.center().x(), r.bottom() + 4))
    r2 = view.mapFromScene(view._markers[notes[1].id].sceneBoundingRect()).boundingRect()
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ControlModifier, r2.center())
    qapp_offscreen.processEvents()
    assert set(c.selection) == {notes[0].id, notes[1].id}
    assert rv.focus_measure == focus_before
    # Mayús + arrastrar sobre todo el compás 2: selecciona todas sus notas
    mp = p.score.measures[1].position
    tl = view.mapFromScene(view.norm_rect(mp.x, mp.y, mp.w, mp.h).topLeft())
    br = view.mapFromScene(view.norm_rect(mp.x, mp.y, mp.w, mp.h).bottomRight())
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.ShiftModifier, tl)
    QTest.mouseMove(view.viewport(), br)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.ShiftModifier, br)
    qapp_offscreen.processEvents()
    assert set(c.selection) == {n.id for n in p.score.notes_in_measure(1) if n.position is not None}
    c.history.clear()
    p.dirty = False
    w.close()


def test_several_figures_read_too_long_are_found_by_their_spacing():
    from note_cascade.music_validation.repairs import repair_duration_by_spacing
    s = S.piano_score(measures=1)
    s.measures[0].time_sig = (12, 8)
    s.measures[0].position = PagePosition(0, 0.05, 0.2, 0.40, 0.12)
    s.notes = []
    # negra + 10 corcheas (como el c. 16 de «The Pool»); dos corcheas se leyeron como negras
    written = [F(1)] + [F(1, 2)] * 10
    read = list(written)
    read[2] = read[7] = F(1)
    x, t = 0.06, F(0)
    for k, (w, r) in enumerate(zip(written, read)):
        n = S._note(s, 0, t, r, "E3", staff=2, voice=5)
        n.position = PagePosition(0, x, 0.3, 0.01, 0.01)
        x += 0.032 * (float(w) / 0.5) ** 0.6
        t += r
    s.sort_notes()
    issues = repair_duration_by_spacing(s)
    assert [i.code for i in issues] == ["figura_espaciado"]
    got = [(n.onset, n.duration) for n in sorted(s.notes, key=lambda n: n.onset)]
    expected, t = [], F(0)
    for w in written:
        expected.append((t, w))
        t += w
    assert got == expected


def test_false_fermata_is_dropped_when_the_other_engine_sees_all_the_others():
    from note_cascade.music_validation.compare import align
    from note_cascade.music_validation.fixes import apply_fix
    from note_cascade.music_validation.repairs import repair_fermatas_with_secondary
    primary = S.piano_score(measures=8)
    for mi in (3, 7):
        next(n for n in primary.notes if n.measure == mi).articulations.append("fermata")
    secondary = primary.clone()
    fake = next(n for n in primary.notes if n.measure == 1 and n.staff == 2)
    fake.articulations.append("fermata")            # solo el motor principal lo ve
    issues = repair_fermatas_with_secondary(primary, secondary, align(primary, secondary), "HOMR")
    assert [i.measure for i in issues] == [1] and "fermata" not in fake.articulations
    assert sum("fermata" in n.articulations for n in primary.notes) == 2, "los calderones confirmados se quedan"
    apply_fix(primary, issues[0].fix)
    assert "fermata" in fake.articulations, "se puede volver a poner"


def test_fermatas_are_kept_if_the_other_engine_does_not_see_fermatas():
    from note_cascade.music_validation.compare import align
    from note_cascade.music_validation.repairs import repair_fermatas_with_secondary
    primary = S.piano_score(measures=4)
    secondary = primary.clone()
    next(n for n in primary.notes if n.measure == 1).articulations.append("fermata")
    assert repair_fermatas_with_secondary(primary, secondary, align(primary, secondary), "HOMR") == []
