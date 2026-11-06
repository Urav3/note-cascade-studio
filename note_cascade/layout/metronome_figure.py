"""Figura de pulso de una indicación metronómica dibujada («♩. = 65»).

En muchos PDF el número del metrónomo está en la capa de texto, pero la figura (negra, negra con
puntillo, blanca…) es un dibujo y no se puede leer como texto. Aquí se mira la tinta justo a la
izquierda del número: el signo «=» (dos rayas), la figura (cabeza + plica) y, a la derecha de la
cabeza, el puntillo si lo hay. Solo se devuelve un resultado cuando la forma es clara; si no, None y
el programa sigue suponiendo la figura habitual del compás.
"""

from __future__ import annotations

import cv2
import numpy as np


def read_beat_figure(page_rgb: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> tuple[str, int] | None:
    """(figura, puntillos) de la indicación cuyo número ocupa la caja normalizada (x0, y0)–(x1, y1)."""
    if page_rgb is None or page_rgb.size == 0:
        return None
    gray = page_rgb if page_rgb.ndim == 2 else cv2.cvtColor(page_rgb, cv2.COLOR_RGB2GRAY)
    H, W = gray.shape
    bx0, by0, bx1, by1 = x0 * W, y0 * H, x1 * W, y1 * H
    h = by1 - by0                     # altura de las cifras
    if h < 8:
        return None
    xa, xb = int(max(0, bx0 - 4.5 * h)), int(max(0, bx0 + 0.05 * h))
    ya, yb = int(max(0, by0 - 1.5 * h)), int(min(H, by1 + 0.4 * h))
    if xb - xa < h or yb - ya < h:
        return None
    reg = gray[ya:yb, xa:xb]
    _t, bw = cv2.threshold(reg, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lab, st, _c = cv2.connectedComponentsWithStats(bw, 8)
    comps = [tuple(int(v) for v in st[k]) + (k,) for k in range(1, n) if st[k][4] > 2]
    # «=»: dos rayas horizontales iguales, una encima de otra, pegadas al número
    bars = [c for c in comps if c[2] >= 3 * c[3] and 0.2 * h <= c[2] <= 0.8 * h]
    eq_left = None
    for a in bars:
        for b in bars:
            if b is not a and abs(a[0] - b[0]) <= 0.15 * h and 0 < b[1] - a[1] <= 0.45 * h:
                eq_left = min(a[0], b[0]) if eq_left is None else max(eq_left, min(a[0], b[0]))
    right_limit = eq_left if eq_left is not None else (xb - xa)
    # figura: plica alta y estrecha con la cabeza abajo (o arriba), a la izquierda del «=»
    figs = [c for c in comps if 0.7 * h <= c[3] <= 1.7 * h and c[2] <= 0.9 * h and c[0] + c[2] <= right_limit + 2]
    if not figs:
        return None
    fx, fy, fw, fh, _area, label = max(figs, key=lambda c: c[0])
    fig = (lab[fy:fy + fh, fx:fx + fw] == label).astype(np.uint8)
    rows = fig.sum(axis=1)
    head_at_bottom = rows[fh * 2 // 3:].sum() >= rows[: fh // 3].sum()
    # plica: columnas con tinta en casi toda la altura de la figura
    stem = fig.sum(axis=0) >= 0.7 * fh
    band = fig[fh - int(0.35 * fh):, :] if head_at_bottom else fig[: int(0.35 * fh), :]
    band = band[:, ~stem] if (~stem).any() else band
    widths = (band > 0).sum(axis=1)
    if widths.max(initial=0) < 0.15 * h:
        return None
    core_rows = np.nonzero(widths >= 0.5 * widths.max())[0]
    core = band[core_rows.min():core_rows.max() + 1, :]
    cols = np.nonzero(core.any(axis=0))[0]
    core = core[:, cols.min():cols.max() + 1]
    off = fh - int(0.35 * fh) if head_at_bottom else 0
    head_cy = fy + off + (core_rows.min() + core_rows.max()) / 2
    head_right = fx + fw if not head_at_bottom else fx + _right_edge(fig, stem)
    # cabeza rellena (negra, corchea) o hueca (blanca)
    filled = float(core.mean()) >= 0.6
    # corchete (corchea): la parte de la plica lejos de la cabeza es más ancha que una plica
    far = fig[: int(0.45 * fh), :] if head_at_bottom else fig[fh - int(0.45 * fh):, :]
    stem_cols = (far.sum(axis=0) > 0).sum()
    flagged = stem_cols > 0.22 * h
    if filled and not flagged:
        unit = "quarter"
    elif not filled and not flagged:
        unit = "half"
    elif filled and flagged:
        unit = "eighth"
    else:
        return None
    # puntillo: mancha pequeña y redonda a la derecha de la figura, a la altura de la cabeza
    dots = 0
    for c in comps:
        if c[5] == label:
            continue
        cx, cy = c[0] + c[2] / 2, c[1] + c[3] / 2
        if c[2] <= 0.22 * h and c[3] <= 0.22 * h and 0.5 <= c[2] / max(1, c[3]) <= 2.0 \
                and head_right - 1 <= c[0] <= right_limit and abs(cy - head_cy) <= 0.35 * h:
            dots += 1
    return unit, min(dots, 2)


def _right_edge(fig: np.ndarray, stem: np.ndarray) -> int:
    """Borde derecho de la cabeza más la plica (la plica de una figura con la cabeza abajo va a su derecha)."""
    cols = np.nonzero(stem)[0]
    if len(cols):
        return int(cols.max()) + 1
    return fig.shape[1]


UNIT_QUARTERS = {"half": 2.0, "quarter": 1.0, "eighth": 0.5}
UNIT_NAMES = {"half": "blanca", "quarter": "negra", "eighth": "corchea"}


def figure_name(unit: str, dots: int) -> str:
    return UNIT_NAMES.get(unit, unit) + (" con puntillo" if dots == 1 else " con doble puntillo" if dots == 2 else "")


def figure_quarters(unit: str, dots: int) -> float:
    q = UNIT_QUARTERS.get(unit, 1.0)
    return q * (1.5 if dots == 1 else 1.75 if dots == 2 else 1.0)
