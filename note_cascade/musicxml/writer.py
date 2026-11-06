"""Exportación del modelo interno a MusicXML 4.0 (partwise).

Refleja las correcciones del usuario. Las alteraciones impresas se recalculan
según la armadura y el contexto del compás para que la partitura exportada
sea coherente aunque se hayan cambiado alturas en el editor.
"""

from __future__ import annotations

import datetime as dt
import math
import zipfile
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from note_cascade import APP_NAME, __version__
from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import (
    TYPE_QUARTERS,
    Direction,
    Note,
    Score,
    dotted,
    notation_for_duration,
    split_duration,
)
from note_cascade.music_model.timeline import measure_lengths

DYN_TAGS = {"pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "sf", "sfz", "sffz", "fz", "rfz", "rf",
            "sfp", "fp", "sfzp"}
ARTICULATION_TAGS = {"accent", "staccato", "tenuto", "staccatissimo", "marcato", "spiccato", "detached-legato",
                     "stress", "unstress"}
ORNAMENT_TAGS = {"trill-mark", "turn", "inverted-turn", "mordent", "inverted-mordent", "tremolo", "shake",
                 "delayed-turn", "wavy-line"}


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = str(text)
    return el


def _divisions(score: Score, lengths: list[Fraction]) -> int:
    dens = {1}
    for n in score.notes:
        dens.add(n.onset.denominator)
        dens.add(n.duration.denominator)
    for L in lengths:
        dens.add(L.denominator)
    for d in score.directions:
        dens.add(d.onset.denominator)
    div = 1
    for d in dens:
        div = math.lcm(div, d)
        if div > 30240:
            return 30240
    return max(div, 1)


def score_to_element(score: Score) -> ET.Element:
    lengths = measure_lengths(score)
    divisions = _divisions(score, lengths)
    root = ET.Element("score-partwise", {"version": "4.0"})
    if score.title:
        work = _sub(root, "work")
        _sub(work, "work-title", score.title)
    if score.subtitle:
        _sub(root, "movement-title", score.subtitle)
    ident = _sub(root, "identification")
    for kind, value in (("composer", score.composer), ("arranger", score.arranger), ("lyricist", score.lyricist)):
        if value:
            _sub(ident, "creator", value, type=kind)
    enc = _sub(ident, "encoding")
    _sub(enc, "software", f"{APP_NAME} {__version__}")
    _sub(enc, "encoding-date", dt.date.today().isoformat())
    part_list = _sub(root, "part-list")
    for pidx, part in enumerate(score.parts):
        pid = f"P{pidx + 1}"
        sp = _sub(part_list, "score-part", id=pid)
        _sub(sp, "part-name", part.name or ("Piano" if part.is_piano else f"Parte {pidx + 1}"))
        if part.abbreviation:
            _sub(sp, "part-abbreviation", part.abbreviation)
        si = _sub(sp, "score-instrument", id=f"{pid}-I1")
        _sub(si, "instrument-name", part.name or ("Piano" if part.is_piano else "Instrumento"))
        mi = _sub(sp, "midi-instrument", id=f"{pid}-I1")
        _sub(mi, "midi-channel", min(16, pidx + 1 if pidx < 9 else pidx + 2))
        _sub(mi, "midi-program", part.midi_program + 1)
    by_pm: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for n in score.notes:
        by_pm[(n.part, n.measure)].append(n)
    dirs: dict[tuple[int, int], list[Direction]] = defaultdict(list)
    for d in score.directions:
        dirs[(d.part, d.measure)].append(d)
    for pidx, part in enumerate(score.parts):
        part_el = _sub(root, "part", id=f"P{pidx + 1}")
        prev_ts = None
        for m in score.measures:
            mel = _sub(part_el, "measure", number=m.number or str(m.index + 1))
            if m.implicit:
                mel.set("implicit", "yes")
            if m.index > 0 and (m.new_page or m.new_system):
                _sub(mel, "print", **({"new_page": "yes"} if m.new_page else {"new_system": "yes"}))
            if m.repeat_start or ("start" in m.ending_type):
                bl = _sub(mel, "barline", location="left")
                if "start" in m.ending_type and m.ending_numbers:
                    _sub(bl, "ending", number=",".join(str(x) for x in m.ending_numbers), type="start")
                if m.repeat_start:
                    _sub(bl, "bar-style", "heavy-light")
                    _sub(bl, "repeat", direction="forward")
            _write_attributes(mel, score, pidx, part, m, divisions, prev_ts)
            if m.time_sig:
                prev_ts = m.time_sig
            _write_measure_body(mel, score, pidx, part, m, by_pm.get((pidx, m.index), []),
                                dirs.get((pidx, m.index), []), divisions, lengths[m.index])
            if m.repeat_end or "stop" in m.ending_type or "discontinue" in m.ending_type or m.barline_right:
                bl = _sub(mel, "barline", location="right")
                style = m.barline_right or ("light-heavy" if m.repeat_end else "")
                if style:
                    _sub(bl, "bar-style", style)
                if ("stop" in m.ending_type or "discontinue" in m.ending_type) and m.ending_numbers:
                    _sub(bl, "ending", number=",".join(str(x) for x in m.ending_numbers),
                         type="discontinue" if "discontinue" in m.ending_type else "stop")
                if m.repeat_end:
                    rep = _sub(bl, "repeat", direction="backward")
                    if m.repeat_times > 2:
                        rep.set("times", str(m.repeat_times))
    return root


