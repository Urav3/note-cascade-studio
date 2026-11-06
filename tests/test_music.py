"""Modelo musical, MusicXML, interpretación temporal y MIDI."""

from __future__ import annotations

from collections import Counter
from fractions import Fraction as F

import mido
import pytest

import synthetic as S
from note_cascade.midi.reader import read_midi
from note_cascade.midi.verify import read_midi_notes, verify_midi
from note_cascade.midi.writer import write_midi
from note_cascade.music_model import pitch as P
from note_cascade.music_model.hands import assign_hands
from note_cascade.music_model.model import Direction, Measure, TempoMark
from note_cascade.music_model.timeline import PerformanceOptions, build_performance, play_order
from note_cascade.musicxml.reader import read_musicxml, read_musicxml_bytes
from note_cascade.musicxml.writer import write_musicxml


def test_pitch_utilities():
    assert P.midi_from("C", 0, 4) == 60
    assert P.midi_from("B", 1, 3) == 60
    assert P.spell_midi(66, 1) == ("F", 1, 4)
    assert P.spell_midi(70, -2) == ("B", -1, 4)
    assert P.note_name("C", 1, 4) == "Do♯4"
    assert P.staff_position("B", 4, "G", 2) == 0
    assert P.staff_position("D", 3, "F", 4) == 0
    assert P.staff_position("F", 5, "G", 2) == -4  # quinta línea


def test_musicxml_round_trip_matches_music21(tmp_path):
    music21 = pytest.importorskip("music21")
    score = S.piano_score(lyrics=True)
    path = write_musicxml(score, tmp_path / "prueba.musicxml")
    # Lectura independiente con music21
    m21 = music21.converter.parse(str(path))
    events = []
    for part in m21.parts:
        for n in part.flatten().notes:
            for p in (n.pitches if n.isChord else [n.pitch]):
                events.append((float(n.getOffsetInHierarchy(part)), float(n.quarterLength), int(p.midi)))
    ours = []
    starts = [0.0]
    for m in score.measures:
        starts.append(starts[-1] + float(m.expected_duration()))
    for n in score.notes:
        if not n.rest:
            ours.append((starts[n.measure] + float(n.onset), float(n.duration), n.midi))
    assert Counter(events) == Counter(ours)
    # Y vuelta al modelo con el lector propio
    back = read_musicxml(path).score
    assert S.note_events(back) == S.note_events(score)
    assert back.title == score.title and back.composer == score.composer
    assert [t.bpm_quarter for t in back.tempos if t.bpm_quarter] == [96.0]


def test_mxl_compressed_round_trip(tmp_path):
    score = S.melody_score()
    path = write_musicxml(score, tmp_path / "m.mxl")
    back = read_musicxml(path).score
    assert S.note_events(back) == S.note_events(score)
    assert back.key_at(0, 0) == -1 and back.measures[0].time_sig == (3, 4)


MXL_TIES_TUPLETS = b"""<?xml version="1.0"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
<part id="P1">
<measure number="1"><attributes><divisions>6</divisions><key><fifths>0</fifths></key><time><beats>2</beats><beat-type>4</beat-type></time>
<staves>2</staves><clef number="1"><sign>G</sign><line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef></attributes>
<direction placement="above"><direction-type><words>Allegro</words></direction-type><sound tempo="132"/></direction>
<direction><direction-type><dynamics><f/></dynamics></direction-type><staff>1</staff></direction>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff></note>
<note><pitch><step>D</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff></note>
<note><pitch><step>E</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><staff>1</staff></note>
<note><pitch><step>G</step><octave>5</octave></pitch><duration>6</duration><tie type="start"/><voice>1</voice><type>quarter</type><staff>1</staff><notations><tied type="start"/></notations></note>
<backup><duration>12</duration></backup>
<note><pitch><step>C</step><octave>3</octave></pitch><duration>12</duration><voice>5</voice><type>half</type><staff>2</staff></note>
<note><chord/><pitch><step>G</step><octave>3</octave></pitch><duration>12</duration><voice>5</voice><type>half</type><staff>2</staff></note>
</measure>
<measure number="2">
<note><pitch><step>G</step><octave>5</octave></pitch><duration>6</duration><tie type="stop"/><voice>1</voice><type>quarter</type><staff>1</staff><notations><tied type="stop"/><fermata/></notations></note>
<note><rest/><duration>6</duration><voice>1</voice><type>quarter</type><staff>1</staff></note>
<backup><duration>12</duration></backup>
<direction><direction-type><pedal type="start"/></direction-type><staff>2</staff></direction>
<note><pitch><step>F</step><alter>1</alter><octave>2</octave></pitch><duration>9</duration><voice>5</voice><type>quarter</type><dot/><accidental>sharp</accidental><staff>2</staff></note>
<note><pitch><step>G</step><octave>2</octave></pitch><duration>3</duration><voice>5</voice><type>eighth</type><staff>2</staff></note>
<direction><direction-type><pedal type="stop"/></direction-type><staff>2</staff></direction>
</measure></part></score-partwise>"""


