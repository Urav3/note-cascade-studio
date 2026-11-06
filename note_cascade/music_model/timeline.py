"""Construcción de la interpretación temporal (Performance) a partir del modelo.

La misma Performance alimenta el reproductor, el visualizador en cascada y el
exportador MIDI, de modo que lo que se escucha es exactamente lo que se
exporta. Incluye:

* longitudes de compás (con anacrusa y compases incompletos),
* orden de reproducción con repeticiones, casillas, D.C., D.S., Fine y Coda,
* fusión de ligaduras de prolongación (sin segundo ataque),
* mapa de tempo (marcas de metrónomo, rit./accel. con destino conocido, calderones),
* velocidades aproximadas a partir de dinámicas, reguladores y acentos,
* acordes arpegiados (de abajo arriba, cada nota sostenida hasta el final del acorde),
* eventos de pedal de resonancia.
"""

from __future__ import annotations

import bisect
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Iterable

from note_cascade.music_model import terms
from note_cascade.music_model.arpeggio import roll_offset_s, roll_ranks
from note_cascade.music_model.hands import hand_of
from note_cascade.music_model.model import ACCENT_DYNAMICS, Score

log = logging.getLogger(__name__)

DYNAMIC_VELOCITY = {
    "pppp": 16, "ppp": 24, "pp": 36, "p": 50, "mp": 64, "mf": 78, "f": 92, "ff": 106, "fff": 118, "ffff": 127,
    "fp": 92, "sfp": 100, "sf": 104, "sfz": 108, "sffz": 116, "fz": 104, "rfz": 104, "rf": 100, "sfzp": 104,
}
DEFAULT_VELOCITY = 78


@dataclass
class PerformanceOptions:
    expand_repeats: bool = True
    default_tempo: float = 100.0
    dynamics: bool = True
    articulations: bool = True
    fermata_stretch: bool = True
    pedal: bool = True
    chord_symbols_to_notes: bool = False
    # Solo para escuchar y ver: «no», «auto» (una octava aparte solo en los pasajes donde las manos se
    # juntan) o «siempre». Las exportaciones siempre llevan las notas escritas.
    separate_hands: str = "no"


@dataclass
class PlayMeasure:
    measure: int
    start_q: float
    length_q: float
    pass_number: int = 1


@dataclass
class PerfNote:
    start_q: float
    dur_q: float
    midi: int
    velocity: int
    part: int
    staff: int
    hand: str | None
    measure: int
    play_index: int
    note_ids: tuple[str, ...]
    start_s: float = 0.0
    end_s: float = 0.0
    lyric: str | None = None
    track: str = ""
    silent: bool = False   # unísono con la otra mano: se ve pero no vuelve a sonar

    @property
    def end_q(self) -> float:
        return self.start_q + self.dur_q


