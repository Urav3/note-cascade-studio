"""Asignación de mano derecha / izquierda para partes de piano.

Regla principal: pentagrama superior = mano derecha, inferior = mano
izquierda. Para respetar los cruces de pentagrama (una voz de la mano derecha
que baja momentáneamente al pentagrama inferior) se usa la *voz*: si casi todas
las notas de una voz están en un pentagrama, las pocas notas de esa voz
escritas en el otro pentagrama conservan la mano de su voz. La asignación es
editable por el usuario y las notas modificadas manualmente no se tocan.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from note_cascade.music_model.model import Score

RIGHT = "R"
LEFT = "L"
HAND_NAME = {RIGHT: "mano derecha", LEFT: "mano izquierda", None: "sin mano asignada"}


def assign_hands(score: Score, force: bool = False, use_voices: bool = True) -> int:
    """Asigna ``note.hand``. Devuelve el número de notas modificadas."""
    changed = 0
    for pidx, part in enumerate(score.parts):
        part_notes = [n for n in score.notes if n.part == pidx]
        if not part.is_piano:
            for n in part_notes:
                if n.hand is not None and not n.user_modified and force:
                    n.hand = None
                    changed += 1
            continue
        if part.staves < 2:
            for n in part_notes:
                if (n.hand is None or force) and not n.user_modified:
                    n.hand = RIGHT
                    changed += 1
            continue
        voice_staff: dict[tuple[int, int], Counter] = defaultdict(Counter)
        for n in part_notes:
            if not n.rest:
                voice_staff[(n.measure, n.voice)][n.staff] += 1
        global_voice_staff: dict[int, Counter] = defaultdict(Counter)
        for (measure, voice), counter in voice_staff.items():
            for staff, count in counter.items():
                global_voice_staff[voice][staff] += count
        for n in part_notes:
            if n.user_modified and n.hand is not None:
                continue
            if n.hand is not None and not force:
                continue
            staff = n.staff
            if use_voices and not n.rest:
                counter = voice_staff.get((n.measure, n.voice))
                if counter and len(counter) > 1:
                    dominant, dom_count = counter.most_common(1)[0]
                    total = sum(counter.values())
                    if dominant != staff and dom_count / total >= 0.7:
                        # Validar con la estadística global de la voz para no
                        # confundir voces que realmente comparten pentagramas.
                        g = global_voice_staff[n.voice]
                        if g and g.most_common(1)[0][0] == dominant:
                            staff = dominant
            hand = RIGHT if staff <= 1 else LEFT
            if n.hand != hand:
                n.hand = hand
                changed += 1
    return changed


def hand_of(note, part_is_piano: bool) -> str | None:
    if note.hand:
        return note.hand
    if not part_is_piano:
        return None
    return RIGHT if note.staff <= 1 else LEFT
