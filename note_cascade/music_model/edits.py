"""Ediciones del usuario con deshacer/rehacer.

Cada operación guarda una instantánea comprimida de la partitura anterior.
Es simple y robusto (cualquier edición se puede deshacer exactamente) y la
memoria se acota con un límite de pasos.
"""

from __future__ import annotations

import pickle
import zlib
from dataclasses import dataclass
from fractions import Fraction

from note_cascade.music_model import pitch as P
from note_cascade.music_model.hands import LEFT, RIGHT
from note_cascade.music_model.model import Note, Score, frac, new_id, notation_for_duration


@dataclass
class _Snapshot:
    label: str
    data: bytes


def _pack(score: Score) -> bytes:
    return zlib.compress(pickle.dumps(score, protocol=pickle.HIGHEST_PROTOCOL), 3)


def _unpack(data: bytes) -> Score:
    return pickle.loads(zlib.decompress(data))


class EditHistory:
    def __init__(self, limit: int = 80) -> None:
        self.limit = limit
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def record(self, score: Score, label: str) -> None:
        """Llamar ANTES de modificar ``score``."""
        self._undo.append(_Snapshot(label, _pack(score)))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def undo(self, current: Score) -> Score | None:
        if not self._undo:
            return None
        snap = self._undo.pop()
        self._redo.append(_Snapshot(snap.label, _pack(current)))
        return _unpack(snap.data)

    def redo(self, current: Score) -> Score | None:
        if not self._redo:
            return None
        snap = self._redo.pop()
        self._undo.append(_Snapshot(snap.label, _pack(current)))
        return _unpack(snap.data)


# ---------------------------------------------------------------------------
# Operaciones de edición (modifican la partitura en sitio)
# ---------------------------------------------------------------------------

def _respell(score: Score, n: Note, prefer: int | None = None) -> None:
    from note_cascade.music_validation.fixes import respell
    # Un semitono arriba o abajo: si la nueva nota está en la armadura se escribe como en ella (en Mi mayor,
    # bajar Mi da Re♯, no Mi♭); si no, sostenido al subir y bemol al bajar.
    respell(n, score, prefer, key_first=True)


def set_pitch(score: Score, note_ids: list[str], midi_values: dict[str, int] | None = None, delta: int = 0) -> int:
    count = 0
    for n in score.notes:
        if n.id in note_ids and not n.rest:
            new = midi_values.get(n.id, n.midi + delta) if midi_values else n.midi + delta
            new = max(0, min(127, int(new)))
            if new != n.midi:
                prefer = 1 if delta > 0 and abs(delta) == 1 else (-1 if delta < 0 and abs(delta) == 1 else None)
                n.pitch_log.append(f"edición del usuario: MIDI {n.midi} → {new}")
                n.midi = new
                _respell(score, n, prefer)
                n.accidental_shown = None
                n.user_modified = True
                n.confidence = None
                count += 1
    return count


def set_spelling(score: Score, note_id: str, step: str, alter: int, octave: int) -> bool:
    n = score.note_by_id(note_id)
    if n is None or n.rest:
        return False
    part = score.parts[n.part] if n.part < len(score.parts) else None
    shift = (part.transpose_chromatic + 12 * part.transpose_octave) if part else 0
    n.step, n.alter, n.octave = step, int(alter), int(octave)
    old = n.midi
    n.midi = P.midi_from(step, alter, octave) + shift
    if n.midi != old:
        n.pitch_log.append(f"edición del usuario: MIDI {old} → {n.midi}")
    n.accidental_shown = None
    n.user_modified = True
    return True


def set_duration(score: Score, note_ids: list[str], quarters: Fraction) -> int:
    count = 0
    quarters = frac(quarters)
    found = notation_for_duration(quarters)
    for n in score.notes:
        if n.id in note_ids and not n.grace:
            n.duration = quarters
            if found:
                n.type_name, n.dots, n.tuplet = found
            else:
                n.type_name, n.dots, n.tuplet = None, 0, None
            n.user_modified = True
            count += 1
    return count


def move_notes(score: Score, note_ids: list[str], delta_q: Fraction) -> int:
    """Desplaza en el tiempo (dentro del compás; si se sale, cambia de compás)."""
    from note_cascade.music_model.timeline import measure_lengths
    lengths = measure_lengths(score)
    count = 0
    for n in score.notes:
        if n.id not in note_ids:
            continue
        onset = n.onset + frac(delta_q)
        m = n.measure
        while onset < 0 and m > 0:
            m -= 1
            onset += lengths[m]
        while m < len(lengths) - 1 and onset >= lengths[m]:
            onset -= lengths[m]
            m += 1
        n.onset = max(Fraction(0), onset)
        n.measure = m
        n.user_modified = True
        count += 1
    score.sort_notes()
    return count


def add_note(score: Score, measure: int, onset: Fraction, midi: int, duration: Fraction, part: int = 0,
             staff: int = 1, voice: int | None = None, hand: str | None = None) -> Note:
    fifths = score.key_at(part, measure, staff)
    step, alter, octave = P.spell_midi(midi, fifths)
    p = score.parts[part] if part < len(score.parts) else None
    shift = (p.transpose_chromatic + 12 * p.transpose_octave) if p else 0
    step, alter, octave = P.spell_midi(midi - shift, fifths)
    found = notation_for_duration(frac(duration))
    n = Note(id=new_id(), part=part, staff=staff, voice=voice or (1 if staff == 1 else 5), measure=measure,
             onset=frac(onset), duration=frac(duration), step=step, alter=alter, octave=octave, midi=midi,
             hand=hand or (RIGHT if staff == 1 else LEFT) if (p and p.is_piano) else hand, source="usuario",
             user_modified=True)
    if found:
        n.type_name, n.dots, n.tuplet = found
    score.notes.append(n)
    score.sort_notes()
    return n


def delete_notes(score: Score, note_ids: list[str]) -> int:
    before = len(score.notes)
    ids = set(note_ids)
    score.notes = [n for n in score.notes if n.id not in ids]
    return before - len(score.notes)


def set_hand(score: Score, note_ids: list[str], hand: str) -> int:
    count = 0
    for n in score.notes:
        if n.id in note_ids and n.hand != hand:
            n.hand = hand
            n.user_modified = True
            count += 1
    return count


def set_tempo(score: Score, measure: int, bpm: float, text: str = "") -> None:
    from note_cascade.music_model.model import TempoMark
    existing = [t for t in score.tempos if t.measure == measure and t.onset == 0]
    if existing:
        existing[0].bpm_quarter = float(bpm)
        existing[0].per_minute = None
        existing[0].beat_unit, existing[0].beat_dots = "quarter", 0
        existing[0].source = "usuario"
    else:
        score.tempos.append(TempoMark(measure, Fraction(0), float(bpm), text=text, source="usuario"))