@dataclass
class Performance:
    play_measures: list[PlayMeasure] = field(default_factory=list)
    notes: list[PerfNote] = field(default_factory=list)
    tempo_events: list[tuple[float, float]] = field(default_factory=list)  # (q, bpm de negra)
    pedal_events: list[tuple[float, int, int]] = field(default_factory=list)  # (q, valor, parte)
    markers: list[tuple[float, str, str]] = field(default_factory=list)  # (q, tipo, texto)
    time_sigs: list[tuple[float, int, int]] = field(default_factory=list)
    key_sigs: list[tuple[float, int]] = field(default_factory=list)
    total_q: float = 0.0
    notes_info: list[str] = field(default_factory=list)  # avisos (ambigüedades de repeticiones, etc.)
    expanded: bool = False
    _seg_q: list[float] = field(default_factory=list, repr=False)
    _seg_s: list[float] = field(default_factory=list, repr=False)
    _seg_bpm: list[float] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------ tiempo
    def build_time_map(self) -> None:
        events = sorted(self.tempo_events, key=lambda e: e[0]) or [(0.0, 100.0)]
        if events[0][0] > 0:
            events.insert(0, (0.0, events[0][1]))
        self._seg_q, self._seg_s, self._seg_bpm = [], [], []
        t = 0.0
        for i, (q, bpm) in enumerate(events):
            if self._seg_q:
                prev_q, prev_bpm = self._seg_q[-1], self._seg_bpm[-1]
                t += (q - prev_q) * 60.0 / prev_bpm
                if q == prev_q:
                    self._seg_bpm[-1] = bpm
                    continue
            self._seg_q.append(q)
            self._seg_s.append(t)
            self._seg_bpm.append(max(1.0, bpm))
        for n in self.notes:
            n.start_s = self.q_to_s(n.start_q)
            n.end_s = self.q_to_s(n.start_q + n.dur_q)

    def q_to_s(self, q: float) -> float:
        if not self._seg_q:
            return q * 60.0 / 100.0
        i = max(0, bisect.bisect_right(self._seg_q, q) - 1)
        return self._seg_s[i] + (q - self._seg_q[i]) * 60.0 / self._seg_bpm[i]

    def s_to_q(self, s: float) -> float:
        if not self._seg_s:
            return s * 100.0 / 60.0
        i = max(0, bisect.bisect_right(self._seg_s, s) - 1)
        return self._seg_q[i] + (s - self._seg_s[i]) * self._seg_bpm[i] / 60.0

    def bpm_at(self, q: float) -> float:
        if not self._seg_q:
            return 100.0
        i = max(0, bisect.bisect_right(self._seg_q, q) - 1)
        return self._seg_bpm[i]

    @property
    def total_seconds(self) -> float:
        end_q = self.total_q
        if self.notes:
            end_q = max(end_q, max(n.end_q for n in self.notes))
        return self.q_to_s(end_q)

    def play_index_at_q(self, q: float) -> int:
        starts = [pm.start_q for pm in self.play_measures]
        return max(0, bisect.bisect_right(starts, q) - 1) if starts else 0

    def play_index_at_s(self, s: float) -> int:
        return self.play_index_at_q(self.s_to_q(s))

    def measure_start_s(self, play_index: int) -> float:
        if not self.play_measures:
            return 0.0
        play_index = max(0, min(play_index, len(self.play_measures) - 1))
        return self.q_to_s(self.play_measures[play_index].start_q)

    def measure_end_s(self, play_index: int) -> float:
        if not self.play_measures:
            return 0.0
        play_index = max(0, min(play_index, len(self.play_measures) - 1))
        pm = self.play_measures[play_index]
        return self.q_to_s(pm.start_q + pm.length_q)

    def first_play_index_of_measure(self, measure: int) -> int | None:
        for i, pm in enumerate(self.play_measures):
            if pm.measure == measure:
                return i
        return None


# ---------------------------------------------------------------------------
# Longitudes de compás y orden de reproducción
# ---------------------------------------------------------------------------

def measure_lengths(score: Score) -> list[Fraction]:
    content = defaultdict(lambda: Fraction(0))
    for n in score.notes:
        if not n.grace:
            content[n.measure] = max(content[n.measure], n.end)
    lengths: list[Fraction] = []
    prev = Fraction(4)
    for m in score.measures:
        expected = m.expected_duration()
        c = content[m.index]
        if expected is None:
            length = c if c > 0 else prev
        elif c > expected:
            length = c  # compás sobrecompleto: no se recortan notas (la validación lo señala)
        elif (m.implicit or m.index == 0) and 0 < c < expected:
            length = c  # anacrusa
        else:
            length = expected
        if length <= 0:
            length = prev
        lengths.append(length)
        prev = length
    return lengths


def _ending_ranges(score: Score) -> list[tuple[int, int, list[int]]]:
    ranges = []
    open_start: int | None = None
    open_numbers: list[int] = []
    for m in score.measures:
        et = m.ending_type
        if "start" in et:
            open_start = m.index
            open_numbers = list(m.ending_numbers)
        if open_start is not None and ("stop" in et or "discontinue" in et):
            ranges.append((open_start, m.index, open_numbers or list(m.ending_numbers)))
            open_start = None
            open_numbers = []
    if open_start is not None:
        ranges.append((open_start, open_start, open_numbers))
    # Una casilla de 1.ª/2.ª vez solo tiene sentido con una barra de repetición: la 1.ª casilla termina
    # volviendo atrás. Corchetes sin repetición (p. ej. una línea «8va - - -┐» que el motor tomó por una
    # casilla) no deben hacer saltar compases.
    valid = []
    for start, end, numbers in ranges:
        first_ending = 1 in numbers or not numbers
        if first_ending and not any(score.measures[k].repeat_end for k in range(start, end + 1)):
            continue
        if not first_ending and not any(r[2] and 1 in r[2] and r[1] < start for r in valid):
            continue
        valid.append((start, end, numbers))
    return valid