def _write_attributes(mel, score: Score, pidx: int, part, m, divisions: int, prev_ts) -> None:
    first = m.index == 0
    keys = [k for k in score.keys if k.part == pidx and k.measure == m.index]
    clefs = [c for c in score.clefs if c.part == pidx and c.measure == m.index and c.onset == 0]
    ts_change = m.time_sig is not None and (first or m.time_sig != prev_ts)
    if not (first or keys or clefs or ts_change):
        return
    attr = _sub(mel, "attributes")
    if first:
        _sub(attr, "divisions", divisions)
    if first and not keys:
        key = _sub(attr, "key")
        _sub(key, "fifths", score.key_at(pidx, 0))
    for k in keys[:1] if not any(k.staff for k in keys) else keys:
        key = _sub(attr, "key", number=k.staff) if k.staff else _sub(attr, "key")
        _sub(key, "fifths", k.fifths)
        if k.mode and k.mode in ("major", "minor"):
            _sub(key, "mode", k.mode)
    if ts_change:
        t = _sub(attr, "time")
        _sub(t, "beats", m.time_sig[0])
        _sub(t, "beat-type", m.time_sig[1])
    if first and part.staves > 1:
        _sub(attr, "staves", part.staves)
    seen = set()
    for c in sorted(clefs, key=lambda c: c.staff):
        if c.staff in seen:
            continue
        seen.add(c.staff)
        clef = _sub(attr, "clef", number=c.staff if part.staves > 1 else None)
        _sub(clef, "sign", c.sign)
        if c.sign not in ("percussion", "PERCUSSION", "TAB", "NONE", "none"):
            _sub(clef, "line", c.line)
        if c.octave_change:
            _sub(clef, "clef-octave-change", c.octave_change)
    if first and (part.transpose_chromatic or part.transpose_octave):
        tr = _sub(attr, "transpose")
        _sub(tr, "chromatic", part.transpose_chromatic)
        if part.transpose_octave:
            _sub(tr, "octave-change", part.transpose_octave)


def _voice_streams(notes: list[Note]) -> dict[tuple[int, int], list[list[Note]]]:
    """Agrupa en (pentagrama, voz) -> lista de acordes sin solapamientos ni acordes de duración mixta."""
    streams: dict[tuple[int, int], list[list[Note]]] = defaultdict(list)
    used_voices: dict[int, set[int]] = defaultdict(set)
    overflow: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for n in notes:
        used_voices[n.staff].add(n.voice)

    def try_place(k: tuple[int, int], n: Note) -> bool:
        events = streams[k]
        if not events:
            events.append([n])
            return True
        head = events[-1][0]
        if (head.onset == n.onset and head.duration == n.duration and not head.rest and not n.rest
                and head.grace == n.grace):
            events[-1].append(n)
            return True
        last_end = head.onset if head.grace else head.end
        if last_end <= n.onset:
            events.append([n])
            return True
        return False

    for n in sorted(notes, key=lambda n: (n.staff, n.voice, n.onset, not n.grace, -n.midi)):
        if try_place((n.staff, n.voice), n):
            continue
        if any(try_place(k, n) for k in overflow[n.staff]):
            continue
        new_voice = max(used_voices[n.staff] | {0}) + 1
        used_voices[n.staff].add(new_voice)
        key = (n.staff, new_voice)
        overflow[n.staff].append(key)
        streams[key].append([n])
    return streams


