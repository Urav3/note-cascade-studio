"""Generación de archivos MIDI estándar (tipo 0 y tipo 1) desde una Performance."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import mido

from note_cascade.music_model.hands import LEFT, RIGHT
from note_cascade.music_model.model import Score
from note_cascade.music_model.timeline import Performance, PerfNote

log = logging.getLogger(__name__)

PPQ = 480


@dataclass
class TrackPlan:
    key: tuple
    name: str
    channel: int
    program: int
    notes: list[PerfNote] = field(default_factory=list)


@dataclass
class MidiWriteResult:
    path: Path
    midi_type: int
    tracks: list[str]
    expected_notes: list[tuple[int, int, int, int]]  # (canal, nota, tick inicio, tick fin)


def plan_tracks(score: Score, perf: Performance, split_hands: bool = True) -> list[TrackPlan]:
    plans: dict[tuple, TrackPlan] = {}
    order: list[tuple] = []
    for n in perf.notes:
        part = score.parts[n.part] if n.part < len(score.parts) else None
        if n.track == "acordes":
            key = ("acordes",)
            name = "Acordes (cifrado)"
            program = 0
        elif part is not None and part.is_piano and split_hands:
            hand = n.hand or (RIGHT if n.staff <= 1 else LEFT)
            key = (n.part, hand)
            base = part.name or "Piano"
            name = f"{base} - {'Mano derecha' if hand == RIGHT else 'Mano izquierda'}"
            program = part.midi_program
        else:
            key = (n.part, None)
            name = (part.name if part and part.name else f"Parte {n.part + 1}")
            program = part.midi_program if part else 0
        if key not in plans:
            plans[key] = TrackPlan(key, name, 0, program)
            order.append(key)
        plans[key].notes.append(n)
    # orden estable: por parte y mano (derecha primero)
    order.sort(key=lambda k: (999 if k[0] == "acordes" else k[0], 0 if (len(k) > 1 and k[1] == RIGHT) else 1))
    channel = 0
    result = []
    for key in order:
        if channel == 9:
            channel += 1
        plans[key].channel = min(channel, 15)
        channel += 1
        result.append(plans[key])
    return result


def _ticks(q: float) -> int:
    return int(round(q * PPQ))


def _note_events(plan: TrackPlan) -> tuple[list[tuple[int, int, mido.Message]], list[tuple[int, int, int, int]]]:
    """Eventos de nota de una pista evitando que dos notas iguales se pisen."""
    events: list[tuple[int, int, mido.Message]] = []
    expected: list[tuple[int, int, int, int]] = []
    by_pitch: dict[int, list[list[int]]] = defaultdict(list)
    for n in sorted(plan.notes, key=lambda x: (x.start_q, x.midi)):
        start = _ticks(n.start_q)
        end = max(start + 1, _ticks(n.start_q + n.dur_q))
        lst = by_pitch[n.midi]
        if lst and lst[-1][0] == start:
            # Misma altura y mismo instante (unísono entre voces): un solo ataque.
            lst[-1][1] = max(lst[-1][1], end)
            lst[-1][2] = max(lst[-1][2], n.velocity)
            continue
        if lst and lst[-1][1] > start:
            # La nota anterior de la misma altura sigue sonando: se corta justo antes.
            lst[-1][1] = start
        lst.append([start, end, n.velocity])
    for pitch, items in by_pitch.items():
        for start, end, vel in items:
            if end <= start:
                continue
            events.append((start, 1, mido.Message("note_on", note=pitch, velocity=int(vel), channel=plan.channel)))
            events.append((end, 0, mido.Message("note_off", note=pitch, velocity=0, channel=plan.channel)))
            expected.append((plan.channel, pitch, start, end))
    return events, expected


def _to_track(events: list[tuple[int, int, mido.Message | mido.MetaMessage]], name: str | None = None) -> mido.MidiTrack:
    track = mido.MidiTrack()
    if name is not None:
        track.append(mido.MetaMessage("track_name", name=name[:120], time=0))
    events.sort(key=lambda e: (e[0], e[1]))
    last = 0
    for tick, _prio, msg in events:
        msg = msg.copy(time=max(0, tick - last))
        last = tick
        track.append(msg)
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def _conductor_events(score: Score, perf: Performance, markers: bool = True) -> list:
    events: list = []
    for q, num, den in perf.time_sigs:
        if den & (den - 1) != 0 or den == 0:
            continue  # MIDI solo admite denominadores potencia de 2
        events.append((_ticks(q), 0, mido.MetaMessage("time_signature", numerator=num, denominator=den,
                                                      clocks_per_click=24, notated_32nd_notes_per_beat=8)))
    for q, fifths in perf.key_sigs:
        key_name = _key_name(fifths)
        if key_name:
            events.append((_ticks(q), 0, mido.MetaMessage("key_signature", key=key_name)))
    last_tempo = None
    for q, bpm in sorted(perf.tempo_events, key=lambda e: e[0]):
        tempo = mido.bpm2tempo(max(1.0, bpm))
        if tempo == last_tempo:
            continue
        last_tempo = tempo
        events.append((_ticks(q), 0, mido.MetaMessage("set_tempo", tempo=tempo)))
    if markers:
        for q, kind, text in perf.markers:
            safe = _latin1(text)
            if not safe:
                continue
            if kind == "marker":
                events.append((_ticks(q), 2, mido.MetaMessage("marker", text=safe)))
            else:
                events.append((_ticks(q), 2, mido.MetaMessage("text", text=safe)))
    return events


def _latin1(text: str) -> str:
    return (text or "").encode("latin-1", "replace").decode("latin-1")[:120]


def _key_name(fifths: int) -> str | None:
    names = {-7: "Cb", -6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F", 0: "C", 1: "G", 2: "D",
             3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#"}
    return names.get(fifths)


def build_midi(score: Score, perf: Performance, midi_type: int = 1, split_hands: bool = True,
               markers: bool = True, lyrics: bool = True) -> tuple[mido.MidiFile, list[str], list[tuple]]:
    plans = plan_tracks(score, perf, split_hands)
    conductor = _conductor_events(score, perf, markers)
    track_events: list[tuple[str, list]] = []
    expected: list[tuple[int, int, int, int]] = []
    pedal_by_part: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for q, value, part in perf.pedal_events:
        pedal_by_part[part].append((q, value))
    for plan in plans:
        evs, exp = _note_events(plan)
        expected.extend(exp)
        evs.insert(0, (0, -1, mido.Message("program_change", program=plan.program, channel=plan.channel)))
        part = plan.key[0] if plan.key[0] != "acordes" else None
        if part is not None:
            for q, value in pedal_by_part.get(part, []):
                evs.append((_ticks(q), 0, mido.Message("control_change", control=64, value=value,
                                                         channel=plan.channel)))
        if lyrics and len(plan.key) > 1 and plan.key[1] in (RIGHT, None):
            seen = set()
            for n in plan.notes:
                if n.lyric and n.start_q not in seen:
                    seen.add(n.start_q)
                    evs.append((_ticks(n.start_q), 2, mido.MetaMessage("lyrics", text=_latin1(n.lyric))))
        track_events.append((plan.name, evs))

    mid = mido.MidiFile(type=midi_type, ticks_per_beat=PPQ)
    names = []
    if midi_type == 0:
        merged = list(conductor)
        for _name, evs in track_events:
            merged.extend(evs)
        mid.tracks.append(_to_track(merged, _latin1(score.title or "Note Cascade Studio")))
        names.append(score.title or "Pista única")
    else:
        mid.tracks.append(_to_track(conductor, _latin1(score.title or "Note Cascade Studio")))
        names.append("Director (tempo y compás)")
        for name, evs in track_events:
            mid.tracks.append(_to_track(evs, _latin1(name)))
            names.append(name)
    return mid, names, expected


def write_midi(score: Score, perf: Performance, path: Path | str, midi_type: int = 1,
               split_hands: bool = True, markers: bool = True) -> MidiWriteResult:
    path = Path(path)
    mid, names, expected = build_midi(score, perf, midi_type, split_hands, markers)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    mid.save(str(tmp))
    tmp.replace(path)
    return MidiWriteResult(path, midi_type, names, expected)