def play_order(score: Score, expand: bool = True) -> tuple[list[tuple[int, int]], list[str]]:
    """Devuelve [(índice de compás, número de pasada)] y una lista de avisos."""
    n = len(score.measures)
    if not expand or n == 0:
        return [(i, 1) for i in range(n)], []
    notes: list[str] = []
    M = score.measures
    endings = _ending_ranges(score)
    ending_at = {start: (end, nums) for start, end, nums in endings}
    segno = next((m.index for m in M if "segno" in m.navigation), None)
    coda_targets = [m.index for m in M if "coda" in m.navigation]

    order: list[tuple[int, int]] = []
    i = 0
    section_start = 0
    pass_no = 1
    returning = False
    repeat_counts: dict[int, int] = defaultdict(int)
    jumped = False
    jumped_to_coda = False
    guard = 0
    limit = max(1000, n * 12)
    while i < n and guard < limit:
        guard += 1
        m = M[i]
        if m.repeat_start and not returning:
            section_start = i
            pass_no = 1
        returning = False
        if i in ending_at and not jumped:
            end, numbers = ending_at[i]
            if numbers and pass_no not in numbers:
                i = end + 1
                continue
        order.append((i, pass_no))
        nav = m.navigation
        if jumped and any(x == "fine" for x in nav):
            break
        if jumped and not jumped_to_coda and "tocoda" in nav:
            target = next((c for c in coda_targets if c > i), None)
            if target is None:
                notes.append(f"Hay «To Coda» en el compás {m.number} pero no se encontró la Coda; se continúa.")
            else:
                jumped_to_coda = True
                i = target
                returning = True
                continue
        if m.repeat_end and not jumped:
            times = max(2, m.repeat_times)
            if repeat_counts[i] < times - 1:
                repeat_counts[i] += 1
                pass_no = repeat_counts[i] + 1
                i = section_start
                returning = True
                continue
            section_start = i + 1
            pass_no = 1  # terminada la repetición, lo que sigue se toca por primera vez
        if not jumped and any(x.startswith("dacapo") for x in nav):
            jumped = True
            i = 0
            pass_no = 1
            returning = True
            continue
        if not jumped and any(x.startswith("dalsegno") for x in nav):
            if segno is None:
                notes.append(f"Hay «D.S.» en el compás {m.number} pero no se encontró el signo (segno); "
                             "no se realiza el salto.")
            else:
                jumped = True
                i = segno
                pass_no = 1
                returning = True
                continue
        i += 1
    if guard >= limit:
        notes.append("Las repeticiones forman un ciclo que no se puede resolver; se reproduce sin expandir.")
        return [(k, 1) for k in range(n)], notes
    return order, notes


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def build_performance(score: Score, options: PerformanceOptions | None = None) -> Performance:
    opt = options or PerformanceOptions()
    perf = Performance()
    lengths = measure_lengths(score)
    order, info = play_order(score, opt.expand_repeats)
    perf.notes_info.extend(info)
    perf.expanded = opt.expand_repeats and len(order) != len(score.measures)

    q = 0.0
    for idx, pass_no in order:
        perf.play_measures.append(PlayMeasure(idx, q, float(lengths[idx]), pass_no))
        q += float(lengths[idx])
    perf.total_q = q

    # Compases y armaduras (en el orden de reproducción)
    last_ts = None
    last_key = None
    for pm in perf.play_measures:
        m = score.measures[pm.measure]
        if m.time_sig and m.time_sig != last_ts:
            perf.time_sigs.append((pm.start_q, m.time_sig[0], m.time_sig[1]))
            last_ts = m.time_sig
        fifths = score.key_at(0, pm.measure) if score.parts else 0
        if fifths != last_key:
            perf.key_sigs.append((pm.start_q, fifths))
            last_key = fifths

    by_measure: dict[int, list] = defaultdict(list)
    for n in score.notes:
        by_measure[n.measure].append(n)
    dirs_by_measure: dict[int, list] = defaultdict(list)
    for d in score.directions:
        dirs_by_measure[d.measure].append(d)
    tempos_by_measure: dict[int, list] = defaultdict(list)
    for t in score.tempos:
        tempos_by_measure[t.measure].append(t)

    _build_tempo_map(score, perf, opt, tempos_by_measure, dirs_by_measure, by_measure)
    velocity_fn = _build_velocity_function(score, perf, opt, dirs_by_measure)

    raw: list[PerfNote] = []
    tie_flags: dict[int, tuple[bool, bool]] = {}
    rolls = roll_ranks(score) if opt.articulations else {}
    for play_index, pm in enumerate(perf.play_measures):
        measure_notes = [n for n in by_measure.get(pm.measure, []) if not n.rest]
        grace_groups: dict[tuple, list] = defaultdict(list)
        for n in measure_notes:
            if n.grace:
                grace_groups[(n.part, n.staff, n.voice, n.onset)].append(n)
        for n in measure_notes:
            part = score.parts[n.part] if n.part < len(score.parts) else None
            is_piano = bool(part and part.is_piano)
            start = pm.start_q + float(n.onset)
            dur = float(n.duration)
            if n.grace:
                group = grace_groups[(n.part, n.staff, n.voice, n.onset)]
                k = len(group) - group.index(n)
                g = min(0.125, 0.5 / max(1, len(group)))
                start = max(0.0, start - k * g)
                dur = g
            elif n.id in rolls:
                # acorde arpegiado: entra de abajo arriba y cada nota se mantiene hasta el final del acorde
                rank, count, shortest = rolls[n.id]
                bpm = _tempo_at(perf.tempo_events, start)
                dq = roll_offset_s(rank, count, float(shortest) * 60.0 / bpm) * bpm / 60.0
                start += dq
                dur -= dq
            vel = float(n.velocity) if n.velocity else velocity_fn(n.part, start)
            if opt.articulations:
                arts = set(n.articulations)
                if "staccatissimo" in arts:
                    dur *= 0.3
                elif "staccato" in arts or "spiccato" in arts:
                    dur *= 0.5
                if "marcato" in arts:
                    vel += 20
                elif "accent" in arts:
                    vel += 14
            if dur <= 0:
                continue
            pn = PerfNote(start, dur, n.midi, max(1, min(127, int(round(vel)))), n.part, n.staff,
                          hand_of(n, is_piano), n.measure, play_index, (n.id,), lyric=n.lyric)
            raw.append(pn)
            tie_flags[id(pn)] = (n.tie_start, n.tie_stop)

    if opt.separate_hands in ("auto", "siempre"):
        _separate_hands(raw, opt.separate_hands, perf, score)
    perf.notes = _merge_unisons(_merge_ties(raw, tie_flags), perf)
    _rearticulate(perf.notes, perf)
    if opt.chord_symbols_to_notes:
        perf.notes.extend(_chord_symbol_notes(score, perf))
    perf.notes.sort(key=lambda x: (x.start_q, x.midi))

    if opt.pedal:
        _build_pedal(score, perf, dirs_by_measure)
    _build_markers(score, perf, dirs_by_measure)
    perf.build_time_map()
    return perf