def _write_measure_body(mel, score: Score, pidx: int, part, m, notes: list[Note], directions: list[Direction],
                        divisions: int, length: Fraction) -> None:
    def dur(q: Fraction) -> int:
        return int(round(q * divisions))

    cursor = Fraction(0)

    def move_to(target: Fraction) -> None:
        nonlocal cursor
        if target > cursor:
            fw = _sub(mel, "forward")
            _sub(fw, "duration", dur(target - cursor))
        elif target < cursor:
            bk = _sub(mel, "backup")
            _sub(bk, "duration", dur(cursor - target))
        cursor = target

    # Indicaciones (tempo, dinámicas, pedal, textos…) y cambios de clave a mitad de compás
    events = []
    for t in score.tempos:
        if t.measure == m.index and pidx == 0:
            events.append((t.onset, 0, ("tempo", t)))
    for d in directions:
        events.append((d.onset, 1, ("dir", d)))
    for c in score.clefs:
        if c.part == pidx and c.measure == m.index and c.onset > 0:
            events.append((c.onset, 2, ("clef", c)))
    for c in score.chord_symbols:
        if c.part == pidx and c.measure == m.index:
            events.append((c.onset, 3, ("harmony", c)))
    for nav in m.navigation:
        if pidx == 0 and nav in ("segno", "coda"):
            events.append((Fraction(0), 4, ("nav", nav)))
        elif pidx == 0:
            events.append((length, 4, ("nav", nav)))
    for onset, _prio, (kind, obj) in sorted(events, key=lambda e: (e[0], e[1])):
        move_to(min(onset, length))
        if kind == "tempo":
            _write_tempo(mel, obj)
        elif kind == "dir":
            _write_direction(mel, obj, part)
        elif kind == "clef":
            attr = _sub(mel, "attributes")
            clef = _sub(attr, "clef", number=obj.staff if part.staves > 1 else None)
            _sub(clef, "sign", obj.sign)
            _sub(clef, "line", obj.line)
        elif kind == "harmony":
            _write_harmony(mel, obj)
        elif kind == "nav":
            _write_navigation(mel, obj)

    streams = _voice_streams(notes)
    key_alt_cache: dict[int, dict[str, int]] = {}
    for (staff, voice), chords in sorted(streams.items()):
        move_to(Fraction(0))
        acc_state: dict[tuple[str, int], int] = {}
        if staff not in key_alt_cache:
            key_alt_cache[staff] = P.key_alterations(score.key_at(pidx, m.index, staff))
        key_alt = key_alt_cache[staff]
        for chord in chords:
            head = chord[0]
            if not head.grace and head.onset > cursor:
                gap = head.onset - cursor
                for piece in split_duration(gap):
                    _write_note(mel, None, piece, staff, voice, divisions, rest=True)
                cursor = head.onset
            if head.grace:
                for k, n in enumerate(chord):
                    _write_note(mel, n, Fraction(0), staff, voice, divisions, chord_member=k > 0,
                                accidental=_accidental(n, key_alt, acc_state))
                continue
            pieces = _pieces_for(head)
            for pi, (qd, type_name, dots, tup) in enumerate(pieces):
                for k, n in enumerate(chord):
                    tie_start = n.tie_start if pi == len(pieces) - 1 else True
                    tie_stop = n.tie_stop if pi == 0 else True
                    _write_note(mel, n, qd, staff, voice, divisions, chord_member=k > 0, type_name=type_name,
                                dots=dots, tuplet=tup, tie_start=tie_start, tie_stop=tie_stop,
                                accidental=_accidental(n, key_alt, acc_state) if pi == 0 else None,
                                first_piece=pi == 0)
                cursor += qd


def _pieces_for(n: Note) -> list[tuple[Fraction, str, int, tuple[int, int] | None]]:
    q = n.duration
    if n.type_name in TYPE_QUARTERS:
        expected = dotted(TYPE_QUARTERS[n.type_name], n.dots)
        if n.tuplet:
            expected = expected * Fraction(n.tuplet[1], n.tuplet[0])
        if expected == q:
            return [(q, n.type_name, n.dots, n.tuplet)]
    found = notation_for_duration(q)
    if found:
        t, d, tup = found
        return [(q, t, d, tup)]
    result = []
    for part in split_duration(q):
        f = notation_for_duration(part)
        if f:
            result.append((part, f[0], f[1], f[2]))
        else:
            result.append((part, "quarter", 0, None))
    return result


