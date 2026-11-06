"""Modelo musical interno.

Representa la partitura reconocida de forma independiente del motor OMR. Las
posiciones temporales se expresan en *negras* (``Fraction``) relativas al
inicio de cada compás, lo que evita errores de redondeo con tresillos y
puntillos. Las alturas se guardan como ortografía escrita (step/alter/octave)
y como número MIDI sonoro (incluye transposición de instrumento).

Las posiciones gráficas se guardan normalizadas (0..1) respecto de la página
original para no depender de la resolución con la que se rasterizó.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field, fields
from fractions import Fraction
from typing import Any, Iterable

from note_cascade.music_model import pitch as P

DYNAMIC_LEVELS = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff"]
ACCENT_DYNAMICS = {"sf", "sfz", "sffz", "fz", "rfz", "rf", "sfp", "fp", "sfzp"}


def frac(value: Any) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(value).limit_denominator(3840)
    return Fraction(value)


def frac_str(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}" if value.denominator != 1 else str(value.numerator)


@dataclass
class PagePosition:
    """Rectángulo normalizado (0..1) sobre la página original."""

    page: int
    x: float
    y: float
    w: float
    h: float
    approximate: bool = False

    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def to_dict(self) -> dict[str, Any]:
        return {"page": self.page, "x": round(self.x, 6), "y": round(self.y, 6),
                "w": round(self.w, 6), "h": round(self.h, 6), "approximate": self.approximate}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "PagePosition | None":
        if not d:
            return None
        return cls(int(d["page"]), float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]),
                   bool(d.get("approximate", False)))


def new_id(prefix: str = "n") -> str:
    """Identificador único y estable (no se repite al cargar proyectos guardados)."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


@dataclass
class Note:
    id: str
    part: int
    staff: int
    voice: int
    measure: int
    onset: Fraction
    duration: Fraction
    rest: bool = False
    step: str = "C"
    alter: int = 0
    octave: int = 4
    midi: int = 60
    accidental_shown: str | None = None
    tie_start: bool = False
    tie_stop: bool = False
    grace: bool = False
    type_name: str | None = None
    dots: int = 0
    tuplet: tuple[int, int] | None = None
    articulations: list[str] = field(default_factory=list)
    hand: str | None = None
    confidence: float | None = None
    source: str = ""
    position: PagePosition | None = None
    user_modified: bool = False
    lyric: str | None = None
    measure_rest: bool = False
    cue: bool = False
    velocity: int | None = None  # solo para MIDI importado o ajuste manual
    image_verified: bool = False  # la cabeza está en la imagen, a la altura que corresponde a la nota
    position_consistent: bool = False  # su posición en el pentagrama (clave y líneas) da exactamente esa altura
    engine_midi: int | None = None  # altura tal como la entregó el motor de reconocimiento (sin cambios)
    pitch_log: list[str] = field(default_factory=list)  # cada cambio de altura posterior y su motivo

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration

    def name(self, style: str = "es") -> str:
        if self.rest:
            return "silencio"
        return P.note_name(self.step, self.alter, self.octave, style)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, Fraction):
                v = frac_str(v)
            elif isinstance(v, PagePosition):
                v = v.to_dict()
            elif isinstance(v, tuple):
                v = list(v)
            d[f.name] = v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Note":
        kwargs = dict(d)
        kwargs["onset"] = frac(kwargs["onset"])
        kwargs["duration"] = frac(kwargs["duration"])
        kwargs["position"] = PagePosition.from_dict(kwargs.get("position"))
        if kwargs.get("tuplet"):
            kwargs["tuplet"] = tuple(kwargs["tuplet"])
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in kwargs.items() if k in known})


def log_pitch_change(n: "Note", old_midi: int, reason: str) -> None:
    """Registra en la nota un cambio de altura y su motivo (para el diagnóstico de alturas)."""
    if n.midi != old_midi:
        n.pitch_log.append(f"{reason}: MIDI {old_midi} → {n.midi}")