REARTICULATION_GAP_Q = 0.03   # silencio mínimo (en negras) entre soltar una tecla y volver a pulsarla


def _rearticulate(notes: list[PerfNote], perf: Performance) -> None:
    """Una tecla sostenida no puede volver a pulsarse sin soltarla antes (sea con la misma mano o con la
    otra). Si la partitura escribe una nota larga y, mientras suena, otra nota en la misma tecla, la larga se
    termina justo antes del nuevo golpe, como haría un pianista. Solo cambia lo que se oye, el MIDI y la
    cascada; la partitura conserva lo escrito."""
    by_key: dict[tuple[int, int], list[PerfNote]] = defaultdict(list)
    for n in notes:
        by_key[(n.part, n.midi)].append(n)
    cut = 0
    for group in by_key.values():
        group.sort(key=lambda x: (x.start_q, -x.dur_q))
        for prev, nxt in zip(group, group[1:]):
            if nxt.start_q <= prev.start_q or prev.start_q + prev.dur_q <= nxt.start_q:
                continue
            gap = min(REARTICULATION_GAP_Q, (nxt.start_q - prev.start_q) / 4)
            prev.dur_q = max(0.01, nxt.start_q - prev.start_q - gap)
            cut += 1
    if cut:
        perf.notes_info.append(f"{cut} nota(s) sostenida(s) se sueltan justo antes de que se vuelva a tocar la misma "
                               "tecla (con la misma mano o con la otra), como en un piano real.")


