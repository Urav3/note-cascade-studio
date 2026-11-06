"""Verificación de archivos MIDI exportados: se vuelven a leer y se comparan."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import mido


@dataclass
class MidiNote:
    channel: int
    note: int
    start: int
    end: int
    velocity: int
    track: int


@dataclass
class MidiVerification:
    ok: bool
    path: Path
    message: str
    notes_expected: int = 0
    notes_found: int = 0
    tracks: int = 0
    duration_s: float = 0.0
    problems: list[str] = field(default_factory=list)


def read_midi_notes(path: Path | str) -> tuple[mido.MidiFile, list[MidiNote]]:
    mid = mido.MidiFile(str(path))
    notes: list[MidiNote] = []
    for tidx, track in enumerate(mid.tracks):
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    start, vel = stack.pop(0)
                    notes.append(MidiNote(msg.channel, msg.note, start, tick, vel, tidx))
    return mid, notes


def verify_midi(path: Path | str, expected: list[tuple[int, int, int, int]] | None = None) -> MidiVerification:
    path = Path(path)
    if not path.exists():
        return MidiVerification(False, path, "El archivo MIDI no se creó.")
    if path.stat().st_size < 30:
        return MidiVerification(False, path, "El archivo MIDI está vacío o incompleto.")
    try:
        mid, notes = read_midi_notes(path)
    except Exception as exc:  # mido lanza varios tipos de excepción
        return MidiVerification(False, path, f"El archivo MIDI no se puede volver a leer: {exc}")
    result = MidiVerification(True, path, "", notes_found=len(notes), tracks=len(mid.tracks))
    try:
        result.duration_s = float(mid.length)
    except Exception:
        result.duration_s = 0.0
    if not notes:
        result.ok = False
        result.message = "El archivo MIDI no contiene ninguna nota."
        return result
    if expected is not None:
        result.notes_expected = len(expected)
        exp = Counter((c, n, s, e) for c, n, s, e in expected)
        got = Counter((m.channel, m.note, m.start, m.end) for m in notes)
        missing = exp - got
        extra = got - exp
        if missing:
            result.problems.append(f"Faltan {sum(missing.values())} notas respecto de lo esperado.")
        if extra:
            result.problems.append(f"Hay {sum(extra.values())} notas no esperadas.")
        if missing or extra:
            result.ok = False
    result.message = ("Archivo MIDI verificado: se volvió a leer correctamente "
                      f"({result.notes_found} notas, {result.tracks} pistas, {result.duration_s:.1f} s)."
                      if result.ok else "El archivo MIDI no coincide con la música esperada: " + " ".join(result.problems))
    return result
