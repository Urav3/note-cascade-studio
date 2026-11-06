"""Acordes arpegiados: la línea ondulada vertical delante de un acorde.

En la partitura (MusicXML) el signo va en las notas del acorde (``<arpeggiate/>``); a veces el motor
solo lo pone en una de ellas. Aquí se trata siempre como propiedad del acorde entero de un pentagrama:
todas las notas que empiezan a la vez en ese pentagrama.

Al tocar, un pianista no pulsa las teclas a la vez sino de abajo arriba, muy seguidas, y las mantiene
hasta el final de la figura: cada nota entra un poco más tarde y termina donde termina el acorde.
"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction

from note_cascade.music_model.model import Note, Score

ARPEGGIO = "arpeggiate"
ROLL_STEP_S = 0.045         # entre una nota y la siguiente del arpegio (≈ lo que tarda un pianista)
ROLL_MAX_FRACTION = 0.4     # el arpegio nunca ocupa más del 40 % de la nota más corta del acorde


def _key(n: Note) -> tuple[int, int, int, Fraction]:
    return n.part, n.staff, n.measure, n.onset


def _playable(n: Note) -> bool:
    return not n.rest and not n.grace


def chord_of(score: Score, note: Note) -> list[Note]:
    """Las notas que suenan a la vez que ``note`` en su pentagrama (incluida ella), de grave a agudo."""
    if not _playable(note):
        return [note]
    k = _key(note)
    return sorted((n for n in score.notes_in_measure(note.measure) if _playable(n) and _key(n) == k),
                  key=lambda n: n.midi)


def arpeggiated_chords(score: Score) -> list[list[Note]]:
    """Acordes (dos alturas o más) con el signo de arpegio en alguna de sus notas, de grave a agudo."""
    groups: dict[tuple, list[Note]] = defaultdict(list)
    for n in score.notes:
        if _playable(n):
            groups[_key(n)].append(n)
    return [sorted(v, key=lambda n: n.midi) for v in groups.values()
            if any(ARPEGGIO in n.articulations for n in v) and len({n.midi for n in v}) >= 2]


def is_arpeggiated(score: Score, note: Note) -> bool:
    chord = chord_of(score, note)
    return len({n.midi for n in chord}) >= 2 and any(ARPEGGIO in n.articulations for n in chord)


def roll_ranks(score: Score) -> dict[str, tuple[int, int, Fraction]]:
    """Para cada nota de un acorde arpegiado: (orden de entrada desde la más grave, nº de alturas, figura
    más corta del acorde). Las notas que continúan una ligadura no se vuelven a tocar y no cuentan."""
    out: dict[str, tuple[int, int, Fraction]] = {}
    for chord in arpeggiated_chords(score):
        struck = [n for n in chord if not n.tie_stop]
        midis = sorted({n.midi for n in struck})
        if len(midis) < 2:
            continue
        shortest = min(n.duration for n in struck)
        for n in struck:
            out[n.id] = (midis.index(n.midi), len(midis), shortest)
    return out


def roll_offset_s(rank: int, count: int, shortest_s: float) -> float:
    """Retraso (segundos) con el que entra la nota ``rank`` de un arpegio de ``count`` alturas."""
    if rank <= 0 or count < 2:
        return 0.0
    step = min(ROLL_STEP_S, ROLL_MAX_FRACTION * max(0.0, shortest_s) / (count - 1))
    return rank * step


def set_arpeggio(score: Score, note_ids: list[str] | set[str], on: bool) -> int:
    """Pone o quita el arpegio en los acordes que contienen esas notas. Devuelve cuántos acordes cambian."""
    ids = set(note_ids)
    done: set[tuple] = set()
    changed = 0
    for n in score.notes:
        if n.id not in ids or not _playable(n) or _key(n) in done:
            continue
        done.add(_key(n))
        chord = chord_of(score, n)
        if on and len({x.midi for x in chord}) < 2:
            continue
        before = [ARPEGGIO in x.articulations for x in chord]
        for x in chord:
            if on and ARPEGGIO not in x.articulations:
                x.articulations.append(ARPEGGIO)
            elif not on and ARPEGGIO in x.articulations:
                x.articulations = [a for a in x.articulations if a != ARPEGGIO]
        if before != [ARPEGGIO in x.articulations for x in chord]:
            changed += 1
    return changed