CLOSE_SEMITONES = 2   # manos a 2 semitonos o menos (o cruzadas) a la vez = «se juntan»


def _separate_hands(raw: list[PerfNote], mode: str, perf: Performance, score: Score) -> None:
    """Separa las manos una octava para verlas y oírlas mejor (no cambia la partitura).

    «auto»: solo en los pasajes donde, a la vez, la izquierda llega a menos de 3 semitonos de la
    derecha o la cruza. El pasaje se toma entero (uniendo compases sueltos entre medias) para que la
    mano no salte de octava compás a compás. Se baja la izquierda; si ya no cabe en el teclado, se
    sube la derecha. «siempre»: lo mismo en toda la pieza."""
    by_pass: dict[int, list[PerfNote]] = defaultdict(list)
    for n in raw:
        if n.hand in ("R", "L"):
            by_pass[n.play_index].append(n)
    if not by_pass:
        return
    indices = sorted(by_pass)
    if mode == "siempre":
        passages = [indices]
    else:
        colliding = []
        for k in indices:
            right = [n for n in by_pass[k] if n.hand == "R"]
            left = [n for n in by_pass[k] if n.hand == "L"]
            if any(l.midi >= r.midi - CLOSE_SEMITONES and l.start_q < r.end_q and r.start_q < l.end_q
                   for l in left for r in right):
                colliding.append(k)
        passages = []
        for k in colliding:
            if passages and k - passages[-1][-1] <= 2:
                passages[-1].extend(range(passages[-1][-1] + 1, k + 1))
            else:
                passages.append([k])
    moved = []
    for passage in passages:
        notes = [n for k in passage for n in by_pass.get(k, [])]
        left = [n for n in notes if n.hand == "L"]
        right = [n for n in notes if n.hand == "R"]
        if left and min(n.midi for n in left) - 12 >= 21:
            for n in left:
                n.midi -= 12
            moved.append((passage, "izquierda una octava más grave"))
        elif right and max(n.midi for n in right) + 12 <= 108:
            for n in right:
                n.midi += 12
            moved.append((passage, "derecha una octava más aguda"))
    if moved:
        def num(k: int) -> str:
            return score.measures[perf.play_measures[k].measure].number if k < len(perf.play_measures) else str(k + 1)
        spans = ", ".join(f"c. {num(p[0])}–{num(p[-1])}" if len(p) > 1 else f"c. {num(p[0])}" for p, _ in moved[:8])
        perf.notes_info.append(f"Manos separadas una octava solo para escuchar y ver en {len(moved)} pasaje(s) "
                               f"({spans}{'…' if len(moved) > 8 else ''}). Lo exportado lleva las notas escritas.")