def _accidental(n: Note, key_alt: dict[str, int], state: dict[tuple[str, int], int]) -> str | None:
    if n.rest:
        return None
    k = (n.step, n.octave)
    expected = state.get(k, key_alt[n.step])
    state[k] = n.alter
    if n.alter != expected:
        return P.ALTER_TO_ACCIDENTAL.get(n.alter)
    if n.accidental_shown and not n.user_modified:
        return n.accidental_shown  # alteración de cortesía impresa en el original
    return None


def _write_note(mel, n: Note | None, q: Fraction, staff: int, voice: int, divisions: int, rest: bool = False,
                chord_member: bool = False, type_name: str | None = None, dots: int = 0,
                tuplet: tuple[int, int] | None = None, tie_start: bool = False, tie_stop: bool = False,
                accidental: str | None = None, first_piece: bool = True) -> None:
    el = _sub(mel, "note")
    is_rest = rest or (n is not None and n.rest)
    if n is not None and n.grace:
        _sub(el, "grace", slash="yes")
    if chord_member:
        _sub(el, "chord")
    if is_rest:
        r = _sub(el, "rest")
        if n is not None and n.measure_rest:
            r.set("measure", "yes")
    else:
        p = _sub(el, "pitch")
        _sub(p, "step", n.step)
        if n.alter:
            _sub(p, "alter", n.alter)
        _sub(p, "octave", n.octave)
    if not (n is not None and n.grace):
        _sub(el, "duration", max(1, int(round(q * divisions))))
    if tie_stop and not is_rest:
        _sub(el, "tie", type="stop")
    if tie_start and not is_rest:
        _sub(el, "tie", type="start")
    _sub(el, "voice", voice)
    if n is not None and n.grace:
        tn = n.type_name if n.type_name in TYPE_QUARTERS else "eighth"
        _sub(el, "type", tn)
    else:
        found = notation_for_duration(q) if type_name is None else (type_name, dots, tuplet)
        if found:
            _sub(el, "type", found[0])
            for _ in range(found[1]):
                _sub(el, "dot")
            tuplet = found[2]
    if accidental:
        _sub(el, "accidental", accidental)
    if tuplet:
        tm = _sub(el, "time-modification")
        _sub(tm, "actual-notes", tuplet[0])
        _sub(tm, "normal-notes", tuplet[1])
    _sub(el, "staff", staff)
    notations_needed = (tie_start or tie_stop) and not is_rest
    arts = [a for a in (n.articulations if n is not None and first_piece else [])]
    if notations_needed or arts:
        nt = _sub(el, "notations")
        if tie_stop and not is_rest:
            _sub(nt, "tied", type="stop")
        if tie_start and not is_rest:
            _sub(nt, "tied", type="start")
        art_list = [a for a in arts if a in ARTICULATION_TAGS]
        if art_list:
            ae = _sub(nt, "articulations")
            for a in art_list:
                _sub(ae, "strong-accent" if a == "marcato" else a)
        orn = [a for a in arts if a in ORNAMENT_TAGS]
        if orn:
            oe = _sub(nt, "ornaments")
            for a in orn:
                _sub(oe, a)
        if "fermata" in arts:
            _sub(nt, "fermata", type="upright")
        if "arpeggiate" in arts:
            _sub(nt, "arpeggiate")
    if n is not None and n.lyric and first_piece and not chord_member:
        ly = _sub(el, "lyric", number="1")
        _sub(ly, "syllabic", "single")
        _sub(ly, "text", n.lyric)


def _write_tempo(mel, t) -> None:
    d = _sub(mel, "direction", placement="above")
    if t.text and (t.bpm_quarter is None or t.source != "sonido"):
        dt_ = _sub(d, "direction-type")
        _sub(dt_, "words", t.text, font_weight="bold")
    if t.bpm_quarter:
        dt_ = _sub(d, "direction-type")
        met = _sub(dt_, "metronome")
        unit = t.beat_unit or "quarter"
        _sub(met, "beat-unit", unit)
        for _ in range(t.beat_dots):
            _sub(met, "beat-unit-dot")
        per_min = t.per_minute if t.per_minute else t.bpm_quarter
        _sub(met, "per-minute", f"{per_min:g}")
        _sub(d, "sound", tempo=f"{t.bpm_quarter:g}")
    if len(d) == 0:
        mel.remove(d)


