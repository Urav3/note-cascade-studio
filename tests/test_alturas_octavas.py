"""Regresión: alturas reales de las notas (claves, líneas adicionales, indicaciones de octava) desde el
pentagrama hasta el MIDI exportado y la tecla de la cascada. Todas las alturas esperadas son conocidas
(Do central = MIDI 60)."""

from __future__ import annotations

from fractions import Fraction as F

import mido
import pytest

import synthetic as S
from note_cascade.image_processing.staff_detection import Staff
from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import ClefChange, Direction, PagePosition
from note_cascade.music_model.timeline import build_performance
from note_cascade.omr import image_check as IC
from note_cascade.omr.octave_lines import OctaveLine, apply_chains, chain_lines, label_kind, resolve_octaves
from note_cascade.piano_roll.keyboard import key_layout

# Pentagrama de prueba: líneas cada 10 píxeles, línea central en y = 120 (medio espacio = 5 píxeles)
STAFF = Staff([(100 + 10 * i, 0.0) for i in range(5)], 0.0, 1000.0, 10.0, 1.0)


def _y(half_spaces: int) -> float:
    """Altura en la imagen de una cabeza a ``half_spaces`` medios espacios por encima de la línea central."""
    return 120 - 5 * half_spaces


def _written_midi(score, staff_no: int, half_spaces: int, measure: int = 0) -> int:
    d = IC.written_diatonic(score, 0, staff_no, measure, F(0), STAFF, 500, _y(half_spaces))
    step, octave = P.from_diatonic_index(d)
    return P.midi_from(step, 0, octave)


@pytest.fixture()
def piano():
    s = S.piano_score(measures=4, fifths=0)
    return s


# 1) clave de sol sin octava · 5) clave de fa con líneas adicionales abajo · 6) varias líneas arriba
@pytest.mark.parametrize("staff_no,half_spaces,midi", [
    (1, 0, 71),     # clave de sol, línea central: Si4
    (1, -6, 60),    # una línea adicional por debajo: Do4 (Do central)
    (1, 6, 81),     # una línea adicional por encima: La5
    (1, 13, 93),    # cuatro líneas adicionales por encima (espacio): La6 (c. 37 de «Sinner's Finale»)
    (2, 0, 50),     # clave de fa, línea central: Re3
    (2, 6, 60),     # una línea adicional por encima: Do4
    (2, -6, 40),    # una línea adicional por debajo: Mi2
    (2, -12, 29),   # cuatro líneas adicionales por debajo: Fa1 (c. 37 de «Sinner's Finale»)
    (2, -9, 35),    # Si1 natural (con la armadura de «Sinner's Finale» sería Si♭1 = 34)
])
def test_written_pitch_from_clef_and_staff_position(piano, staff_no, half_spaces, midi):
    assert _written_midi(piano, staff_no, half_spaces) == midi


def test_clef_change_inside_the_staff_changes_the_written_pitch(piano):
    piano.clefs.append(ClefChange(0, 2, 2, F(0), "G", 2))      # la izquierda pasa a clave de sol en el c. 3
    assert _written_midi(piano, 2, 6, measure=1) == 60         # aún en clave de fa: Do4
    assert _written_midi(piano, 2, 6, measure=2) == 81         # ya en clave de sol: La5


# 7) acorde · 8) secuencia grave ascendente: cada nota más aguda que la anterior, sin plegarse
def test_low_ascending_sequence_and_chord_keep_their_register(piano):
    seq = [_written_midi(piano, 2, hs) for hs in (-12, -9, -6, -3, 0, 3, 6, 9)]
    assert seq == [29, 35, 40, 45, 50, 55, 60, 65]          # Fa1 Si1 Mi2 La2 Re3 Sol3 Do4 Fa4
    chord = {_written_midi(piano, 1, hs) for hs in (-6, -4, -1, 8)}
    assert chord == {60, 64, 69, 84}                        # Do4 Mi4 La4 Do6