def _merge_unisons(notes: list[PerfNote], perf: Performance) -> list[PerfNote]:
    """Una misma tecla no puede pulsarse dos veces a la vez.

    Unísono dentro de una misma mano (dos voces): se toca una sola nota (la más larga).
    Unísono entre las dos manos: se conservan las dos notas, para que cada mano muestre lo que
    está escrito en su pentagrama, pero solo suena una (``silent`` en la más corta).
    """
    keep: dict[tuple[int, int, int, str | None], PerfNote] = {}
    by_key: dict[tuple[int, int, int], list[PerfNote]] = {}
    result: list[PerfNote] = []
    merged = 0
    for n in notes:
        tick = int(round(n.start_q * 480))
        key = (n.part, n.midi, tick, n.hand)
        other = keep.get(key)
        if other is None:
            keep[key] = n
            result.append(n)
            by_key.setdefault((n.part, n.midi, tick), []).append(n)
            continue
        merged += 1
        if n.dur_q > other.dur_q:
            other.dur_q = n.dur_q
            other.end_s = n.end_s
        other.velocity = max(other.velocity, n.velocity)
        other.note_ids = other.note_ids + n.note_ids
    doubled = 0
    for group in by_key.values():
        if len(group) < 2:
            continue
        loud = max(group, key=lambda x: (x.dur_q, x.velocity))
        loud.velocity = max(x.velocity for x in group)
        for x in group:
            if x is not loud:
                x.silent = True
                doubled += 1
    if merged:
        perf.notes_info.append(f"{merged} nota(s) escritas a la vez en la misma tecla por la misma mano (unísono "
                               "entre voces) se tocan una sola vez.")
    if doubled:
        perf.notes_info.append(f"{doubled} nota(s) están escritas a la vez en las dos manos (unísono): se ven en las "
                               "dos manos pero la tecla suena una sola vez.")
    return result


def _merge_ties(raw: list[PerfNote], tie_flags: dict[int, tuple[bool, bool]]) -> list[PerfNote]:
    raw.sort(key=lambda x: (x.start_q, x.midi))
    result: list[PerfNote] = []
    open_ties: dict[tuple[int, int], PerfNote] = {}
    eps = 1e-6
    for pn in raw:
        tie_start, tie_stop = tie_flags.get(id(pn), (False, False))
        key = (pn.part, pn.midi)
        prev = open_ties.get(key)
        if tie_stop and prev is not None and abs(prev.end_q - pn.start_q) < 0.02 + eps:
            prev.dur_q = pn.end_q - prev.start_q
            prev.note_ids = prev.note_ids + pn.note_ids
            if not tie_start:
                open_ties.pop(key, None)
            continue
        result.append(pn)
        if tie_start:
            open_ties[key] = pn
        elif key in open_ties and open_ties[key] is not pn:
            open_ties.pop(key, None)
    return result


def _build_tempo_map(score: Score, perf: Performance, opt: PerformanceOptions, tempos_by_measure,
                     dirs_by_measure, by_measure) -> None:
    events: list[tuple[float, float]] = []
    current = None
    # tempo inicial: la primera marca con BPM en el primer compás interpretado
    first_marks = []
    for pm in perf.play_measures[:1]:
        first_marks = [t for t in tempos_by_measure.get(pm.measure, []) if t.bpm_quarter and t.onset == 0]
    if first_marks:
        current = float(first_marks[0].bpm_quarter)
    else:
        current = float(opt.default_tempo)
    events.append((0.0, current))
    base_tempo = current

    # Lista lineal de marcas absolutas (en orden de reproducción)
    abs_marks: list[tuple[float, float | None, str]] = []
    for pm in perf.play_measures:
        for t in tempos_by_measure.get(pm.measure, []):
            abs_marks.append((pm.start_q + float(t.onset), t.bpm_quarter, "mark"))
        for d in dirs_by_measure.get(pm.measure, []):
            if d.kind == "tempo_change":
                abs_marks.append((pm.start_q + float(d.onset), None, d.value))
    abs_marks.sort(key=lambda x: x[0])

    last_bpm = current
    for idx, (q, bpm, kind) in enumerate(abs_marks):
        if kind == "mark" and bpm:
            events.append((q, float(bpm)))
            last_bpm = float(bpm)
            base_tempo = last_bpm
        elif kind in ("ritardando", "accelerando"):
            target = None
            for q2, bpm2, kind2 in abs_marks[idx + 1:]:
                if kind2 == "mark" and bpm2:
                    target = (q2, float(bpm2))
                    break
                if kind2 in ("a_tempo",):
                    target = (q2, base_tempo)
                    break
            if target is None or target[0] - q <= 0 or target[0] - q > 64:
                perf.notes_info.append(
                    f"«{kind}» sin tempo de destino conocido (posición {q:.1f}); se mantiene el tempo.")
                continue
            span = target[0] - q
            steps = max(2, int(span))
            for s in range(steps):
                frac_pos = s / steps
                events.append((q + span * frac_pos, last_bpm + (target[1] - last_bpm) * frac_pos))
        elif kind == "a_tempo":
            events.append((q, base_tempo))
            last_bpm = base_tempo

    # Orden estable: a igual posición manda la última marca añadida (la más reciente en la partitura).
    events = [e for _i, e in sorted(enumerate(events), key=lambda ie: (ie[1][0], ie[0]))]
    if opt.fermata_stretch:
        spans: list[tuple[float, float]] = []
        seen: set[float] = set()
        for pm in perf.play_measures:
            for n in by_measure.get(pm.measure, []):
                if "fermata" in n.articulations and n.duration > 0:
                    q0 = round(pm.start_q + float(n.onset), 4)
                    if q0 in seen:
                        continue
                    seen.add(q0)
                    spans.append((q0, q0 + float(n.duration)))
        if spans:
            events = _apply_fermatas(events, spans)
    perf.tempo_events = events


