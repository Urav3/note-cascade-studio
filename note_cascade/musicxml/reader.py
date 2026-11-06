"""Lectura de MusicXML (.musicxml, .xml, .mxl) al modelo interno.

Se implementa un lector propio (en lugar de delegar en music21) para conservar
con precisión la información que necesita la revisión: voces, pentagrama de
cada nota, alteraciones impresas, posiciones ``default-x`` del motor,
comentarios de posición, saltos de sistema/página, repeticiones y
navegación.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from note_cascade.app.errors import CorruptFileError
from note_cascade.music_model import pitch as P
from note_cascade.music_model import terms
from note_cascade.music_model.model import (
    ChordSymbol,
    ClefChange,
    Direction,
    KeyChange,
    Measure,
    Note,
    PartInfo,
    Score,
    TempoMark,
    TYPE_QUARTERS,
    new_id,
)

log = logging.getLogger(__name__)

PIANO_NAME_RE = re.compile(r"(piano|pno|pf\b|klavier|keyboard|teclado|clavier|harpsichord|clave|cembalo|organ|órgano|celesta)", re.I)
IMGPOS_RE = re.compile(r"imgpos:\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")


@dataclass
class NoteLayout:
    default_x: float | None = None
    default_y: float | None = None
    imgpos: tuple[float, float] | None = None


@dataclass
class MeasureLayout:
    page: int = 0            # índice de página (0..) según <print new-page>
    system: int = 0          # índice de sistema global
    system_in_page: int = 0
    index_in_system: int = 0
    width: float | None = None


@dataclass
class ReadResult:
    score: Score
    note_layout: dict[str, NoteLayout] = field(default_factory=dict)
    measure_layout: list[MeasureLayout] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    software: str = ""
    credits: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Carga de bytes
# ---------------------------------------------------------------------------

def load_xml_bytes(path: Path | str) -> bytes:
    path = Path(path)
    data = path.read_bytes()
    return xml_bytes_from_data(data, path.name)


def xml_bytes_from_data(data: bytes, name: str = "") -> bytes:
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                rootfile = None
                if "META-INF/container.xml" in z.namelist():
                    container = ET.fromstring(z.read("META-INF/container.xml"))
                    for el in container.iter():
                        if el.tag.endswith("rootfile") and el.get("full-path"):
                            rootfile = el.get("full-path")
                            break
                if rootfile is None:
                    candidates = [n for n in z.namelist()
                                  if n.lower().endswith((".xml", ".musicxml")) and not n.startswith("META-INF")]
                    if not candidates:
                        raise CorruptFileError(f"El archivo MXL «{name}» no contiene ninguna partitura.",
                                               "Comprueba que el archivo no esté dañado.")
                    rootfile = candidates[0]
                return z.read(rootfile)
        except zipfile.BadZipFile as exc:
            raise CorruptFileError(f"El archivo «{name}» parece un MXL comprimido pero está dañado.",
                                   "Vuelve a exportarlo desde el programa de notación.", str(exc)) from exc
    return data


def read_musicxml(path: Path | str, source: str = "importado") -> ReadResult:
    data = load_xml_bytes(path)
    return read_musicxml_bytes(data, source=source, name=Path(path).name)


def read_musicxml_bytes(data: bytes, source: str = "importado", name: str = "") -> ReadResult:
    try:
        parser = ET.XMLParser(target=_CommentKeepingTreeBuilder())
        parser.feed(data)
        root = parser.close()
    except ET.ParseError as exc:
        raise CorruptFileError(f"El archivo MusicXML «{name}» no se puede leer (XML mal formado).",
                               "Si lo generó un motor de reconocimiento, prueba con otro motor.", str(exc)) from exc
    tag = _local(root.tag)
    if tag == "score-timewise":
        root = _timewise_to_partwise(root)
    elif tag != "score-partwise":
        raise CorruptFileError(f"«{name}» no es una partitura MusicXML (elemento raíz «{tag}»).")
    return _Reader(root, source).run()


class _CommentKeepingTreeBuilder(ET.TreeBuilder):
    """TreeBuilder que conserva los comentarios (posiciones ``imgpos`` de HOMR)."""

    def __init__(self) -> None:
        super().__init__(insert_comments=True)


def _local(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1]


def _timewise_to_partwise(root: ET.Element) -> ET.Element:
    new = ET.Element("score-partwise", root.attrib)
    for child in root:
        if _local(child.tag) != "measure":
            new.append(child)
    parts: dict[str, ET.Element] = {}
    for measure in root.findall("measure"):
        for part in measure.findall("part"):
            pid = part.get("id", "P1")
            if pid not in parts:
                parts[pid] = ET.SubElement(new, "part", {"id": pid})
            m = ET.SubElement(parts[pid], "measure", measure.attrib)
            for el in part:
                m.append(el)
    return new


def _text(el: ET.Element | None, path: str, default: str = "") -> str:
    if el is None:
        return default
    found = el.find(path)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value)) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


class _Reader:
    def __init__(self, root: ET.Element, source: str) -> None:
        self.root = root
        self.source = source
        self.score = Score()
        self.result = ReadResult(self.score)

    # ------------------------------------------------------------------
    def run(self) -> ReadResult:
        self._read_header()
        part_elements = self.root.findall("part")
        if not part_elements:
            raise CorruptFileError("La partitura MusicXML no contiene ninguna parte (pentagrama).",
                                   "El reconocimiento no encontró música en la imagen.")
        part_list = {sp.get("id"): sp for sp in self.root.iter("score-part")}
        for pidx, part_el in enumerate(part_elements):
            pid = part_el.get("id", f"P{pidx + 1}")
            info = self._part_info(pid, part_list.get(pid))
            self.score.parts.append(info)
            self._read_part(pidx, part_el, info)
        self._finalize()
        return self.result

    def _read_header(self) -> None:
        r = self.root
        self.score.title = _text(r, "work/work-title") or _text(r, "movement-title")
        if _text(r, "work/work-title") and _text(r, "movement-title"):
            self.score.subtitle = _text(r, "movement-title")
        for creator in r.findall("identification/creator"):
            kind = (creator.get("type") or "").lower()
            value = (creator.text or "").strip()
            if kind == "composer":
                self.score.composer = value
            elif kind == "arranger":
                self.score.arranger = value
            elif kind in ("lyricist", "poet"):
                self.score.lyricist = value
        self.result.software = " ".join(s.text.strip() for s in r.findall("identification/encoding/software") if s.text)
        for credit in r.findall("credit"):
            words = " ".join((w.text or "").strip() for w in credit.findall("credit-words"))
            ctype = _text(credit, "credit-type")
            if words:
                self.result.credits.append({"page": credit.get("page", "1"), "type": ctype, "text": words})
                if ctype == "title" and not self.score.title:
                    self.score.title = words
                elif ctype == "composer" and not self.score.composer:
                    self.score.composer = words
                elif ctype == "subtitle" and not self.score.subtitle:
                    self.score.subtitle = words

    def _part_info(self, pid: str, sp: ET.Element | None) -> PartInfo:
        info = PartInfo(id=pid)
        if sp is not None:
            info.name = _text(sp, "part-name")
            info.abbreviation = _text(sp, "part-abbreviation")
            prog = _text(sp, "midi-instrument/midi-program")
            if prog:
                info.midi_program = max(0, min(127, _int(prog, 1) - 1))
            sound = _text(sp, "score-instrument/instrument-sound")
            if sound.startswith("keyboard.piano") or PIANO_NAME_RE.search(info.name or ""):
                info.is_piano = True
            if PIANO_NAME_RE.search(_text(sp, "score-instrument/instrument-name")):
                info.is_piano = True
        return info

    # ------------------------------------------------------------------
    def _read_part(self, pidx: int, part_el: ET.Element, info: PartInfo) -> None:
        divisions = 1
        measures_el = part_el.findall("measure")
        page = 0
        system = 0
        system_in_page = 0
        index_in_system = 0
        current_time: tuple[int, int] | None = None
        octave_shift_active: dict[int, int] = {}
        first_part = pidx == 0
        clef_seen: dict[int, tuple[str, int]] = {}
        for midx, mel in enumerate(measures_el):
            if first_part:
                measure = Measure(index=midx, number=mel.get("number", str(midx + 1)))
                measure.implicit = mel.get("implicit", "no") == "yes"
                self.score.measures.append(measure)
                layout = MeasureLayout(width=_float(mel.get("width")))
            else:
                if midx >= len(self.score.measures):
                    self.result.warnings.append(
                        f"La parte {info.name or info.id} tiene más compases que la primera; se añaden vacíos.")
                    measure = Measure(index=midx, number=mel.get("number", str(midx + 1)))
                    self.score.measures.append(measure)
                    self.result.measure_layout.append(MeasureLayout())
                measure = self.score.measures[midx]
                layout = None

            cursor = 0  # en divisiones
            max_cursor = 0
            last_onset = 0
            pending_chord_onset = 0
            tie_state: dict[str, Any] = {}

            for el in list(mel):
                tag = _local(el.tag)
                if tag == "print":
                    if el.get("new-page") == "yes" and midx > 0:
                        page += 1
                        system += 1
                        system_in_page = 0
                        index_in_system = 0
                        if first_part:
                            measure.new_page = True
                            measure.new_system = True
                    elif el.get("new-system") == "yes" and midx > 0:
                        system += 1
                        system_in_page += 1
                        index_in_system = 0
                        if first_part:
                            measure.new_system = True
                elif tag == "attributes":
                    divisions, current_time = self._read_attributes(el, pidx, midx, cursor, divisions, info,
                                                                    current_time, measure, first_part, clef_seen)
                elif tag == "note":
                    onset_div, dur_div = self._read_note(el, pidx, midx, cursor, last_onset, divisions, info,
                                                         measure)
                    if el.find("chord") is not None:
                        pass
                    else:
                        last_onset = cursor
                        if el.find("grace") is None:
                            cursor += dur_div
                    max_cursor = max(max_cursor, cursor)
                elif tag == "backup":
                    cursor -= _int(_text(el, "duration"), 0)
                    cursor = max(cursor, 0)
                elif tag == "forward":
                    cursor += _int(_text(el, "duration"), 0)
                    max_cursor = max(max_cursor, cursor)
                elif tag == "direction":
                    self._read_direction(el, pidx, midx, cursor, divisions, measure, octave_shift_active)
                elif tag == "sound":
                    self._read_sound(el, pidx, midx, Fraction(cursor, divisions), measure)
                elif tag == "barline":
                    if first_part:
                        self._read_barline(el, measure)
                elif tag == "harmony":
                    self._read_harmony(el, pidx, midx, cursor, divisions)

            if first_part:
                measure.time_sig = current_time
                if layout is not None:
                    layout.page = page
                    layout.system = system
                    layout.system_in_page = system_in_page
                    layout.index_in_system = index_in_system
                    self.result.measure_layout.append(layout)
                    measure.page = page
                    measure.system = system
                index_in_system += 1

    # ------------------------------------------------------------------
    def _read_attributes(self, el: ET.Element, pidx: int, midx: int, cursor: int, divisions: int,
                         info: PartInfo, current_time: tuple[int, int] | None, measure: Measure,
                         first_part: bool, clef_seen: dict[int, tuple[str, int]]) -> tuple[int, tuple[int, int] | None]:
        div = _text(el, "divisions")
        if div:
            divisions = max(1, _int(div, divisions))
        staves = _text(el, "staves")
        if staves:
            info.staves = max(info.staves, _int(staves, 1))
        for key in el.findall("key"):
            fifths = _text(key, "fifths")
            if fifths == "":
                continue
            staff_num = key.get("number")
            self.score.keys.append(KeyChange(
                part=pidx, measure=midx, fifths=_int(fifths), mode=_text(key, "mode") or "major",
                staff=_int(staff_num) if staff_num else None))
        for time in el.findall("time"):
            if time.find("senza-misura") is not None:
                current_time = None
                continue
            beats_list = [b.text or "" for b in time.findall("beats")]
            types_list = [b.text or "" for b in time.findall("beat-type")]
            total = Fraction(0)
            denominator = 4
            ok = True
            for beats_txt, type_txt in zip(beats_list, types_list):
                try:
                    beats = sum(int(x) for x in beats_txt.replace(" ", "").split("+"))
                    btype = int(type_txt)
                except ValueError:
                    ok = False
                    break
                total += Fraction(beats, btype)
                denominator = max(denominator, btype)
            if ok and total > 0:
                num = total * denominator
                if num.denominator == 1:
                    new_time = (int(num), denominator)
                else:
                    new_time = (total.numerator, total.denominator)
                if first_part:
                    measure.time_sig_change = new_time != current_time or midx == 0
                current_time = new_time
        for clef in el.findall("clef"):
            staff = _int(clef.get("number"), 1)
            sign = _text(clef, "sign") or "G"
            line = _int(_text(clef, "line"), {"G": 2, "F": 4, "C": 3}.get(sign.upper(), 2))
            octave_change = _int(_text(clef, "clef-octave-change"), 0)
            onset = Fraction(cursor, divisions)
            self.score.clefs.append(ClefChange(pidx, staff, midx, onset, sign.upper(), line, octave_change))
            clef_seen[staff] = (sign.upper(), line)
            if sign.upper() == "F" and info.staves >= 2:
                info.is_piano = info.is_piano or True
        transpose = el.find("transpose")
        if transpose is not None and midx == 0:
            info.transpose_chromatic = _int(_text(transpose, "chromatic"), 0)
            info.transpose_octave = _int(_text(transpose, "octave-change"), 0)
        return divisions, current_time

    # ------------------------------------------------------------------
    def _read_note(self, el: ET.Element, pidx: int, midx: int, cursor: int, last_onset: int, divisions: int,
                   info: PartInfo, measure: Measure) -> tuple[int, int]:
        is_chord = el.find("chord") is not None
        onset_div = last_onset if is_chord else cursor
        grace = el.find("grace") is not None
        dur_div = _int(_text(el, "duration"), 0)
        if grace:
            dur_div = 0
        staff = _int(_text(el, "staff"), 1)
        voice = _int(_text(el, "voice"), 1)
        rest_el = el.find("rest")
        note = Note(
            id=new_id(), part=pidx, staff=staff, voice=voice, measure=midx,
            onset=Fraction(onset_div, divisions), duration=Fraction(dur_div, divisions),
            rest=rest_el is not None, grace=grace, source=self.source,
            cue=el.find("cue") is not None,
        )
        if note.rest:
            note.measure_rest = rest_el.get("measure") == "yes"
        else:
            pitch_el = el.find("pitch")
            if pitch_el is not None:
                note.step = (_text(pitch_el, "step") or "C").upper()
                note.alter = int(round(float(_text(pitch_el, "alter") or 0)))
                note.octave = _int(_text(pitch_el, "octave"), 4)
            else:
                unp = el.find("unpitched")
                if unp is not None:
                    note.step = (_text(unp, "display-step") or "C").upper()
                    note.octave = _int(_text(unp, "display-octave"), 4)
            written = P.midi_from(note.step, note.alter, note.octave)
            note.midi = written + info.transpose_chromatic + 12 * info.transpose_octave
            note.engine_midi = note.midi
            acc = _text(el, "accidental")
            note.accidental_shown = acc or None
        note.type_name = _text(el, "type") or None
        note.dots = len(el.findall("dot"))
        tm = el.find("time-modification")
        if tm is not None:
            actual = _int(_text(tm, "actual-notes"), 0)
            normal = _int(_text(tm, "normal-notes"), 0)
            if actual > 0 and normal > 0:
                note.tuplet = (actual, normal)
        if note.duration == 0 and not grace and note.type_name in TYPE_QUARTERS and not note.rest:
            # Algunos motores omiten <duration>; se reconstruye a partir de la figura.
            from note_cascade.music_model.model import dotted
            q = dotted(TYPE_QUARTERS[note.type_name], note.dots)
            if note.tuplet:
                q = q * Fraction(note.tuplet[1], note.tuplet[0])
            note.duration = q
            dur_div = int(q * divisions)
        for tie in el.findall("tie"):
            if tie.get("type") == "start":
                note.tie_start = True
            elif tie.get("type") == "stop":
                note.tie_stop = True
        notations = el.find("notations")
        if notations is not None:
            for tied in notations.findall("tied"):
                if tied.get("type") == "start":
                    note.tie_start = True
                elif tied.get("type") == "stop":
                    note.tie_stop = True
            arts = notations.find("articulations")
            if arts is not None:
                for a in arts:
                    name = _local(a.tag)
                    if name:
                        note.articulations.append({"strong-accent": "marcato"}.get(name, name))
            if notations.find("fermata") is not None:
                note.articulations.append("fermata")
            orn = notations.find("ornaments")
            if orn is not None:
                for o in orn:
                    name = _local(o.tag)
                    if name and name not in ("accidental-mark",):
                        note.articulations.append(name)
            if notations.find("arpeggiate") is not None:
                note.articulations.append("arpeggiate")
            tech = notations.find("technical")
            if tech is not None:
                for t in tech:
                    name = _local(t.tag)
                    if name in ("up-bow", "down-bow", "harmonic", "snap-pizzicato"):
                        note.articulations.append(name)
        lyric = el.find("lyric")
        if lyric is not None:
            note.lyric = _text(lyric, "text") or None
        layout = NoteLayout(default_x=_float(el.get("default-x")), default_y=_float(el.get("default-y")))
        for child in el:
            if child.tag is ET.Comment and child.text:
                m = IMGPOS_RE.search(child.text)
                if m:
                    layout.imgpos = (float(m.group(1)), float(m.group(2)))
        self.result.note_layout[note.id] = layout
        self.score.notes.append(note)
        return onset_div, dur_div

    # ------------------------------------------------------------------
    def _read_direction(self, el: ET.Element, pidx: int, midx: int, cursor: int, divisions: int,
                        measure: Measure, octave_shift_active: dict[int, int]) -> None:
        offset = _int(_text(el, "offset"), 0)
        onset = Fraction(max(0, cursor + offset), divisions)
        staff_txt = _text(el, "staff")
        staff = _int(staff_txt) if staff_txt else None
        placement = el.get("placement", "")
        words_text: list[str] = []
        metronome_found = False
        for dt in el.findall("direction-type"):
            for item in dt:
                kind = _local(item.tag)
                if kind == "dynamics":
                    for d in item:
                        dname = _local(d.tag)
                        if dname == "other-dynamics":
                            dname = (d.text or "").strip()
                        if dname:
                            self.score.directions.append(Direction("dynamic", midx, onset, pidx, staff, dname,
                                                                   placement=placement, source=self.source))
                elif kind == "wedge":
                    wtype = item.get("type", "")
                    value = {"crescendo": "crescendo", "diminuendo": "diminuendo"}.get(wtype, wtype)
                    self.score.directions.append(Direction("wedge", midx, onset, pidx, staff, value,
                                                           placement=placement, source=self.source))
                elif kind == "words":
                    if item.text and item.text.strip():
                        words_text.append(item.text.strip())
                elif kind == "metronome":
                    metronome_found = True
                    unit = _text(item, "beat-unit") or "quarter"
                    dots = len(item.findall("beat-unit-dot"))
                    per_minute = _float(_text(item, "per-minute"))
                    bpm = None
                    if per_minute:
                        bpm = per_minute * terms.beat_unit_quarters(unit, dots)
                    self.score.tempos.append(TempoMark(midx, onset, bpm, text="", beat_unit=unit, beat_dots=dots,
                                                       per_minute=per_minute, source=self.source))
                elif kind == "pedal":
                    ptype = item.get("type", "")
                    if ptype in ("start", "stop", "change", "continue", "resume", "discontinue"):
                        self.score.directions.append(Direction("pedal", midx, onset, pidx, staff, ptype,
                                                               source=self.source))
                elif kind == "octave-shift":
                    stype = item.get("type", "")
                    size = _int(item.get("size"), 8)
                    self.score.directions.append(Direction("octave_shift", midx, onset, pidx, staff, stype,
                                                           size=size, source=self.source))
                elif kind == "segno":
                    measure.navigation.append("segno")
                elif kind == "coda":
                    measure.navigation.append("coda")
                elif kind == "rehearsal":
                    if item.text:
                        self.score.directions.append(Direction("rehearsal", midx, onset, pidx, staff,
                                                               item.text.strip(), text=item.text.strip(),
                                                               source=self.source))
        if words_text:
            text = " ".join(words_text)
            self._classify_words(text, pidx, midx, onset, staff, placement, measure, metronome_found)
        sound = el.find("sound")
        if sound is not None:
            self._read_sound(sound, pidx, midx, onset, measure, from_direction=True,
                             has_metronome=metronome_found)

    def _classify_words(self, text: str, pidx: int, midx: int, onset: Fraction, staff: int | None,
                        placement: str, measure: Measure, metronome_found: bool) -> None:
        nav = terms.navigation_kind(text)
        if nav:
            measure.navigation.append(nav)
            self.score.directions.append(Direction("words", midx, onset, pidx, staff, nav, text=text,
                                                   placement=placement, source=self.source))
            return
        change = terms.tempo_change_kind(text)
        if change:
            self.score.directions.append(Direction("tempo_change", midx, onset, pidx, staff, change, text=text,
                                                   placement=placement, source=self.source))
            return
        metro = terms.parse_metronome(text)
        if metro and not metronome_found:
            unit, dots, per_minute = metro
            bpm = per_minute * terms.beat_unit_quarters(unit, dots)
            self.score.tempos.append(TempoMark(midx, onset, bpm, text=text, beat_unit=unit, beat_dots=dots,
                                               per_minute=per_minute, source=self.source))
            return
        tw = terms.tempo_word_bpm(text)
        if tw and (placement == "above" or placement == "") and not terms.is_expression(text):
            # Palabra de tempo sin número: se guarda como texto, sin inventar BPM.
            if metronome_found and self.score.tempos and self.score.tempos[-1].measure == midx:
                self.score.tempos[-1].text = text
            else:
                self.score.tempos.append(TempoMark(midx, onset, None, text=text, source=self.source))
            return
        dyn_kind = terms.dynamic_text_kind(text)
        if dyn_kind:
            self.score.directions.append(Direction("wedge", midx, onset, pidx, staff, dyn_kind, text=text,
                                                   placement=placement, source=self.source))
            return
        if terms.is_dynamic(text):
            self.score.directions.append(Direction("dynamic", midx, onset, pidx, staff, text.strip(". ").lower(),
                                                   placement=placement, source=self.source))
            return
        if terms.OCTAVE_LINE_RE.match(text.strip()):
            self.score.directions.append(Direction("words", midx, onset, pidx, staff, "octave_text", text=text,
                                                   placement=placement, source=self.source))
            return
        self.score.directions.append(Direction("words", midx, onset, pidx, staff, "", text=text,
                                               placement=placement, source=self.source))

    def _read_sound(self, el: ET.Element, pidx: int, midx: int, onset: Fraction, measure: Measure,
                    from_direction: bool = False, has_metronome: bool = False) -> None:
        tempo = _float(el.get("tempo"))
        if tempo and tempo > 0:
            # <sound tempo> siempre está expresado en negras por minuto.
            existing = [t for t in self.score.tempos if t.measure == midx and t.onset == onset]
            if existing:
                for t in existing:
                    if t.bpm_quarter is None or has_metronome:
                        t.bpm_quarter = tempo
            else:
                self.score.tempos.append(TempoMark(midx, onset, tempo, source="sonido"))
        for attr, nav in (("dacapo", "dacapo"), ("dalsegno", "dalsegno"), ("fine", "fine"),
                          ("tocoda", "tocoda"), ("coda", "coda"), ("segno", "segno")):
            if el.get(attr) and nav not in measure.navigation:
                measure.navigation.append(nav)
        damper = el.get("damper-pedal")
        if damper in ("yes", "no") and not from_direction:
            self.score.directions.append(Direction("pedal", midx, onset, pidx, None,
                                                   "start" if damper == "yes" else "stop", source=self.source))

    def _read_barline(self, el: ET.Element, measure: Measure) -> None:
        location = el.get("location", "right")
        style = _text(el, "bar-style")
        if location == "right" and style:
            measure.barline_right = style
        repeat = el.find("repeat")
        if repeat is not None:
            direction = repeat.get("direction")
            if direction == "forward":
                measure.repeat_start = True
            elif direction == "backward":
                measure.repeat_end = True
                measure.repeat_times = max(2, _int(repeat.get("times"), 2))
        ending = el.find("ending")
        if ending is not None:
            numbers = [int(x) for x in re.findall(r"\d+", ending.get("number", ""))]
            if numbers:
                measure.ending_numbers = numbers
            etype = ending.get("type", "")
            if measure.ending_type and etype and measure.ending_type != etype:
                measure.ending_type = measure.ending_type + "-" + etype
            elif etype:
                measure.ending_type = etype
        for sym in ("segno", "coda"):
            if el.find(sym) is not None and sym not in measure.navigation:
                measure.navigation.append(sym)

    def _read_harmony(self, el: ET.Element, pidx: int, midx: int, cursor: int, divisions: int) -> None:
        root = el.find("root")
        if root is None:
            return
        step = _text(root, "root-step")
        if not step:
            return
        alter = int(round(float(_text(root, "root-alter") or 0)))
        kind_el = el.find("kind")
        kind = (kind_el.text or "major").strip() if kind_el is not None else "major"
        text = kind_el.get("text", "") if kind_el is not None else ""
        bass = el.find("bass")
        offset = _int(_text(el, "offset"), 0)
        self.score.chord_symbols.append(ChordSymbol(
            measure=midx, onset=Fraction(max(0, cursor + offset), divisions), part=pidx,
            root_step=step.upper(), root_alter=alter, kind=kind, text=f"{step}{P.ALTER_ASCII.get(alter, '')}{text}",
            bass_step=_text(bass, "bass-step") or None if bass is not None else None,
            bass_alter=int(round(float(_text(bass, "bass-alter") or 0))) if bass is not None else 0,
        ))

    # ------------------------------------------------------------------
    def _finalize(self) -> None:
        s = self.score
        # Compás vigente en compases sin cambio explícito.
        current = None
        for m in s.measures:
            if m.time_sig is not None:
                current = m.time_sig
            else:
                m.time_sig = current
        # Asegura clave inicial por pentagrama.
        for pidx, part in enumerate(s.parts):
            for staff in range(1, part.staves + 1):
                if not any(c.part == pidx and c.staff == staff for c in s.clefs):
                    default = ("F", 4) if (staff == 2 and part.staves == 2) else ("G", 2)
                    s.clefs.append(ClefChange(pidx, staff, 0, Fraction(0), default[0], default[1]))
                    self.result.warnings.append(
                        f"No se indicó clave para el pentagrama {staff} de «{part.name or part.id}»; se supone "
                        f"{P.clef_description(*default)}.")
            # Piano: dos pentagramas con claves de sol y fa.
            if part.staves >= 2:
                signs = {c.sign for c in s.clefs if c.part == pidx}
                if "F" in signs and "G" in signs:
                    part.is_piano = part.is_piano or not part.name or bool(PIANO_NAME_RE.search(part.name)) \
                        or len(part.name.strip(" .")) <= 3
            # Nombres ilegibles del OCR del motor (p. ej. «no.» por «Pno.»)
            if part.is_piano and not PIANO_NAME_RE.search(part.name or ""):
                if part.name:
                    self.result.warnings.append(f"Nombre de parte «{part.name}» poco fiable; se usa «Piano».")
                part.name = "Piano"
        s.metadata.setdefault("software", self.result.software)
        s.sort_notes()
