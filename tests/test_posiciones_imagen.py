"""Regresión: situar en la imagen las notas sin posición exacta, no proponer quitar notas que sí están,
alteraciones leídas como notas, armaduras «de cortesía» que no son compases y la figura del metrónomo.

Casos reales de «The Pool»: c. 44 (dos Sol♯3 con puntillo que se proponía quitar), c. 24 (el ♮ de Do5
leído como otra nota), indicaciones «♩. = 65» y armadura de cortesía tras la doble barra del c. 44."""

from __future__ import annotations

from fractions import Fraction as F
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import synthetic as S
from note_cascade.image_processing.staff_detection import PageGeometry, Staff, System
from note_cascade.music_model.model import KeyChange, Measure, PagePosition, PartInfo, Score, ClefChange, TempoMark
from note_cascade.omr import image_check as IC
from note_cascade.omr.positions import drop_courtesy_span, x_for_onset

W, H = 1000, 400
IL = 10.0


def _page() -> IC._Page:
    """Página de 1000×400 con un sistema de piano: pentagrama 1 (líneas y = 100…140) y 2 (y = 200…240)."""
    st1 = Staff([(100 + 10 * i, 0.0) for i in range(5)], 0.0, 1000.0, IL, 1.0)
    st2 = Staff([(200 + 10 * i, 0.0) for i in range(5)], 0.0, 1000.0, IL, 1.0)
    geom = PageGeometry(W, H, IL, 1.0, [st1, st2], [System([0, 1], 0.0, 1000.0, 100.0, 240.0, [1000.0])])
    eye = np.eye(3)
    return IC._Page(SimpleNamespace(index=0), geom, np.zeros((H, W), np.float32), eye, eye, (W, H))


def _score() -> Score:
    s = Score()
    s.parts.append(PartInfo("P1", "Piano", "Pno.", 2, 0, True))
    s.measures.append(Measure(0, "1", (4, 4), time_sig_change=True))
    s.measures[0].position = PagePosition(0, 0.0, 0.25, 1.0, 0.35)
    s.keys.append(KeyChange(0, 0, 0))
    s.clefs.append(ClefChange(0, 1, 0, F(0), "G", 2))
    s.clefs.append(ClefChange(0, 2, 0, F(0), "F", 4))
    return s


def _place(n, x: float, y: float, approximate: bool = False) -> None:
    n.position = PagePosition(0, (x - 6) / W, (y - 5) / H, 12 / W, 10 / H, approximate)


def _head(x: float, y: float, score: float = 0.9) -> IC.Head:
    return IC.Head(0, "negra", x, y, 12.0, 10.0, score)


def test_estimated_x_comes_from_notes_of_the_same_instant():
    anchors = [(0.0, 100.0), (1.5, 400.0), (3.0, 520.0)]
    ends = [(0.0, 80.0), (4.0, 1000.0)]
    assert x_for_onset(anchors, ends, 1.5) == 400.0            # otra nota del mismo instante
    assert x_for_onset(anchors, ends, 2.25) == pytest.approx(460.0)   # entre vecinos, no proporcional
    assert 520.0 < x_for_onset(anchors, ends, 3.5) < 1000.0
    assert x_for_onset([], ends, 2.0) == pytest.approx(540.0)   # sin notas localizadas: proporcional


def test_weak_head_at_the_right_instant_and_pitch_is_used_not_deleted():
    """c. 44 de «The Pool»: la cabeza colgada de una línea adicional coincide peor con el modelo (0.69)."""
    s, pg = _score(), _page()
    rh = [S._note(s, 0, 0, 1, "E4"), S._note(s, 0, 2, 1, "B4"), S._note(s, 0, 3, 1, "D5")]
    for n, x, y in zip(rh, (100, 600, 800), (140, 120, 110)):
        _place(n, x, y)
    lh = S._note(s, 0, 1, 1, "C3", staff=2)
    _place(lh, 400, 225)
    lost = S._note(s, 0, 1, 1, "G4")
    _place(lost, 300, 130, approximate=True)          # estimación lineal, corrida a la izquierda
    heads = {0: [_head(100, 140), _head(600, 120), _head(800, 110), _head(400, 225), _head(400, 130, score=0.68)]}
    for h in heads[0][:4]:
        h.used = True
    assert IC._relocate(s, {0: pg}, heads) == 1
    x0, _y0, x1, _y1 = pg.to_proc(lost.position)
    assert (x0 + x1) / 2 == pytest.approx(400) and lost.image_verified and not lost.position.approximate
    assert not IC._unlocated_notes(s, {0: pg}, heads)


