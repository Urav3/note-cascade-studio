"""Geometría del teclado de piano."""

from __future__ import annotations

from dataclasses import dataclass

from note_cascade.music_model.pitch import is_black_key

# Desplazamiento de las teclas negras respecto del borde entre blancas (fracción del ancho de blanca)
BLACK_OFFSET = {1: -0.08, 3: 0.08, 6: -0.1, 8: 0.0, 10: 0.1}

RANGES = {
    "88 teclas": (21, 108),
    "76 teclas": (28, 103),
    "61 teclas": (36, 96),
    "49 teclas": (36, 84),
    "Teclado automático": None,
}


@dataclass
class KeyRect:
    midi: int
    x: float
    w: float
    black: bool


def snap_white(midi: int, direction: int) -> int:
    while is_black_key(midi):
        midi += direction
    return midi


def key_layout(low: int, high: int, x0: float, width: float) -> dict[int, KeyRect]:
    low = snap_white(max(0, low), -1)
    high = snap_white(min(127, high), +1)
    whites = [m for m in range(low, high + 1) if not is_black_key(m)]
    if not whites:
        return {}
    ww = width / len(whites)
    layout: dict[int, KeyRect] = {}
    for i, m in enumerate(whites):
        layout[m] = KeyRect(m, x0 + i * ww, ww, False)
    bw = ww * 0.62
    for m in range(low, high + 1):
        if is_black_key(m) and (m - 1) in layout:
            left = layout[m - 1]
            center = left.x + left.w + BLACK_OFFSET.get(m % 12, 0.0) * ww
            layout[m] = KeyRect(m, center - bw / 2, bw, True)
    return layout


def auto_range(midis: list[int], minimum_span: int = 25) -> tuple[int, int]:
    if not midis:
        return 36, 96
    lo, hi = min(midis) - 2, max(midis) + 2
    if hi - lo < minimum_span:
        mid = (lo + hi) // 2
        lo, hi = mid - minimum_span // 2, mid + minimum_span // 2
    lo = max(21, lo)
    hi = min(108, hi)
    return snap_white(lo, -1), snap_white(hi, +1)