@dataclass
class ClefChange:
    part: int
    staff: int
    measure: int
    onset: Fraction
    sign: str
    line: int
    octave_change: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"part": self.part, "staff": self.staff, "measure": self.measure, "onset": frac_str(self.onset),
                "sign": self.sign, "line": self.line, "octave_change": self.octave_change}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ClefChange":
        return cls(int(d["part"]), int(d["staff"]), int(d["measure"]), frac(d["onset"]), d["sign"],
                   int(d["line"]), int(d.get("octave_change", 0)))


@dataclass
class KeyChange:
    part: int
    measure: int
    fifths: int
    mode: str = "major"
    staff: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"part": self.part, "measure": self.measure, "fifths": self.fifths, "mode": self.mode,
                "staff": self.staff}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KeyChange":
        return cls(int(d["part"]), int(d["measure"]), int(d["fifths"]), d.get("mode", "major"), d.get("staff"))


@dataclass
class Direction:
    """Indicación asociada a un instante: dinámica, regulador, pedal, texto...

    kind: dynamic | wedge | pedal | words | octave_shift | rehearsal | tempo_change
    value: p.ej. "pp", "crescendo", "start", "stop", "8va", "rit."
    """

    kind: str
    measure: int
    onset: Fraction
    part: int = 0
    staff: int | None = None
    value: str = ""
    text: str = ""
    size: int = 0
    placement: str = ""
    source: str = ""
    position: PagePosition | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "measure": self.measure, "onset": frac_str(self.onset), "part": self.part,
                "staff": self.staff, "value": self.value, "text": self.text, "size": self.size,
                "placement": self.placement, "source": self.source,
                "position": self.position.to_dict() if self.position else None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Direction":
        return cls(d["kind"], int(d["measure"]), frac(d["onset"]), int(d.get("part", 0)), d.get("staff"),
                   d.get("value", ""), d.get("text", ""), int(d.get("size", 0)), d.get("placement", ""),
                   d.get("source", ""), PagePosition.from_dict(d.get("position")))


@dataclass
class TempoMark:
    measure: int
    onset: Fraction
    bpm_quarter: float | None
    text: str = ""
    beat_unit: str = "quarter"
    beat_dots: int = 0
    per_minute: float | None = None
    # motor | texto | usuario | predeterminado | sonido
    source: str = "motor"

    def to_dict(self) -> dict[str, Any]:
        return {"measure": self.measure, "onset": frac_str(self.onset), "bpm_quarter": self.bpm_quarter,
                "text": self.text, "beat_unit": self.beat_unit, "beat_dots": self.beat_dots,
                "per_minute": self.per_minute, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TempoMark":
        return cls(int(d["measure"]), frac(d["onset"]), d.get("bpm_quarter"), d.get("text", ""),
                   d.get("beat_unit", "quarter"), int(d.get("beat_dots", 0)), d.get("per_minute"),
                   d.get("source", "motor"))


@dataclass
class ChordSymbol:
    measure: int
    onset: Fraction
    part: int
    root_step: str
    root_alter: int
    kind: str
    text: str
    bass_step: str | None = None
    bass_alter: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"measure": self.measure, "onset": frac_str(self.onset), "part": self.part,
                "root_step": self.root_step, "root_alter": self.root_alter, "kind": self.kind,
                "text": self.text, "bass_step": self.bass_step, "bass_alter": self.bass_alter}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChordSymbol":
        return cls(int(d["measure"]), frac(d["onset"]), int(d["part"]), d["root_step"], int(d["root_alter"]),
                   d["kind"], d.get("text", ""), d.get("bass_step"), int(d.get("bass_alter", 0)))