def test_note_with_an_unexplained_head_at_its_instant_is_not_proposed_for_deletion():
    s, pg = _score(), _page()
    for name, onset, x, y in (("E4", 0, 100, 140), ("B4", 2, 600, 120), ("D5", 3, 800, 110)):
        _place(S._note(s, 0, onset, 1, name), x, y)
    _place(S._note(s, 0, 1, 1, "C3", staff=2), 400, 225)
    lost = S._note(s, 0, 1, 1, "A4")                  # el motor leyó La4; en la imagen la cabeza está en Sol4
    _place(lost, 300, 125, approximate=True)
    heads = {0: [_head(400, 130, score=0.66)]}
    issues = IC._unlocated_notes(s, {0: pg}, heads)
    assert [i.code for i in issues] == ["nota_sin_confirmar"]
    assert issues[0].fix.action == "set_pitch" and issues[0].fix.params["midi"] == 67   # Sol4
    # sin ninguna cabeza en ese instante sí se propone quitarla (y nunca se quita sola)
    issues = IC._unlocated_notes(s, {0: pg}, {0: []})
    assert [i.code for i in issues] == ["nota_sin_imagen"] and issues[0].fix.action == "delete_notes"
    assert lost in s.notes


def test_accidental_read_as_a_note_is_removed_and_can_be_restored():
    """c. 24 de «The Pool»: el ♮ delante de Do5 leído como «Do♯5»."""
    from note_cascade.music_validation.fixes import apply_fix
    s, pg = _score(), _page()
    phantom = S._note(s, 0, 0, 3, "C#5")
    _place(phantom, 380, 115)
    real = S._note(s, 0, 0, 3, "C5")
    real.accidental_shown = "natural"
    real.image_verified = True
    _place(real, 395, 115)
    other = S._note(s, 0, 0, 3, "G4")
    other.image_verified = True
    _place(other, 395, 130)
    issues = IC._accidentals_read_as_notes(s, {0: pg}, {0: [_head(395, 115), _head(395, 130)]})
    assert [i.code for i in issues] == ["alteracion_como_nota"]
    assert phantom not in s.notes and real in s.notes and other in s.notes
    apply_fix(s, issues[0].fix)
    assert any(n.id == phantom.id for n in s.notes)


def test_two_noteheads_on_the_same_spot_are_not_touched():
    s, pg = _score(), _page()
    a = S._note(s, 0, 0, 1, "C5")
    _place(a, 380, 115)
    b = S._note(s, 0, 0, 1, "C#5")
    b.image_verified = True
    _place(b, 395, 115)
    assert not IC._accidentals_read_as_notes(s, {0: pg}, {0: [_head(380, 115), _head(395, 115)]})
    assert a in s.notes


def test_courtesy_key_signature_after_the_last_barline_is_not_a_measure():
    s = S.piano_score(measures=3, fifths=4)
    s.keys.append(KeyChange(0, 2, -1))            # el c. 3 cambia a un bemol: armadura de cortesía al final del 2
    spans = [(0.0, 960.0), (960.0, 980.0)]
    assert drop_courtesy_span(s, spans, 1, IL, 1) == [(0.0, 960.0)]
    assert drop_courtesy_span(s, spans, 0, IL, 1) == spans          # sin cambio después: no se toca
    assert drop_courtesy_span(s, [(0.0, 900.0), (900.0, 990.0)], 1, IL, 1) == [(0.0, 900.0), (900.0, 990.0)]


def test_audiveris_stack_without_notes_is_not_a_measure():
    from note_cascade.omr import audiveris_omr as A
    system = A.OmrSystem([A.OmrStack(139, 2355), A.OmrStack(2355, 2403)], [],
                         [A.OmrInter("head", "NOTEHEAD_BLACK", 400, 100, 20, 18, 0.9, 0.9, 1, 0.0)])
    page = A.OmrPage(5, 2550, 3300, 25.0, [system])
    assert [st.left for st in A.musical_stacks(page, system)] == [139]