def test_ties_tuplets_chords_pedal_fermata_in_performance(tmp_path):
    rr = read_musicxml_bytes(MXL_TIES_TUPLETS)
    s = rr.score
    assign_hands(s)
    assert s.parts[0].is_piano
    triplets = [n for n in s.notes if n.tuplet]
    assert [n.duration for n in triplets] == [F(1, 3)] * 3
    assert s.tempos[0].bpm_quarter == 132 and s.tempos[0].text == "Allegro"
    perf = build_performance(s, PerformanceOptions())
    g5 = [n for n in perf.notes if n.midi == 79]
    assert len(g5) == 1, "la ligadura de prolongación no debe producir un segundo ataque"
    assert g5[0].dur_q == pytest.approx(2.0)
    lh = sorted((n.start_q, n.midi) for n in perf.notes if n.hand == "L")
    assert (0.0, 48) in lh and (0.0, 55) in lh, "el acorde de la mano izquierda suena a la vez"
    assert any(v == 127 for _q, v, _p in perf.pedal_events) and any(v == 0 for _q, v, _p in perf.pedal_events)
    assert perf.bpm_at(2.1) == pytest.approx(66), "el calderón alarga (tempo a la mitad)"
    vel = {n.midi: n.velocity for n in perf.notes}
    assert vel[72] >= 90, "forte se traduce a intensidad alta"
    out = tmp_path / "t.mid"
    res = write_midi(s, perf, out, 1)
    v = verify_midi(out, res.expected_notes)
    assert v.ok, v.message
    mid, notes = read_midi_notes(out)
    assert any(msg.type == "control_change" and msg.control == 64 for t in mid.tracks for msg in t)
    assert [t.name for t in mid.tracks if t.name][1:] == ["Piano - Mano derecha", "Piano - Mano izquierda"]


def test_midi_type0_and_type1_contain_same_notes(tmp_path):
    s = S.piano_score()
    perf = build_performance(s)
    r1 = write_midi(s, perf, tmp_path / "a.mid", 1)
    r0 = write_midi(s, perf, tmp_path / "b.mid", 0)
    assert verify_midi(r1.path, r1.expected_notes).ok
    assert verify_midi(r0.path, r0.expected_notes).ok
    m0 = mido.MidiFile(str(r0.path))
    assert m0.type == 0 and len(m0.tracks) == 1
    n1 = Counter((n.note, n.start, n.end) for n in read_midi_notes(r1.path)[1])
    n0 = Counter((n.note, n.start, n.end) for n in read_midi_notes(r0.path)[1])
    assert n1 == n0


def test_midi_reimport_preserves_pitches_and_durations(tmp_path):
    s = S.piano_score()
    perf = build_performance(s, PerformanceOptions(dynamics=False))
    res = write_midi(s, perf, tmp_path / "x.mid", 1)
    back, info = read_midi(res.path)
    orig = Counter((round(n.start_q * 12), round(n.dur_q * 12), n.midi) for n in perf.notes)
    perf2 = build_performance(back, PerformanceOptions(dynamics=False))
    again = Counter((round(n.start_q * 12), round(n.dur_q * 12), n.midi) for n in perf2.notes)
    assert orig == again
    assert {n.hand for n in back.notes} == {"R", "L"}


def test_rests_are_time_between_notes(tmp_path):
    s = S.melody_score()
    s.notes = [n for n in s.notes if not (n.measure == 1 and n.onset == 2)]
    s.notes.append(S._note(s, 1, 2, 1, None))
    perf = build_performance(s)
    starts = sorted(n.start_q for n in perf.notes)
    assert 5.0 not in starts and all(n.midi > 0 for n in perf.notes)
    assert perf.total_q == pytest.approx(12.0)


