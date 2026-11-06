"""Detección de pentagramas, sistemas y barras de compás.

Método clásico y determinista (sin redes neuronales):

1. Estimación del grosor de línea y del interlineado con *run-length* vertical.
2. Máscara de líneas horizontales (apertura morfológica) y perfil por franjas
   verticales para tolerar inclinación residual y ligera curvatura.
3. Agrupación de 5 líneas equiespaciadas en pentagramas y fusión entre franjas.
4. Agrupación de pentagramas en sistemas comprobando la conexión vertical en
   el inicio del sistema (barra inicial, llave o corchete).
5. Barras de compás: líneas verticales que cubren todo el alto de cada
   pentagrama del sistema.

El resultado sirve para: elegir la resolución, corregir la inclinación,
localizar regiones musicales frente a texto, superponer información en la
revisión y contrastar el número de compases de los motores OMR.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Staff:
    lines: list[tuple[float, float]]  # (a, b) con y = a + b·x para cada una de las 5 líneas
    x0: float
    x1: float
    interline: float
    thickness: float
    strips: int = 0

    def y(self, line: int, x: float | None = None) -> float:
        a, b = self.lines[line]
        if x is None:
            x = (self.x0 + self.x1) / 2
        return a + b * x

    @property
    def top(self) -> float:
        return min(self.y(0, self.x0), self.y(0, self.x1))

    @property
    def bottom(self) -> float:
        return max(self.y(4, self.x0), self.y(4, self.x1))

    @property
    def middle(self) -> float:
        return self.y(2)

    @property
    def height(self) -> float:
        return self.y(4) - self.y(0)

    def position_to_y(self, position: float, x: float | None = None) -> float:
        """Posición en el pentagrama (0 = línea central, positivo hacia abajo) a coordenada y."""
        return self.y(2, x) + position * self.interline / 2

    def to_dict(self) -> dict:
        return {"lines": self.lines, "x0": self.x0, "x1": self.x1, "interline": self.interline,
                "thickness": self.thickness}

    @classmethod
    def from_dict(cls, d: dict) -> "Staff":
        return cls([tuple(x) for x in d["lines"]], d["x0"], d["x1"], d["interline"], d["thickness"])


@dataclass
class System:
    staves: list[int]
    x0: float
    x1: float
    y0: float
    y1: float
    barlines: list[float] = field(default_factory=list)

    def measure_spans(self, min_width: float = 0.0) -> list[tuple[float, float]]:
        """Segmentos horizontales entre barras (aproximación de los compases)."""
        xs = [b for b in sorted(self.barlines) if self.x0 + min_width < b < self.x1 + 1]
        spans = []
        left = self.x0
        for b in xs:
            if b - left > min_width:
                spans.append((left, b))
                left = b
        if self.x1 - left > max(min_width, 1.0) and (not xs or self.x1 - xs[-1] > min_width):
            spans.append((left, self.x1))
        return spans

    def to_dict(self) -> dict:
        return {"staves": self.staves, "x0": self.x0, "x1": self.x1, "y0": self.y0, "y1": self.y1,
                "barlines": self.barlines}

    @classmethod
    def from_dict(cls, d: dict) -> "System":
        return cls(list(d["staves"]), d["x0"], d["x1"], d["y0"], d["y1"], list(d.get("barlines", [])))


@dataclass
class PageGeometry:
    width: int
    height: int
    interline: float = 0.0
    thickness: float = 0.0
    staves: list[Staff] = field(default_factory=list)
    systems: list[System] = field(default_factory=list)
    skew_deg: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def has_music(self) -> bool:
        return len(self.staves) > 0

    def music_bbox(self, margin_interlines: float = 4.0) -> tuple[float, float, float, float] | None:
        if not self.staves:
            return None
        m = margin_interlines * self.interline
        x0 = min(s.x0 for s in self.staves) - m
        x1 = max(s.x1 for s in self.staves) + m
        y0 = min(s.top for s in self.staves) - m
        y1 = max(s.bottom for s in self.staves) + m
        return max(0, x0), max(0, y0), min(self.width, x1), min(self.height, y1)

    def staff_at_y(self, y: float) -> int | None:
        best, best_d = None, 1e18
        for i, s in enumerate(self.staves):
            if s.top - 2 * s.interline <= y <= s.bottom + 2 * s.interline:
                d = abs(y - s.middle)
                if d < best_d:
                    best, best_d = i, d
        return best

    def system_of_staff(self, staff_index: int) -> int | None:
        for i, sys in enumerate(self.systems):
            if staff_index in sys.staves:
                return i
        return None

    def scaled(self, sx: float, sy: float) -> "PageGeometry":
        g = PageGeometry(int(self.width * sx), int(self.height * sy), self.interline * sy, self.thickness * sy,
                         skew_deg=self.skew_deg, notes=list(self.notes))
        for s in self.staves:
            g.staves.append(Staff([(a * sy, b * sy / sx) for a, b in s.lines], s.x0 * sx, s.x1 * sx,
                                  s.interline * sy, s.thickness * sy, s.strips))
        for sys in self.systems:
            g.systems.append(System(list(sys.staves), sys.x0 * sx, sys.x1 * sx, sys.y0 * sy, sys.y1 * sy,
                                    [b * sx for b in sys.barlines]))
        return g

    def to_dict(self) -> dict:
        return {"width": self.width, "height": self.height, "interline": self.interline,
                "thickness": self.thickness, "skew_deg": self.skew_deg, "notes": self.notes,
                "staves": [s.to_dict() for s in self.staves], "systems": [s.to_dict() for s in self.systems]}

    @classmethod
    def from_dict(cls, d: dict) -> "PageGeometry":
        g = cls(int(d["width"]), int(d["height"]), float(d.get("interline", 0)), float(d.get("thickness", 0)),
                skew_deg=float(d.get("skew_deg", 0)), notes=list(d.get("notes", [])))
        g.staves = [Staff.from_dict(x) for x in d.get("staves", [])]
        g.systems = [System.from_dict(x) for x in d.get("systems", [])]
        return g


# ---------------------------------------------------------------------------

def binarize(gray: np.ndarray) -> np.ndarray:
    """Devuelve una máscara uint8 (1 = tinta)."""
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_RGB2GRAY)
    _, bw = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Si casi todo es "tinta" el umbral de Otsu falló (imagen casi blanca).
    if bw.mean() > 0.6:
        _, bw = cv2.threshold(gray, 160, 1, cv2.THRESH_BINARY_INV)
    return bw


def estimate_runs(bw: np.ndarray, samples: int = 300) -> tuple[float, float]:
    """(grosor de línea, interlineado) a partir de rachas verticales."""
    h, w = bw.shape
    cols = np.linspace(w * 0.05, w * 0.95, num=min(samples, max(10, w // 4))).astype(int)
    black_hist = np.zeros(64, dtype=np.int64)
    white_hist = np.zeros(256, dtype=np.int64)
    for x in cols:
        col = bw[:, x].astype(np.int8)
        diff = np.diff(np.concatenate(([0], col, [0])))
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)
        if len(starts) < 3:
            continue
        blacks = ends - starts
        whites = starts[1:] - ends[:-1]
        blacks = blacks[blacks < 64]
        whites = whites[(whites > 1) & (whites < 256)]
        np.add.at(black_hist, blacks, 1)
        np.add.at(white_hist, whites, 1)
    if black_hist.sum() == 0 or white_hist.sum() == 0:
        return 0.0, 0.0
    thickness = float(np.argmax(black_hist[1:]) + 1)
    # El hueco entre líneas del pentagrama es la racha blanca más frecuente.
    smooth = np.convolve(white_hist.astype(float), [0.25, 0.5, 0.25], mode="same")
    space = float(np.argmax(smooth[2:]) + 2)
    # refinamiento sub-píxel
    i = int(space)
    if 1 <= i < len(smooth) - 1:
        y0, y1, y2 = smooth[i - 1], smooth[i], smooth[i + 1]
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            space += 0.5 * (y0 - y2) / denom
    return thickness, space + thickness


def estimate_skew(bw: np.ndarray, interline: float) -> float:
    """Ángulo (grados) de las líneas del pentagrama; positivo = sentido horario."""
    h, w = bw.shape
    scale = min(1.0, 1600.0 / max(h, w))
    small = cv2.resize(bw * 255, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    small = (small > 96).astype(np.uint8)
    k = max(5, int(w * scale / 40))
    horiz = cv2.morphologyEx(small, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    # La apertura con un kernel horizontal pierde líneas muy inclinadas; ampliar con un cierre previo.
    if horiz.sum() < small.sum() * 0.02:
        closed = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 3)))
        horiz = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    lines = cv2.HoughLinesP(horiz * 255, 1, np.pi / 1800, threshold=max(30, int(w * scale / 8)),
                            minLineLength=max(20, int(w * scale / 6)), maxLineGap=max(3, int(interline * scale)))
    if lines is None or len(lines) == 0:
        return 0.0
    angles, weights = [], []
    for x1, y1, x2, y2 in lines[:, 0, :]:
        dx, dy = x2 - x1, y2 - y1
        if dx == 0:
            continue
        ang = math.degrees(math.atan2(dy, dx))
        if abs(ang) <= 15:
            angles.append(ang)
            weights.append(math.hypot(dx, dy))
    if not angles:
        return 0.0
    order = np.argsort(angles)
    a = np.array(angles)[order]
    wgt = np.array(weights)[order]
    cum = np.cumsum(wgt)
    median = float(a[np.searchsorted(cum, cum[-1] / 2)])
    return median


def _line_candidates(profile: np.ndarray, min_frac: float) -> list[tuple[float, float]]:
    """Filas (centro, grosor) donde el perfil supera ``min_frac``."""
    mask = profile >= min_frac
    diff = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    result = []
    for s, e in zip(starts, ends):
        seg = profile[s:e]
        weights = seg - min_frac + 1e-3
        center = s + float(np.sum(np.arange(e - s) * weights) / np.sum(weights))
        result.append((center, float(e - s)))
    return result


def _find_staves_in_lines(lines: list[tuple[float, float]], interline: float, thickness: float) -> list[list[float]]:
    """Agrupa líneas candidatas en conjuntos de 5 equiespaciadas."""
    ys = [y for y, t in lines if t <= max(3.0, thickness * 3.5)]
    staves: list[list[float]] = []
    used: set[int] = set()
    tol = max(1.5, 0.22 * interline)
    i = 0
    n = len(ys)
    while i < n:
        if i in used:
            i += 1
            continue
        group = [i]
        last = ys[i]
        j = i + 1
        while j < n and len(group) < 5:
            gap = ys[j] - last
            if abs(gap - interline) <= tol:
                group.append(j)
                last = ys[j]
            elif gap > interline + tol:
                break
            j += 1
        if len(group) == 5:
            staves.append([ys[k] for k in group])
            used.update(group)
            i = group[-1] + 1
        else:
            i += 1
    return staves


def detect_staves(gray_or_bw: np.ndarray, assume_binary: bool = False) -> PageGeometry:
    if assume_binary:
        bw = gray_or_bw.astype(np.uint8)
    else:
        bw = binarize(gray_or_bw)
    h, w = bw.shape
    geom = PageGeometry(w, h)
    thickness, interline = estimate_runs(bw)
    if interline < 4 or interline > h / 8:
        geom.notes.append("No se encontraron líneas de pentagrama.")
        return geom
    geom.interline, geom.thickness = interline, thickness

    k = int(max(interline * 2.5, 15))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    # Las barras de corchea son horizontales pero gruesas: se eliminan para que
    # no tapen las líneas finas del pentagrama.
    thick_k = int(max(3, round(thickness * 2.2 + 1)))
    thick = cv2.morphologyEx(horiz, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, thick_k)))
    thin = horiz & (1 - thick)
    n_strips = int(np.clip(w / (12 * interline), 4, 32))
    strip_w = w / n_strips
    strip_staves: list[tuple[float, list[float]]] = []
    for si in range(n_strips):
        xa, xb = int(si * strip_w), int((si + 1) * strip_w)
        found_any = False
        for mask, frac in ((horiz, 0.35), (thin, 0.25)):
            profile = mask[:, xa:xb].mean(axis=1)
            cands = _line_candidates(profile, frac)
            for ys in _find_staves_in_lines(cands, interline, thickness):
                if any(abs(ys[2] - other[2]) < interline * 1.5 for xc, other in strip_staves if xc == (xa + xb) / 2):
                    continue
                strip_staves.append(((xa + xb) / 2, ys))
                found_any = True
        _ = found_any

    # Fusión entre franjas: se encadenan por proximidad vertical.
    clusters: list[list[tuple[float, list[float]]]] = []
    for xc, ys in sorted(strip_staves, key=lambda s: s[1][2]):
        placed = False
        for cl in clusters:
            ref_x, ref_ys = min(cl, key=lambda c: abs(c[0] - xc))
            if abs(ref_ys[2] - ys[2]) < 1.6 * interline and all(abs(c[0] - xc) > 1 for c in cl):
                cl.append((xc, ys))
                placed = True
                break
        if not placed:
            clusters.append([(xc, ys)])

    staves: list[Staff] = []
    for cl in clusters:
        if len(cl) < 2 and n_strips > 3:
            continue
        # Mediana robusta: descartar franjas cuyo centro se desvía (falsos positivos).
        mids = np.array([c[1][2] for c in cl])
        med = float(np.median(mids))
        cl = [c for c in cl if abs(c[1][2] - med) < 0.8 * interline] or cl
        xs = np.array([c[0] for c in cl])
        fits = []
        for li in range(5):
            yv = np.array([c[1][li] for c in cl])
            if len(cl) >= 2 and np.ptp(xs) > 0:
                b, a = np.polyfit(xs, yv, 1)
            else:
                a, b = float(yv.mean()), 0.0
            fits.append((float(a), float(b)))
        x0, x1 = _staff_extent(horiz, fits, interline, w)
        if x1 - x0 < 8 * interline:
            continue
        staves.append(Staff(fits, x0, x1, interline, thickness, len(cl)))

    staves = _dedupe_staves(staves, interline)
    staves.sort(key=lambda s: s.middle)
    geom.staves = staves
    if staves:
        geom.interline = float(np.median([np.mean(np.diff([s.y(i) for i in range(5)])) for s in staves]))
        geom.skew_deg = float(np.degrees(np.arctan(np.median([np.mean([b for _a, b in s.lines]) for s in staves]))))
        geom.systems = _group_systems(bw, staves, interline)
        for sys in geom.systems:
            sys.barlines = _find_barlines(bw, staves, sys, interline, thickness)
    else:
        geom.notes.append("No se encontraron pentagramas completos de 5 líneas.")
    return geom


def _staff_extent(horiz: np.ndarray, fits: list[tuple[float, float]], interline: float, w: int) -> tuple[float, float]:
    h = horiz.shape[0]
    xs = np.arange(w)
    votes = np.zeros(w, dtype=np.int16)
    for a, b in fits:
        ys = np.clip(np.round(a + b * xs).astype(int), 0, h - 1)
        col = horiz[ys, xs]
        up = horiz[np.clip(ys - 1, 0, h - 1), xs]
        down = horiz[np.clip(ys + 1, 0, h - 1), xs]
        votes += ((col | up | down) > 0).astype(np.int16)
    present = votes >= 3
    # cerrar huecos menores que un interlineado (cabezas de nota, alteraciones)
    gap = int(max(3, interline * 1.5))
    present_c = cv2.morphologyEx(present.astype(np.uint8)[None, :], cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (gap, 1)))[0] > 0
    diff = np.diff(np.concatenate(([0], present_c.astype(np.int8), [0])))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    if len(starts) == 0:
        return 0.0, 0.0
    lengths = ends - starts
    k = int(np.argmax(lengths))
    return float(starts[k]), float(ends[k] - 1)


def _dedupe_staves(staves: list[Staff], interline: float) -> list[Staff]:
    result: list[Staff] = []
    for s in sorted(staves, key=lambda s: -(s.x1 - s.x0)):
        overlap = False
        for r in result:
            if abs(r.middle - s.middle) < 2.5 * interline and min(r.x1, s.x1) - max(r.x0, s.x0) > 0:
                overlap = True
                break
        if not overlap:
            result.append(s)
    return result


def _group_systems(bw: np.ndarray, staves: list[Staff], interline: float) -> list[System]:
    systems: list[list[int]] = [[0]]
    for i in range(1, len(staves)):
        a, b = staves[i - 1], staves[i]
        if _connected(bw, a, b, interline):
            systems[-1].append(i)
        else:
            systems.append([i])
    result = []
    for idxs in systems:
        sts = [staves[i] for i in idxs]
        result.append(System(idxs, min(s.x0 for s in sts), max(s.x1 for s in sts),
                             min(s.top for s in sts), max(s.bottom for s in sts)))
    return result


def _connected(bw: np.ndarray, a: Staff, b: Staff, interline: float) -> bool:
    h, w = bw.shape
    gap = b.top - a.bottom
    if gap <= 0 or gap > 4.5 * max(a.height, 1):
        return gap <= 0
    x_start = min(a.x0, b.x0)
    xa = int(max(0, x_start - 3.0 * interline))
    xb = int(min(w, x_start + 1.0 * interline))
    ya = int(a.y(4, a.x0) + 1)
    yb = int(b.y(0, b.x0) - 1)
    if yb <= ya or xb <= xa:
        return False
    region = bw[ya:yb, xa:xb]
    rows_covered = (region.max(axis=1) > 0).mean()
    if rows_covered < 0.97:
        return False
    # Debe existir continuidad: el componente conectado debe tocar ambos pentagramas.
    region = bw[max(0, ya - 2):min(h, yb + 2), xa:xb]
    n, labels = cv2.connectedComponents(region.astype(np.uint8), connectivity=8)
    top_labels = set(np.unique(labels[0])) - {0}
    bottom_labels = set(np.unique(labels[-1])) - {0}
    if top_labels & bottom_labels:
        return True
    # Llaves partidas por el escaneo: aceptar si la cobertura de filas es total.
    return rows_covered >= 0.995


def _find_barlines(bw: np.ndarray, staves: list[Staff], sys: System, interline: float, thickness: float) -> list[float]:
    h, w = bw.shape
    kv = int(max(5, interline * 2.2))
    x0, x1 = int(max(0, sys.x0 - 2)), int(min(w, sys.x1 + 3))
    y0, y1 = int(max(0, sys.y0 - 2)), int(min(h, sys.y1 + 3))
    region = bw[y0:y1, x0:x1]
    vert = cv2.morphologyEx(region, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, kv)))
    ok = np.ones(x1 - x0, dtype=bool)
    for si in sys.staves:
        s = staves[si]
        ya = int(round(s.y(0) - y0)) + 1
        yb = int(round(s.y(4) - y0))
        if yb - ya < 3:
            continue
        cover = vert[max(0, ya):max(ya + 1, yb), :].mean(axis=0)
        ok &= cover >= 0.94
    if len(sys.staves) >= 2:
        # Para sistemas de varios pentagramas, la barra suele cruzar el hueco entre ellos.
        cross = np.ones(x1 - x0, dtype=bool)
        for i in range(len(sys.staves) - 1):
            a, b = staves[sys.staves[i]], staves[sys.staves[i + 1]]
            ya = int(round(a.y(4) - y0)) + 2
            yb = int(round(b.y(0) - y0)) - 2
            if yb > ya:
                cross &= vert[ya:yb, :].mean(axis=0) >= 0.9
        # Si en esta edición las barras cruzan el hueco, se exige; si no, se aceptan por pentagrama.
        if cross[ok].any():
            ok &= cross
    diff = np.diff(np.concatenate(([0], ok.astype(np.int8), [0])))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    xs = []
    for s, e in zip(starts, ends):
        width = e - s
        if width > max(interline * 1.3, thickness * 6):
            continue
        xs.append(x0 + (s + e - 1) / 2)
    merged: list[float] = []
    for x in sorted(xs):
        if merged and x - merged[-1] < interline * 1.2:
            merged[-1] = max(merged[-1], x)  # barra doble/final: se queda con la derecha
        else:
            merged.append(x)
    # descartar la barra inicial del sistema (pegada al comienzo de los pentagramas)
    merged = [x for x in merged if x - sys.x0 > interline * 1.5]
    if merged and sys.x1 - merged[-1] < interline * 1.5:
        merged[-1] = sys.x1
    elif not merged or sys.x1 - merged[-1] >= interline * 1.5:
        merged.append(sys.x1)
    return merged


def orientation_score(bw: np.ndarray, geom: PageGeometry) -> float:
    """> 0 si los pentagramas tienen más tinta al inicio (clave) que al final."""
    if not geom.staves:
        return 0.0
    score = 0.0
    for s in geom.staves:
        span = 5 * s.interline
        ya = int(max(0, s.top - 2 * s.interline))
        yb = int(min(bw.shape[0], s.bottom + 2 * s.interline))
        left = bw[ya:yb, int(s.x0):int(min(bw.shape[1], s.x0 + span))].mean()
        right = bw[ya:yb, int(max(0, s.x1 - span)):int(s.x1)].mean()
        score += left - right
    return score / len(geom.staves)
