"""Importación de MIDI para reproducir, visualizar y editar (no para reconocer)."""

from __future__ import annotations

import re
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import mido

from note_cascade.app.errors import CorruptFileError
from note_cascade.music_model import pitch as P
from note_cascade.music_model.hands import LEFT, RIGHT
from note_cascade.music_model.model import ClefChange, KeyChange, Measure, Note, PartInfo, Score, TempoMark, new_id

GRID = Fraction(1, 12)  # cuantización: admite semicorcheas y tresillos de corchea
KEY_TO_FIFTHS = {"Cb": -7, "Gb": -6, "Db": -5, "Ab": -4, "Eb": -3, "Bb": -2, "F": -1, "C": 0, "G": 1, "D": 2,
                 "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7, "Abm": -7, "Ebm": -6, "Bbm": -5, "Fm": -4, "Cm": -3,
                 "Gm": -2, "Dm": -1, "Am": 0, "Em": 1, "Bm": 2, "F#m": 3, "C#m": 4, "G#m": 5, "D#m": 6, "A#m": 7}


def _q(ticks: int, tpb: int) -> Fraction:
    return Fraction(round(Fraction(ticks, tpb) / GRID)) * GRID


def _assign_voices(score: Score) -> None:
    """Reparte las notas de cada pentagrama en voces sin solapamientos (los acordes comparten voz)."""
    by_staff: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for n in score.notes:
        by_staff[(n.measure, n.staff)].append(n)
    for (_mi, staff), notes in by_staff.items():
        base = 1 if staff == 1 else 5
        voices: list[tuple[Fraction, Fraction, Fraction]] = []  # (inicio, duración, fin) del último evento
        for n in sorted(notes, key=lambda x: (x.onset, -x.duration)):
            chosen = None
            for vi, (on, du, end) in enumerate(voices):
                if (on, du) == (n.onset, n.duration) or end <= n.onset:
                    chosen = vi
                    break
            if chosen is None:
                voices.append((n.onset, n.duration, n.end))
                chosen = len(voices) - 1
            else:
                voices[chosen] = (n.onset, n.duration, n.end)
            n.voice = base + min(chosen, 3)


def read_midi(path: Path | str) -> tuple[Score, list[str]]:
    path = Path(path)
    notes_info: list[str] = []
    try:
        mid = mido.MidiFile(str(path))
    except Exception as exc:
        raise CorruptFileError(f"El archivo MIDI «{path.name}» está dañado o no es un MIDI válido.",
                               details=str(exc)) from exc
    tpb = mid.ticks_per_beat or 480
    raw = []  # (start, end, pitch, vel, track, channel)
    tempos = []
    time_sigs = []
    keys = []
    track_names: dict[int, str] = {}
    for tidx, track in enumerate(mid.tracks):
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for msg in track:
            tick += msg.time
            if msg.type == "track_name":
                track_names[tidx] = msg.name
            elif msg.type == "set_tempo":
                tempos.append((tick, mido.tempo2bpm(msg.tempo)))
            elif msg.type == "time_signature":
                time_sigs.append((tick, msg.numerator, msg.denominator))
            elif msg.type == "key_signature":
                keys.append((tick, KEY_TO_FIFTHS.get(msg.key, 0)))
            elif msg.type == "note_on" and msg.velocity > 0:
                if msg.channel == 9:
                    continue
                open_notes[(msg.channel, msg.note)].append((tick, msg.velocity))
            elif msg.type in ("note_off", "note_on"):
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    start, vel = stack.pop(0)
                    if tick > start:
                        raw.append((start, tick, msg.note, vel, tidx, msg.channel))
    if not raw:
        raise CorruptFileError(f"El MIDI «{path.name}» no contiene notas.")

    # Manos: por nombre de pista, por número de pistas o por registro.
    tracks_with_notes = sorted({r[4] for r in raw})
    hand_of_track: dict[int, str] = {}
    for t in tracks_with_notes:
        name = (track_names.get(t) or "").lower()
        if re.search(r"(right|derecha|\brh\b|treble|upper|m\.?\s*d\.?|mano der)", name):
            hand_of_track[t] = RIGHT
        elif re.search(r"(left|izquierda|\blh\b|bass|lower|m\.?\s*i\.?|mano izq)", name):
            hand_of_track[t] = LEFT
    split_by_pitch = False
    if len(tracks_with_notes) >= 2 and not hand_of_track:
        avg = {t: sum(r[2] for r in raw if r[4] == t) / sum(1 for r in raw if r[4] == t) for t in tracks_with_notes}
        ordered = sorted(tracks_with_notes, key=lambda t: -avg[t])
        hand_of_track[ordered[0]] = RIGHT
        for t in ordered[1:]:
            hand_of_track[t] = LEFT
        notes_info.append("Manos asignadas por pista: la pista más aguda es la mano derecha.")
    elif len(tracks_with_notes) == 1:
        split_by_pitch = True
        notes_info.append("El MIDI tiene una sola pista: las manos se estiman por registro (Do central = derecha). "
                          "Puedes corregirlo en la revisión.")

    ts = time_sigs[0] if time_sigs else (0, 4, 4)
    if not time_sigs:
        notes_info.append("El MIDI no indica compás: se supone 4/4.")
    num, den = ts[1], ts[2]
    measure_q = Fraction(num * 4, den)
    end_q = max(_q(r[1], tpb) for r in raw)
    n_measures = int(end_q // measure_q) + (1 if end_q % measure_q else 0)
    score = Score(title=path.stem, parts=[PartInfo("P1", "Piano", "Pno.", 2, 0, True)])
    for i in range(max(1, n_measures)):
        score.measures.append(Measure(i, str(i + 1), (num, den), time_sig_change=i == 0))
    fifths = keys[0][1] if keys else 0
    score.keys.append(KeyChange(0, 0, fifths))
    score.clefs.append(ClefChange(0, 1, 0, Fraction(0), "G", 2))
    score.clefs.append(ClefChange(0, 2, 0, Fraction(0), "F", 4))
    for tick, bpm in tempos:
        q = _q(tick, tpb)
        mi = min(int(q // measure_q), len(score.measures) - 1)
        score.tempos.append(TempoMark(mi, q - mi * measure_q, round(bpm, 2), source="importado"))

    for start, end, pitch, vel, tidx, ch in sorted(raw):
        s = _q(start, tpb)
        e = _q(end, tpb)
        if e <= s:
            e = s + GRID
        hand = hand_of_track.get(tidx) or (RIGHT if (pitch >= 60 if split_by_pitch else True) else LEFT)
        staff = 1 if hand == RIGHT else 2
        pieces = []
        cur = s
        while cur < e:
            mi = int(cur // measure_q)
            m_end = (mi + 1) * measure_q
            seg_end = min(e, m_end)
            pieces.append((mi, cur - mi * measure_q, seg_end - cur))
            cur = seg_end
        step, alter, octave = P.spell_midi(pitch, fifths)
        for k, (mi, onset, dur) in enumerate(pieces):
            if mi >= len(score.measures):
                score.measures.append(Measure(mi, str(mi + 1), (num, den)))
            score.notes.append(Note(
                id=new_id(), part=0, staff=staff, voice=1 if staff == 1 else 5, measure=mi, onset=onset,
                duration=dur, step=step, alter=alter, octave=octave, midi=pitch, hand=hand, source="importado",
                tie_start=k < len(pieces) - 1, tie_stop=k > 0, velocity=vel))
    _assign_voices(score)
    score.sort_notes()
    return score, notes_info