def _repeat_score(nav=None):
    s = S.melody_score()
    for i in range(4, 6):
        s.measures.append(Measure(i, str(i + 1), (3, 4)))
        S._note(s, i, 0, 3, "C5")
    s.measures[1].repeat_start = True
    s.measures[2].repeat_end = True
    return s


def test_repeat_expansion_and_voltas():
    s = _repeat_score()
    order, notes = play_order(s, True)
    assert [m for m, _ in order] == [0, 1, 2, 1, 2, 3, 4, 5]
    s.measures[2].ending_numbers, s.measures[2].ending_type = [1], "start-stop"
    s.measures[3].ending_numbers, s.measures[3].ending_type = [2], "start-stop"
    order, _ = play_order(s, True)
    assert [m for m, _ in order] == [0, 1, 2, 1, 3, 4, 5]
    assert [m for m, _ in play_order(s, False)[0]] == list(range(6))


def test_da_capo_al_fine_and_missing_segno():
    s = S.melody_score()
    s.measures[1].navigation.append("fine")
    s.measures[3].navigation.append("dacapo_al_fine")
    order, _ = play_order(s, True)
    assert [m for m, _ in order] == [0, 1, 2, 3, 0, 1]
    s2 = S.melody_score()
    s2.measures[3].navigation.append("dalsegno")
    _order, notes = play_order(s2, True)
    assert notes and "segno" in notes[0]


def test_accelerando_with_known_target_interpolates():
    s = S.melody_score()
    s.tempos = [TempoMark(0, F(0), 60.0), TempoMark(3, F(0), 120.0)]
    s.directions.append(Direction("tempo_change", 1, F(0), 0, None, "accelerando", text="accel."))
    perf = build_performance(s)
    assert perf.bpm_at(0.5) == 60
    assert 60 < perf.bpm_at(5.0) < 120
    assert perf.bpm_at(9.5) == 120


def test_tie_continues_across_pages():
    """Una nota ligada al final de una página continúa en la siguiente sin nuevo ataque."""
    from note_cascade.music_model.combine import concat_scores
    page1 = S.melody_score()
    page2 = S.melody_score()
    last = max((n for n in page1.notes if n.measure == 3), key=lambda n: n.onset)
    last.tie_start = True
    first = min((n for n in page2.notes if n.measure == 0), key=lambda n: n.onset)
    for n in (first,):
        n.step, n.alter, n.octave, n.midi = last.step, last.alter, last.octave, last.midi
        n.tie_stop = True
    whole = concat_scores([page1, page2])
    assert len(whole.measures) == 8 and whole.measures[4].new_page
    perf = build_performance(whole)
    tied = [n for n in perf.notes if n.midi == last.midi and abs(n.start_q - 9.0) < 1e-6]
    assert len(tied) == 1 and tied[0].dur_q == pytest.approx(float(last.duration + first.duration))


def test_time_signature_change_round_trip_and_midi(tmp_path):
    s = S.melody_score()  # 3/4
    s.measures[2].time_sig = (2, 4)
    s.measures[2].time_sig_change = True
    s.measures[3].time_sig = (2, 4)
    s.notes = [n for n in s.notes if n.measure < 2]
    S._note(s, 2, 0, 2, "A4", fifths=-1)
    S._note(s, 3, 0, 2, "F4", fifths=-1)
    back = read_musicxml(write_musicxml(s, tmp_path / "ts.musicxml")).score
    assert [m.time_sig for m in back.measures] == [(3, 4), (3, 4), (2, 4), (2, 4)]
    perf = build_performance(back)
    assert [(q, a, b) for q, a, b in perf.time_sigs] == [(0.0, 3, 4), (6.0, 2, 4)]
    res = write_midi(back, perf, tmp_path / "ts.mid", 1)
    ts = [(m.numerator, m.denominator) for m in mido.MidiFile(str(res.path)).tracks[0] if m.type == "time_signature"]
    assert ts == [(3, 4), (2, 4)]


def test_hands_follow_voices_for_cross_staff_notes():
    s = S.piano_score()
    n = next(x for x in s.notes if x.staff == 1 and not x.rest and x.measure == 0)
    n.staff = 2  # una nota de la voz 1 cruza al pentagrama inferior
    n.hand = None
    for x in s.notes:
        if not x.user_modified:
            x.hand = None
    assign_hands(s)
    assert n.hand == "R"
    assert all(x.hand == "L" for x in s.notes if x.voice == 5 and not x.rest)