def _metronome(unit: str, dots: int) -> np.ndarray:
    img = np.full((400, 800), 255, np.uint8)
    cv2.putText(img, "65", (400, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    cv2.rectangle(img, (372, 200), (390, 202), 0, -1)
    cv2.rectangle(img, (372, 208), (390, 210), 0, -1)
    cv2.ellipse(img, (330, 212), (8, 6), -20, 0, 360, 0, 2 if unit == "half" else -1)
    cv2.line(img, (337, 210), (337, 168), 0, 2)
    if unit == "eighth":
        cv2.line(img, (337, 168), (348, 185), 0, 3)
    for k in range(dots):
        cv2.circle(img, (348 + 7 * k, 212), 2, 0, -1)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)


@pytest.mark.parametrize("unit", ["quarter", "half", "eighth"])
@pytest.mark.parametrize("dots", [0, 1])
def test_drawn_metronome_figure_is_read_from_the_image(unit, dots):
    from note_cascade.layout.metronome_figure import read_beat_figure
    assert read_beat_figure(_metronome(unit, dots), 400 / 800, 180 / 400, 462 / 800, 222 / 400) == (unit, dots)


def test_tempo_from_text_uses_the_figure_seen_in_the_image():
    from note_cascade.layout.integrate import _tempo_from_text
    from note_cascade.layout.text_classifier import TextElement
    s = S.piano_score(measures=2)
    s.tempos.clear()
    s.measures[0].time_sig = (12, 8)
    s.measures[0].position = PagePosition(0, 0.05, 0.1, 0.9, 0.2)
    e = TextElement("65", 0, 0.1, 0.05, 0.12, 0.07, "pdf", "tempo", value="65")
    e.beat_figure = ("quarter", 0)            # «♩ = 65» aunque el compás sea 12/8
    issues = _tempo_from_text(s, e, (0, F(0)))
    assert s.tempos[0].bpm_quarter == 65 and (s.tempos[0].beat_unit, s.tempos[0].beat_dots) == ("quarter", 0)
    assert issues[0].evidence == "comprobado" and issues[0].fix is None
    s.tempos.clear()
    e.beat_figure = ("quarter", 1)            # «♩. = 65»
    issues = _tempo_from_text(s, e, (0, F(0)))
    assert s.tempos[0].bpm_quarter == pytest.approx(97.5) and issues[0].evidence == "comprobado"


def test_changing_a_tempo_also_changes_the_written_figure(tmp_path):
    """Antes, «Cambiar a negra = 65» dejaba «♩. = 65» escrito en el MusicXML pero sonaba ♩ = 65."""
    from note_cascade.music_validation.fixes import apply_fix
    from note_cascade.music_validation.issues import Fix
    from note_cascade.musicxml.reader import read_musicxml
    from note_cascade.musicxml.writer import write_musicxml
    s = S.piano_score(measures=2)
    s.tempos[:] = [TempoMark(0, F(0), 97.5, text="= 65", beat_unit="quarter", beat_dots=1, per_minute=65,
                             source="texto")]
    apply_fix(s, Fix("Usar negra = 65", "set_tempo", {"measure": 0, "onset": "0", "bpm": 65.0, "unit": "quarter",
                                                       "dots": 0, "per_minute": 65.0}))
    t = s.tempos[0]
    assert (t.bpm_quarter, t.beat_unit, t.beat_dots, t.per_minute) == (65.0, "quarter", 0, 65.0)
    path = tmp_path / "tempo.musicxml"
    write_musicxml(s, path)
    back = read_musicxml(path).score.tempos[0]
    assert back.bpm_quarter == pytest.approx(65.0) and back.beat_dots == 0


def test_rearticulation_note_has_its_own_title():
    from note_cascade.music_validation.checks import check_repeats
    s = S.piano_score(measures=1)
    issues = check_repeats(s, ["23 nota(s) sostenida(s) se sueltan justo antes de que se vuelva a tocar la misma "
                               "tecla (con la misma mano o con la otra), como en un piano real."])
    assert [(i.code, i.title) for i in issues] == [("rearticulacion", "Notas que se vuelven a tocar")]
