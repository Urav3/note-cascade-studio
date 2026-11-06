"""Utilidades de altura: nombres de notas, MIDI, armaduras y ortografía."""

from __future__ import annotations

STEPS = ["C", "D", "E", "F", "G", "A", "B"]
STEP_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SOLFEGE = {"C": "Do", "D": "Re", "E": "Mi", "F": "Fa", "G": "Sol", "A": "La", "B": "Si"}
ALTER_SYMBOL = {-2: "𝄫", -1: "♭", 0: "", 1: "♯", 2: "𝄪"}
ALTER_ASCII = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}
ALTER_NAME_ES = {-2: "doble bemol", -1: "bemol", 0: "natural", 1: "sostenido", 2: "doble sostenido"}
ACCIDENTAL_TO_ALTER = {
    "sharp": 1, "flat": -1, "natural": 0, "double-sharp": 2, "sharp-sharp": 2,
    "flat-flat": -2, "double-flat": -2, "natural-sharp": 1, "natural-flat": -1,
}
ALTER_TO_ACCIDENTAL = {1: "sharp", -1: "flat", 0: "natural", 2: "double-sharp", -2: "flat-flat"}

# Orden de sostenidos y bemoles en las armaduras
SHARP_ORDER = ["F", "C", "G", "D", "A", "E", "B"]
FLAT_ORDER = ["B", "E", "A", "D", "G", "C", "F"]

KEY_NAMES_MAJOR = {
    -7: "Do♭ mayor", -6: "Sol♭ mayor", -5: "Re♭ mayor", -4: "La♭ mayor", -3: "Mi♭ mayor",
    -2: "Si♭ mayor", -1: "Fa mayor", 0: "Do mayor", 1: "Sol mayor", 2: "Re mayor",
    3: "La mayor", 4: "Mi mayor", 5: "Si mayor", 6: "Fa♯ mayor", 7: "Do♯ mayor",
}


def midi_from(step: str, alter: int, octave: int) -> int:
    return 12 * (octave + 1) + STEP_PC[step] + int(alter)


def diatonic_index(step: str, octave: int) -> int:
    """Índice diatónico absoluto (C0 = 0, D0 = 1, ...)."""
    return octave * 7 + STEPS.index(step)


def from_diatonic_index(index: int) -> tuple[str, int]:
    octave, s = divmod(index, 7)
    return STEPS[s], octave


def key_alterations(fifths: int) -> dict[str, int]:
    """Alteración que la armadura impone a cada nombre de nota."""
    alters = {s: 0 for s in STEPS}
    if fifths > 0:
        for s in SHARP_ORDER[: min(fifths, 7)]:
            alters[s] = 1
    elif fifths < 0:
        for s in FLAT_ORDER[: min(-fifths, 7)]:
            alters[s] = -1
    return alters


def key_signature_text(fifths: int) -> str:
    if fifths == 0:
        return "sin alteraciones (Do mayor / La menor)"
    n = abs(fifths)
    kind = "sostenido" if fifths > 0 else "bemol"
    plural = kind + ("s" if n > 1 else "")
    return f"{n} {plural} ({KEY_NAMES_MAJOR.get(fifths, '')})"


def spell_midi(midi: int, fifths: int = 0, prefer: int | None = None,
               key_first: bool = False) -> tuple[str, int, int]:
    """Devuelve (step, alter, octave) para un número MIDI según la armadura.

    Se prefieren las notas naturales o las alteraciones de la propia armadura;
    si no hay coincidencia se usan sostenidos en tonalidades con sostenidos (o
    Do mayor) y bemoles en tonalidades con bemoles. ``prefer`` (+1/-1) fuerza el
    tipo de alteración cuando se quiere respetar la escrita (al cambiar de octava).
    Con ``key_first`` manda la armadura aunque haya ``prefer``: al bajar un Mi un
    semitono en Mi mayor sale Re♯ (está en la armadura), no Mi♭.
    """
    pc = midi % 12
    octave = midi // 12 - 1
    key_alt = key_alterations(fifths)
    # 1) nota de la armadura
    for step in STEPS:
        alt = key_alt[step]
        if (STEP_PC[step] + alt) % 12 == pc and (prefer is None or key_first):
            return step, alt, _octave_fix(step, alt, midi)
    # 2) nota natural
    for step in STEPS:
        if STEP_PC[step] == pc:
            return step, 0, octave
    direction = prefer if prefer in (1, -1) else (-1 if fifths < 0 else 1)
    for step in STEPS:
        if (STEP_PC[step] + direction) % 12 == pc:
            return step, direction, _octave_fix(step, direction, midi)
    for step in STEPS:  # pragma: no cover - siempre se encuentra antes
        if (STEP_PC[step] - direction) % 12 == pc:
            return step, -direction, _octave_fix(step, -direction, midi)
    return "C", 0, octave  # pragma: no cover


def _octave_fix(step: str, alter: int, midi: int) -> int:
    # Si (B#) y Do (Cb) cruzan la frontera de octava.
    return (midi - STEP_PC[step] - alter) // 12 - 1


def note_name(step: str, alter: int, octave: int, style: str = "es") -> str:
    if style == "en":
        return f"{step}{ALTER_ASCII.get(alter, '')}{octave}"
    return f"{SOLFEGE[step]}{ALTER_SYMBOL.get(alter, '')}{octave}"


def midi_name(midi: int, style: str = "es", fifths: int = 0) -> str:
    step, alter, octave = spell_midi(midi, fifths)
    return note_name(step, alter, octave, style)


def is_black_key(midi: int) -> bool:
    return (midi % 12) in (1, 3, 6, 8, 10)


# ---------------------------------------------------------------------------
# Claves
# ---------------------------------------------------------------------------

# Nota que ocupa la línea central del pentagrama para cada clave (sign, line).
def clef_middle_line(sign: str, line: int, octave_change: int = 0) -> tuple[str, int]:
    """Nota escrita en la tercera línea del pentagrama para la clave dada."""
    sign = sign.upper()
    if sign == "G":
        ref_step, ref_oct = "G", 4
    elif sign == "F":
        ref_step, ref_oct = "F", 3
    elif sign == "C":
        ref_step, ref_oct = "C", 4
    else:  # percusión / TAB: usar clave de sol como referencia
        ref_step, ref_oct, line = "B", 4, 3
    ref = diatonic_index(ref_step, ref_oct + octave_change)
    middle = ref + (3 - line) * 2
    return from_diatonic_index(middle)


def staff_position(step: str, octave: int, sign: str, line: int, octave_change: int = 0) -> int:
    """Posición en el pentagrama (0 = línea central, positivo hacia abajo)."""
    mid_step, mid_oct = clef_middle_line(sign, line, octave_change)
    return diatonic_index(mid_step, mid_oct) - diatonic_index(step, octave)


def clef_description(sign: str, line: int, octave_change: int = 0) -> str:
    base = {
        ("G", 2): "clave de sol",
        ("F", 4): "clave de fa en cuarta",
        ("F", 3): "clave de fa en tercera",
        ("C", 3): "clave de do en tercera",
        ("C", 4): "clave de do en cuarta",
        ("C", 1): "clave de do en primera",
        ("C", 2): "clave de do en segunda",
        ("G", 1): "clave de sol en primera",
    }.get((sign.upper(), line), f"clave {sign}{line}")
    if sign.upper() == "PERCUSSION":
        base = "clave de percusión"
    if octave_change == -1:
        base += " (8 abajo)"
    elif octave_change == 1:
        base += " (8 arriba)"
    return base