def _write_direction(mel, d: Direction, part) -> None:
    el = ET.Element("direction", {"placement": d.placement} if d.placement in ("above", "below") else {})
    dt_ = ET.SubElement(el, "direction-type")
    if d.kind == "dynamic":
        dyn = ET.SubElement(dt_, "dynamics")
        if d.value in DYN_TAGS:
            ET.SubElement(dyn, d.value)
        else:
            od = ET.SubElement(dyn, "other-dynamics")
            od.text = d.value
    elif d.kind == "wedge":
        if d.text:
            w = ET.SubElement(dt_, "words")
            w.text = d.text
        else:
            ET.SubElement(dt_, "wedge", {"type": d.value if d.value in ("crescendo", "diminuendo", "stop") else "stop"})
    elif d.kind == "pedal":
        ET.SubElement(dt_, "pedal", {"type": d.value if d.value in ("start", "stop", "change", "continue") else "start",
                                     "line": "no"})
    elif d.kind == "octave_shift":
        ET.SubElement(dt_, "octave-shift", {"type": d.value or "stop", "size": str(d.size or 8)})
    elif d.kind == "rehearsal":
        r = ET.SubElement(dt_, "rehearsal")
        r.text = d.text or d.value
    elif d.kind in ("words", "tempo_change"):
        if not d.text:
            return
        w = ET.SubElement(dt_, "words")
        w.text = d.text
    else:
        return
    if d.staff and part.staves > 1:
        s = ET.SubElement(el, "staff")
        s.text = str(d.staff)
    mel.append(el)


def _write_harmony(mel, c) -> None:
    h = _sub(mel, "harmony")
    root = _sub(h, "root")
    _sub(root, "root-step", c.root_step)
    if c.root_alter:
        _sub(root, "root-alter", c.root_alter)
    _sub(h, "kind", c.kind, text=c.text[len(c.root_step):] if c.text else None)
    if c.bass_step:
        b = _sub(h, "bass")
        _sub(b, "bass-step", c.bass_step)
        if c.bass_alter:
            _sub(b, "bass-alter", c.bass_alter)


def _write_navigation(mel, nav: str) -> None:
    d = _sub(mel, "direction", placement="above")
    dt_ = _sub(d, "direction-type")
    if nav == "segno":
        _sub(dt_, "segno")
        _sub(d, "sound", segno="segno")
    elif nav == "coda":
        _sub(dt_, "coda")
        _sub(d, "sound", coda="coda")
    else:
        text = {"fine": "Fine", "dacapo": "D.C.", "dalsegno": "D.S.", "tocoda": "To Coda",
                "dacapo_al_fine": "D.C. al Fine", "dacapo_al_coda": "D.C. al Coda",
                "dalsegno_al_fine": "D.S. al Fine", "dalsegno_al_coda": "D.S. al Coda"}.get(nav, nav)
        _sub(dt_, "words", text)
        attrs = {}
        if nav.startswith("dacapo"):
            attrs["dacapo"] = "yes"
        elif nav.startswith("dalsegno"):
            attrs["dalsegno"] = "segno"
        elif nav == "fine":
            attrs["fine"] = "yes"
        elif nav == "tocoda":
            attrs["tocoda"] = "coda"
        if attrs:
            _sub(d, "sound", **attrs)


def score_to_bytes(score: Score) -> bytes:
    root = score_to_element(score)
    ET.indent(root, space="  ")
    header = (b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
              b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
              b'"http://www.musicxml.org/dtds/partwise.dtd">\n')
    return header + ET.tostring(root, encoding="utf-8")


def write_musicxml(score: Score, path: Path | str, compressed: bool | None = None) -> Path:
    path = Path(path)
    if compressed is None:
        compressed = path.suffix.lower() == ".mxl"
    data = score_to_bytes(score)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if compressed:
        inner = path.with_suffix(".musicxml").name
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/vnd.recordare.musicxml", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml",
                       '<?xml version="1.0" encoding="UTF-8"?>\n<container><rootfiles>'
                       f'<rootfile full-path="{inner}" media-type="application/vnd.recordare.musicxml+xml"/>'
                       '</rootfiles></container>')
            z.writestr(inner, data)
    else:
        tmp.write_bytes(data)
    tmp.replace(path)
    return path