def _apply_fermatas(base: list[tuple[float, float]], spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Ralentiza a la mitad los tramos con calderón sin alterar el tempo de después.

    Se calcula sobre el mapa de tempo base: al terminar el calderón se vuelve al tempo
    que la partitura indica en ese punto (no al que había antes del calderón), así un
    cambio de tempo que coincide con el final del calderón se respeta.
    """
    def factor(q: float) -> float:
        return 0.5 if any(a <= q < b for a, b in spans) else 1.0

    points = sorted({q for q, _ in base} | {a for a, _ in spans} | {b for _, b in spans})
    result: list[tuple[float, float]] = []
    for q in points:
        bpm = _tempo_at(base, q) * factor(q)
        if not result or abs(result[-1][1] - bpm) > 1e-9:
            result.append((q, bpm))
    return result


def _tempo_at(events: list[tuple[float, float]], q: float) -> float:
    current = events[0][1] if events else 100.0
    for eq, bpm in sorted(events, key=lambda e: e[0]):
        if eq <= q:
            current = bpm
        else:
            break
    return current


def _build_velocity_function(score: Score, perf: Performance, opt: PerformanceOptions, dirs_by_measure):
    if not opt.dynamics:
        return lambda part, q: DEFAULT_VELOCITY
    levels: dict[int, list[tuple[float, float]]] = defaultdict(list)
    accents: dict[int, list[tuple[float, float]]] = defaultdict(list)
    wedges: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
    open_wedge: dict[int, tuple[float, str]] = {}
    for pm in perf.play_measures:
        for d in sorted(dirs_by_measure.get(pm.measure, []), key=lambda x: x.onset):
            q = pm.start_q + float(d.onset)
            if d.kind == "dynamic":
                value = d.value.lower()
                if value in ACCENT_DYNAMICS:
                    accents[d.part].append((q, DYNAMIC_VELOCITY.get(value, 100)))
                    if value in ("fp", "sfp", "sfzp"):
                        levels[d.part].append((q + 0.25, DYNAMIC_VELOCITY["p"]))
                elif value in DYNAMIC_VELOCITY:
                    levels[d.part].append((q, DYNAMIC_VELOCITY[value]))
            elif d.kind == "wedge":
                if d.value in ("crescendo", "diminuendo"):
                    if d.part in open_wedge:
                        s, kind = open_wedge.pop(d.part)
                        wedges[d.part].append((s, q, kind))
                    open_wedge[d.part] = (q, d.value)
                    if d.text:  # «cresc.» escrito: dura hasta la siguiente dinámica
                        pass
                elif d.value == "stop" and d.part in open_wedge:
                    s, kind = open_wedge.pop(d.part)
                    wedges[d.part].append((s, q, kind))
    for part, (s, kind) in open_wedge.items():
        wedges[part].append((s, min(s + 8.0, perf.total_q), kind))
    for part in levels:
        levels[part].sort()

    def level_at(part: int, q: float) -> float:
        lv = levels.get(part)
        if not lv:
            # sin dinámicas en esta parte: usar las de la primera parte (piano a dos manos, etc.)
            lv = levels.get(0, [])
        current = DEFAULT_VELOCITY
        for lq, val in lv:
            if lq <= q + 1e-6:
                current = val
            else:
                break
        return current

    def velocity(part: int, q: float) -> float:
        base = level_at(part, q)
        for s, e, kind in wedges.get(part, wedges.get(0, [])):
            if s <= q <= e and e > s:
                target = level_at(part, e + 0.01)
                if target == base:
                    target = base + (16 if kind == "crescendo" else -16)
                base = base + (target - base) * ((q - s) / (e - s))
                break
        for aq, val in accents.get(part, accents.get(0, [])):
            if abs(aq - q) < 1e-3:
                base = max(base, val)
        return max(1.0, min(127.0, base))

    return velocity


def _build_pedal(score: Score, perf: Performance, dirs_by_measure) -> None:
    down: dict[int, bool] = defaultdict(bool)
    for pm in perf.play_measures:
        for d in sorted(dirs_by_measure.get(pm.measure, []), key=lambda x: x.onset):
            if d.kind != "pedal":
                continue
            q = pm.start_q + float(d.onset)
            if d.value in ("start", "resume"):
                if down[d.part]:
                    perf.pedal_events.append((max(0.0, q - 0.02), 0, d.part))
                perf.pedal_events.append((q, 127, d.part))
                down[d.part] = True
            elif d.value in ("stop", "discontinue"):
                perf.pedal_events.append((q, 0, d.part))
                down[d.part] = False
            elif d.value == "change":
                perf.pedal_events.append((max(0.0, q - 0.02), 0, d.part))
                perf.pedal_events.append((q + 0.02, 127, d.part))
                down[d.part] = True
    for part, is_down in down.items():
        if is_down:
            perf.pedal_events.append((perf.total_q, 0, part))
    perf.pedal_events.sort()


def _build_markers(score: Score, perf: Performance, dirs_by_measure) -> None:
    for pm in perf.play_measures:
        m = score.measures[pm.measure]
        for d in dirs_by_measure.get(pm.measure, []):
            q = pm.start_q + float(d.onset)
            if d.kind == "rehearsal":
                perf.markers.append((q, "marker", d.text or d.value))
            elif d.kind in ("words", "tempo_change") and d.text:
                perf.markers.append((q, "text", d.text))
        for t in score.tempos:
            if t.measure == pm.measure and t.text:
                perf.markers.append((pm.start_q + float(t.onset), "text", t.text))
        for nav in m.navigation:
            perf.markers.append((pm.start_q, "marker", nav))
    perf.markers.sort(key=lambda x: x[0])


def _chord_symbol_notes(score: Score, perf: Performance) -> list[PerfNote]:
    from note_cascade.music_model.pitch import STEP_PC

    result: list[PerfNote] = []
    symbols = sorted(score.chord_symbols, key=lambda c: (c.measure, c.onset))
    by_measure = defaultdict(list)
    for c in symbols:
        by_measure[c.measure].append(c)
    flat: list[tuple[float, object, int]] = []
    for idx, pm in enumerate(perf.play_measures):
        for c in by_measure.get(pm.measure, []):
            flat.append((pm.start_q + float(c.onset), c, idx))
    for i, (q, c, play_index) in enumerate(flat):
        end = flat[i + 1][0] if i + 1 < len(flat) else perf.total_q
        intervals = terms.CHORD_INTERVALS.get(c.kind, [0, 4, 7])
        root = 48 + (STEP_PC[c.root_step] + c.root_alter) % 12
        pitches = [root + iv for iv in intervals]
        if c.bass_step:
            pitches.insert(0, 36 + (STEP_PC[c.bass_step] + c.bass_alter) % 12)
        for midi in pitches:
            result.append(PerfNote(q, max(0.1, end - q), midi, 60, c.part, 99, None, c.measure, play_index,
                                   tuple(), track="acordes"))
    return result


def iter_performed_note_ids(perf: Performance) -> Iterable[str]:
    for n in perf.notes:
        yield from n.note_ids