# ---------------------------------------------------------------------------
# Indicaciones de octava
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,semis", [
    ("8va", 12), ("8va---", 12), ("(8va)", 12), ("8vb", -12), ("8va bassa", -12), ("15ma", 24), ("15mb", -24),
    ("8", 0), ("Ped.", 0), ("rit.", 0),
])
def test_octave_labels(text, semis):
    assert label_kind(text) == semis


def _placed(score):
    """Posiciones en la página: 4 compases en un sistema (página 0) y los 4 siguientes en otro (página 1)."""
    for m in score.measures:
        m.position = PagePosition(m.index // 4, 0.1 + (m.index % 4) * 0.2, 0.2, 0.2, 0.15)
    for n in score.notes:
        mp = score.measures[n.measure].position
        n.position = PagePosition(mp.page, mp.x + 0.02 + float(n.onset) / 4 * 0.16, 0.22 if n.staff == 1 else 0.3,
                                  0.01, 0.01)
    return {m.index: (m.index // 4, 0) for m in score.measures}


@pytest.mark.parametrize("semis", [12, -12, 24])
def test_octave_line_applies_once_only_to_its_staff_and_range(semis):
    s = S.piano_score(measures=8)
    measure_sys = _placed(s)
    before = {n.id: n.midi for n in s.notes}
    # 2–4) 8va/8vb/15ma sobre el pentagrama 1 desde el c. 2 hasta mitad del c. 3 (9: termina a mitad de pieza)
    line = OctaveLine(0, 0, 1, semis, x0=0.29, x1=0.42)
    chains = chain_lines([line], sorted(set(measure_sys.values())))
    apply_chains(s, chains, measure_sys, written_diatonic={})
    for n in s.notes:
        if n.rest:
            continue
        cx = n.position.x + n.position.w / 2
        inside = n.position.page == 0 and n.staff == 1 and 0.29 - 0.008 <= cx <= 0.42 + 0.012
        assert n.midi - before[n.id] == (semis if inside else 0), (n.measure, n.staff, n.onset)
    assert any(not n.rest and n.staff == 2 and n.measure in (1, 2) for n in s.notes), "hay notas de la otra mano"
    # aplicar otra vez la misma cadena no vuelve a transponer (la nota se transpone una sola vez)
    once = {n.id: n.midi for n in s.notes}
    done = {n.id for n in s.notes if n.midi != before[n.id]}
    apply_chains(s, chains, measure_sys, written_diatonic={}, already=done)
    assert {n.id: n.midi for n in s.notes} == once


def test_octave_line_continues_across_a_page_break():
    # 10) la línea llega al final del sistema de la página 0 y sigue al principio del de la página 1
    s = S.piano_score(measures=8)
    measure_sys = _placed(s)
    before = {n.id: n.midi for n in s.notes}
    a = OctaveLine(0, 0, 1, 12, x0=0.5, x1=0.9, reaches_end=True)
    b = OctaveLine(1, 0, 1, 0, x0=0.1, x1=0.35, starts_at_start=True)        # continuación sin rótulo
    chains = chain_lines([a, b], sorted(set(measure_sys.values())))
    assert len(chains) == 1 and len(chains[0].lines) == 2
    apply_chains(s, chains, measure_sys, written_diatonic={})
    moved = {(n.measure, n.staff) for n in s.notes if not n.rest and n.midi != before[n.id]}
    assert {m for m, st in moved} == {2, 3, 4, 5} and {st for _m, st in moved} == {1}


def test_dashed_line_without_label_that_continues_nothing_is_ignored():
    s = S.piano_score(measures=8)
    measure_sys = _placed(s)
    lone = OctaveLine(1, 0, 1, 0, x0=0.1, x1=0.35, starts_at_start=True)    # p. ej. «rit. - - -»
    assert chain_lines([lone], sorted(set(measure_sys.values()))) == []


def test_engine_pitches_that_already_sound_are_not_transposed_again():
    # 11) si la imagen demuestra que el motor ya dio la altura sonora (una octava sobre lo escrito), no se toca
    s = S.piano_score(measures=4)
    measure_sys = _placed(s)
    covered = [n for n in s.notes if not n.rest and n.staff == 1 and n.measure == 1]
    written = {n.id: P.diatonic_index(n.step, n.octave) - 7 for n in covered}     # la cabeza está una octava abajo
    before = {n.id: n.midi for n in s.notes}
    issues = apply_chains(s, chain_lines([OctaveLine(0, 0, 1, 12, x0=0.29, x1=0.5)], [(0, 0), (1, 0)]),
                          measure_sys, written_diatonic=written)
    assert all(n.midi == before[n.id] for n in s.notes)
    assert any("ya aplicada por el motor" in i.title for i in issues)


def test_engine_octave_mark_without_end_or_image_evidence_is_never_applied_blindly(tmp_path):
    # Audiveris escribe solo el inicio de la octava (sin fin) y con alturas escritas: no se aplica a ciegas
    from note_cascade.project.project import Project
    s = S.piano_score(measures=4)
    s.directions.append(Direction("octave_shift", 1, F(0), 0, 2, "up", size=8, source="audiveris"))
    before = {n.id: n.midi for n in s.notes}
    p = Project.new_workspace("octava_motor")
    p.score = s
    issues = resolve_octaves(s, p)
    assert all(n.midi == before[n.id] for n in s.notes), "ninguna nota de la mano izquierda se desplaza"
    assert [i.code for i in issues] == ["octava_motor_dudosa"]
    assert not any(d.kind == "octave_shift" for d in s.directions)


# ---------------------------------------------------------------------------
# 12) MIDI exportado, reproductor y cascada con los mismos números MIDI
# ---------------------------------------------------------------------------

def test_exported_midi_player_and_keyboard_keep_the_same_pitches(tmp_path):
    from note_cascade.midi.writer import write_midi
    s = S.piano_score(measures=4)
    for n in s.notes:                      # registro amplio: graves con líneas adicionales y agudos
        if not n.rest and n.staff == 2:
            n.midi -= 24
        elif not n.rest:
            n.midi += 12
    perf = build_performance(s)
    path = tmp_path / "alturas.mid"
    write_midi(s, perf, path)
    mid = mido.MidiFile(str(path))
    exported = sorted(msg.note for tr in mid.tracks for msg in tr if msg.type == "note_on" and msg.velocity > 0)
    played = sorted(p.midi for p in perf.notes if not p.silent)
    assert exported == played == sorted(n.midi for n in s.notes if not n.rest)
    layout = key_layout(21, 108, 0.0, 1000.0)
    xs = [layout[m].x + layout[m].w / 2 for m in range(21, 109)]
    whites = [x for m, x in zip(range(21, 109), xs) if not P.is_black_key(m)]
    assert whites == sorted(whites), "las teclas blancas avanzan de grave a agudo"
    assert all(layout[m].x < layout[m + 12].x for m in range(21, 97)), "una octava más aguda está más a la derecha"
    narrow = key_layout(48, 72, 0.0, 1000.0)
    assert narrow.get(36) is None and narrow.get(84) is None, "fuera del rango visible no se pliega a otra octava"


def test_musicxml_round_trip_keeps_sounding_pitch_under_an_applied_octave(tmp_path):
    from note_cascade.musicxml.reader import read_musicxml
    from note_cascade.musicxml.writer import write_musicxml
    s = S.piano_score(measures=8)
    measure_sys = _placed(s)
    apply_chains(s, chain_lines([OctaveLine(0, 0, 1, 12, x0=0.29, x1=0.5)], [(0, 0), (1, 0)]), measure_sys, {})
    path = tmp_path / "octava.musicxml"
    write_musicxml(s, path)
    back = read_musicxml(path).score
    assert sorted(n.midi for n in back.notes if not n.rest) == sorted(n.midi for n in s.notes if not n.rest)
    assert any(d.kind == "octave_shift" and d.value == "down" for d in back.directions)


def test_held_key_is_released_before_the_other_hand_strikes_it(tmp_path):
    """Una tecla sostenida no puede volver a tocarse sin soltarla (c. 64 de «The Pool»)."""
    from note_cascade.midi.writer import write_midi
    s = S.piano_score(measures=2)
    s.notes = []
    held = S._note(s, 0, 2, 2, "G#4", staff=1)                  # blanca en la derecha
    S._note(s, 0, F(5, 2), F(1, 2), "G#4", staff=2, voice=5)     # la izquierda la toca mientras suena
    S._note(s, 0, 3, F(1, 2), "G#4", staff=2, voice=5)
    s.sort_notes()
    perf = build_performance(s)
    g = sorted((p for p in perf.notes if p.midi == held.midi), key=lambda p: p.start_q)
    assert [p.start_q for p in g] == [2, 2.5, 3]
    assert all(a.start_q + a.dur_q <= b.start_q for a, b in zip(g, g[1:])), "nunca dos notas a la vez en la tecla"
    assert g[0].start_q + g[0].dur_q < 2.5, "la blanca se suelta antes del golpe de la otra mano"
    assert held.duration == 2, "la partitura conserva lo escrito"
    path = tmp_path / "tecla.mid"
    write_midi(s, perf, path)
    sounding, overlap = 0, False
    events = []
    t = 0
    for msg in mido.merge_tracks(mido.MidiFile(str(path)).tracks):
        t += msg.time
        if msg.type in ("note_on", "note_off") and msg.note == held.midi:
            events.append((t, msg.type == "note_on" and msg.velocity > 0))
    for _t, on in sorted(events, key=lambda e: (e[0], e[1])):
        sounding += 1 if on else -1
        overlap |= sounding > 1
    assert not overlap, "el MIDI exportado nunca tiene la misma tecla pulsada dos veces a la vez"


def test_fix_preview_shows_before_and_after_without_touching_the_score():
    from note_cascade.music_validation.issues import Fix
    from note_cascade.music_validation.preview import preview_fix
    s = S.piano_score(measures=4)
    before = [(n.id, n.midi) for n in s.notes]
    fix = Fix("Desplazar +12 semitonos", "octave_shift",
              {"part": 0, "staff": 1, "from_measure": 1, "to_measure": 1, "semitones": 12})
    pv = preview_fix(s, fix)
    assert [(n.id, n.midi) for n in s.notes] == before, "la partitura abierta no cambia"
    changed = [n for n in s.notes if n.measure == 1 and n.staff == 1 and not n.rest]
    assert {n.id for n in pv.removed} == {n.id for n in changed}
    assert sorted(n.midi for n in pv.added) == sorted(n.midi + 12 for n in changed)
    assert pv.measures == [1] and all("→" in line for line in pv.lines)


@pytest.mark.gui
def test_review_shows_the_preview_of_the_selected_correction(qapp_offscreen):
    from PySide6.QtCore import Qt
    from note_cascade.app.config import AppConfig
    from note_cascade.music_validation.issues import Fix, Issue
    from note_cascade.pipeline.conversion import revalidate_project
    from note_cascade.project.project import Project
    from note_cascade.ui.controller import AppController
    from note_cascade.ui.main_window import MainWindow
    p = Project.new_workspace("vista_previa")
    p.score = S.piano_score(measures=4)
    p.original_score = p.score.clone()
    revalidate_project(p, AppConfig())
    p.issues.append(Issue("aviso", "altura", "Prueba", "x", measure=1, code="prueba",
                          fix=Fix("Desplazar +12 semitonos", "octave_shift",
                                  {"part": 0, "staff": 1, "from_measure": 1, "to_measure": 1, "semitones": 12})))
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
    panel = rv.issues
    panel.filter.setCurrentIndex(5)   # todos
    panel.refresh()
    item = next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
                if panel.tree.topLevelItem(i).data(0, Qt.UserRole).code == "prueba")
    panel.tree.setCurrentItem(item)
    qapp_offscreen.processEvents()
    assert rv.editor.preview_add and rv.editor.preview_remove
    assert "Qué cambiaría" in panel.detail.toPlainText() and panel.b_after.isEnabled()
    before = [n.midi for n in p.score.notes]
    panel.ck_preview.setChecked(False)
    assert not rv.editor.preview_add
    assert [n.midi for n in p.score.notes] == before
    c.history.clear()
    p.dirty = False
    w.close()