@dataclass
class Measure:
    index: int
    number: str
    time_sig: tuple[int, int] | None = None
    time_sig_change: bool = False
    implicit: bool = False
    repeat_start: bool = False
    repeat_end: bool = False
    repeat_times: int = 2
    ending_numbers: list[int] = field(default_factory=list)
    ending_type: str = ""          # start | stop | discontinue | start-stop
    navigation: list[str] = field(default_factory=list)  # segno, coda, fine, dacapo, dalsegno, tocoda
    barline_right: str = ""
    new_system: bool = False
    new_page: bool = False
    page: int | None = None
    system: int | None = None
    position: PagePosition | None = None

    def expected_duration(self) -> Fraction | None:
        if not self.time_sig:
            return None
        num, den = self.time_sig
        return Fraction(num * 4, den)

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, PagePosition):
                v = v.to_dict()
            elif isinstance(v, tuple):
                v = list(v)
            d[f.name] = v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Measure":
        kwargs = dict(d)
        kwargs["position"] = PagePosition.from_dict(kwargs.get("position"))
        if kwargs.get("time_sig"):
            kwargs["time_sig"] = tuple(kwargs["time_sig"])
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in kwargs.items() if k in known})


@dataclass
class PartInfo:
    id: str
    name: str = ""
    abbreviation: str = ""
    staves: int = 1
    midi_program: int = 0
    is_piano: bool = False
    transpose_chromatic: int = 0
    transpose_octave: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PartInfo":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Score:
    title: str = ""
    subtitle: str = ""
    composer: str = ""
    arranger: str = ""
    lyricist: str = ""
    parts: list[PartInfo] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    clefs: list[ClefChange] = field(default_factory=list)
    keys: list[KeyChange] = field(default_factory=list)
    directions: list[Direction] = field(default_factory=list)
    tempos: list[TempoMark] = field(default_factory=list)
    chord_symbols: list[ChordSymbol] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ consultas
    def notes_in_measure(self, measure: int, include_rests: bool = True) -> list[Note]:
        return [n for n in self.notes if n.measure == measure and (include_rests or not n.rest)]

    def pitched_notes(self) -> list[Note]:
        return [n for n in self.notes if not n.rest]

    def note_by_id(self, note_id: str) -> Note | None:
        for n in self.notes:
            if n.id == note_id:
                return n
        return None

    def key_at(self, part: int, measure: int, staff: int | None = None) -> int:
        fifths = 0
        best = -1
        for k in self.keys:
            if k.part != part or k.measure > measure:
                continue
            if k.staff is not None and staff is not None and k.staff != staff:
                continue
            if k.measure >= best:
                best = k.measure
                fifths = k.fifths
        return fifths

    def clef_at(self, part: int, staff: int, measure: int, onset: Fraction = Fraction(0)) -> ClefChange | None:
        found = None
        for c in self.clefs:
            if c.part == part and c.staff == staff and (c.measure, c.onset) <= (measure, onset):
                if found is None or (c.measure, c.onset) >= (found.measure, found.onset):
                    found = c
        return found

    def time_sig_at(self, measure: int) -> tuple[int, int] | None:
        if 0 <= measure < len(self.measures):
            return self.measures[measure].time_sig
        return None

    def staff_count(self) -> int:
        return sum(p.staves for p in self.parts)

    def measure_content_duration(self, measure: int) -> Fraction:
        end = Fraction(0)
        for n in self.notes:
            if n.measure == measure and not n.grace:
                end = max(end, n.end)
        return end

    def is_piano_score(self) -> bool:
        return any(p.is_piano for p in self.parts)

    # ------------------------------------------------------------------ utilidades
    def clone(self) -> "Score":
        return copy.deepcopy(self)

    def sort_notes(self) -> None:
        self.notes.sort(key=lambda n: (n.measure, n.onset, n.part, n.staff, n.voice, n.grace is False, -n.midi))

    def renumber_measures(self) -> None:
        for i, m in enumerate(self.measures):
            m.index = i

    def iter_measures(self) -> Iterable[Measure]:
        return iter(self.measures)

    # ------------------------------------------------------------------ JSON
    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "subtitle": self.subtitle, "composer": self.composer,
            "arranger": self.arranger, "lyricist": self.lyricist,
            "parts": [p.to_dict() for p in self.parts],
            "measures": [m.to_dict() for m in self.measures],
            "notes": [n.to_dict() for n in self.notes],
            "clefs": [c.to_dict() for c in self.clefs],
            "keys": [k.to_dict() for k in self.keys],
            "directions": [d.to_dict() for d in self.directions],
            "tempos": [t.to_dict() for t in self.tempos],
            "chord_symbols": [c.to_dict() for c in self.chord_symbols],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Score":
        return cls(
            title=d.get("title", ""), subtitle=d.get("subtitle", ""), composer=d.get("composer", ""),
            arranger=d.get("arranger", ""), lyricist=d.get("lyricist", ""),
            parts=[PartInfo.from_dict(x) for x in d.get("parts", [])],
            measures=[Measure.from_dict(x) for x in d.get("measures", [])],
            notes=[Note.from_dict(x) for x in d.get("notes", [])],
            clefs=[ClefChange.from_dict(x) for x in d.get("clefs", [])],
            keys=[KeyChange.from_dict(x) for x in d.get("keys", [])],
            directions=[Direction.from_dict(x) for x in d.get("directions", [])],
            tempos=[TempoMark.from_dict(x) for x in d.get("tempos", [])],
            chord_symbols=[ChordSymbol.from_dict(x) for x in d.get("chord_symbols", [])],
            metadata=dict(d.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# Duraciones escritas
# ---------------------------------------------------------------------------

TYPE_QUARTERS: dict[str, Fraction] = {
    "maxima": Fraction(32), "long": Fraction(16), "breve": Fraction(8), "whole": Fraction(4),
    "half": Fraction(2), "quarter": Fraction(1), "eighth": Fraction(1, 2), "16th": Fraction(1, 4),
    "32nd": Fraction(1, 8), "64th": Fraction(1, 16), "128th": Fraction(1, 32), "256th": Fraction(1, 64),
}

TYPE_NAME_ES = {
    "breve": "cuadrada", "whole": "redonda", "half": "blanca", "quarter": "negra", "eighth": "corchea",
    "16th": "semicorchea", "32nd": "fusa", "64th": "semifusa", "128th": "garrapatea",
}


def dotted(base: Fraction, dots: int) -> Fraction:
    total = base
    add = base
    for _ in range(dots):
        add /= 2
        total += add
    return total


def describe_duration(quarters: Fraction, tuplet: tuple[int, int] | None = None) -> str:
    """Nombre en español de una duración expresada en negras."""
    if quarters <= 0:
        return "sin duración (nota de adorno)"
    base_q = quarters
    if tuplet:
        base_q = quarters * Fraction(tuplet[0], tuplet[1])
    for type_name, q in TYPE_QUARTERS.items():
        for dots in range(3):
            if dotted(q, dots) == base_q and type_name in TYPE_NAME_ES:
                name = TYPE_NAME_ES[type_name]
                if dots == 1:
                    name += " con puntillo"
                elif dots == 2:
                    name += " con doble puntillo"
                if tuplet:
                    name += f" (grupo de {tuplet[0]}:{tuplet[1]})"
                return name
    beats = float(quarters)
    return f"{beats:g} tiempos de negra"


def notation_for_duration(quarters: Fraction) -> tuple[str, int, tuple[int, int] | None] | None:
    """Busca figura, puntillos y tresillo que representen una duración exacta."""
    for type_name, q in TYPE_QUARTERS.items():
        for dots in range(3):
            if dotted(q, dots) == quarters:
                return type_name, dots, None
    for actual, normal in ((3, 2), (5, 4), (6, 4), (7, 4), (3, 4)):
        base_q = quarters * Fraction(actual, normal)
        for type_name, q in TYPE_QUARTERS.items():
            for dots in range(2):
                if dotted(q, dots) == base_q:
                    return type_name, dots, (actual, normal)
    return None


def split_duration(quarters: Fraction) -> list[Fraction]:
    """Divide una duración no representable en partes representables (ligadas)."""
    parts: list[Fraction] = []
    remaining = quarters
    candidates = sorted({dotted(q, d) for q in TYPE_QUARTERS.values() for d in range(2)}, reverse=True)
    guard = 0
    while remaining > 0 and guard < 64:
        guard += 1
        for c in candidates:
            if c <= remaining:
                parts.append(c)
                remaining -= c
                break
        else:
            parts.append(remaining)
            break
    return parts
