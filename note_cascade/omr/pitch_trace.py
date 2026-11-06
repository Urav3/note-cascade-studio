"""Diagnóstico de alturas: recorrido de cada nota desde la imagen hasta la tecla que se ilumina.

Para cada nota se calcula, con números MIDI (independientes de cómo se llame a cada octava):

* la altura **escrita** según la imagen: pentagrama, clave activa, posición de la cabeza respecto de la
  línea central y número de líneas adicionales;
* la alteración aplicable (armadura y alteraciones del compás);
* la indicación de octava activa y su desplazamiento;
* la altura que entregó el motor de reconocimiento, la del modelo (con el historial de cambios), la del
  MIDI exportado, la del reproductor y la tecla del teclado de la cascada.

Si la altura final no coincide con la esperada se indica la primera etapa en la que se separa.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field

from note_cascade.music_model import pitch as P
from note_cascade.music_model.model import Note, Score

STEP_NAMES = {"C": "Do", "D": "Re", "E": "Mi", "F": "Fa", "G": "Sol", "A": "La", "B": "Si"}


@dataclass
class PitchTrace:
    note_id: str
    page: int | None
    measure: str
    beat: float
    staff: int
    hand: str
    clef: str = ""
    staff_position: int | None = None      # medios espacios sobre (+) o bajo (−) la línea central
    ledger_lines: int | None = None        # + por encima del pentagrama, − por debajo
    written_image: str = ""                # nota escrita que corresponde a la posición en la imagen
    written_image_midi: int | None = None
    accidental: str = ""                   # alteración aplicable y su origen
    octave_shift: int = 0                  # semitonos de la indicación de octava activa (8va = +12…)
    expected_midi: int | None = None       # escrita (imagen) + octava
    engine_midi: int | None = None
    model_midi: int = 0
    pitch_log: list[str] = field(default_factory=list)
    export_midi: int | None = None         # altura en el MIDI exportado
    playback_midi: int | None = None       # altura que recibe el reproductor (con la separación de manos)
    keyboard_x: float | None = None        # centro de la tecla en el teclado de la cascada (0–1000)
    status: str = ""                       # ok | diferencia | sin_imagen
    origin: str = ""                       # dónde empieza la diferencia

    def as_row(self) -> dict:
        d = asdict(self)
        d["pitch_log"] = " | ".join(self.pitch_log)
        return d


def _midi_name(midi: int | None) -> str:
    return "—" if midi is None else f"{P.midi_name(midi)} (MIDI {midi})"


def trace_notes(score: Score, project=None, note_ids: list[str] | None = None, export_perf=None,
                playback_perf=None, keyboard: tuple[int, int] | None = None) -> list[PitchTrace]:
    """Rastrea las notas indicadas (o todas). ``project`` da acceso a las imágenes de las páginas."""
    from note_cascade.omr import image_check as IC
    from note_cascade.piano_roll.keyboard import auto_range, key_layout

    wanted = set(note_ids) if note_ids else None
    notes = [n for n in score.notes if not n.rest and (wanted is None or n.id in wanted)]
    pages = {}
    if project is not None and project.pages:
        needed = {n.position.page for n in notes if n.position is not None}
        for rec in project.pages:
            if rec.index in needed:
                try:
                    pg = IC._load_page(project, rec)
                except Exception:
                    pg = None
                if pg is not None:
                    pages[rec.index] = pg
    export = _perf_pitch(export_perf)
    playback = _perf_pitch(playback_perf)
    if keyboard is None and playback_perf is not None:
        keyboard = auto_range([p.midi for p in playback_perf.notes])
    layout = key_layout(keyboard[0], keyboard[1], 0.0, 1000.0) if keyboard else {}
    out = []
    for n in sorted(notes, key=lambda n: (n.measure, n.onset, n.staff, n.midi)):
        clef = score.clef_at(n.part, n.staff, n.measure, n.onset)
        t = PitchTrace(n.id, n.position.page + 1 if n.position else None, score.measures[n.measure].number,
                       float(n.onset) + 1, n.staff, n.hand or "",
                       clef=_clef_name(clef), engine_midi=n.engine_midi, model_midi=n.midi, pitch_log=list(n.pitch_log))
        t.octave_shift = 12 * IC._octave_shift_at(score, n)
        pg = pages.get(n.position.page) if n.position is not None and not n.position.approximate else None
        staff = IC._measure_staff(score, pg, n.measure, n.staff) if pg is not None else None
        if pg is not None and staff is not None and clef is not None:
            x0, y0, x1, y1 = pg.to_proc(n.position)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            pos = int(round((staff.y(2, cx) - cy) / (staff.interline / 2)))
            t.staff_position = pos
            t.ledger_lines = (pos - 4) // 2 if pos >= 6 else -((-pos - 4) // 2) if pos <= -6 else 0
            d = IC.written_diatonic(score, n.part, n.staff, n.measure, n.onset, staff, cx, cy)
            step, octave = P.from_diatonic_index(d)
            alter, why = _alter_for(score, n, step, octave)
            t.accidental = why
            t.written_image_midi = P.midi_from(step, alter, octave)
            t.written_image = f"{P.note_name(step, alter, octave)} (MIDI {t.written_image_midi})"
            t.expected_midi = t.written_image_midi + t.octave_shift
        t.export_midi = export.get(n.id)
        t.playback_midi = playback.get(n.id)
        if t.playback_midi is not None and t.playback_midi in layout:
            k = layout[t.playback_midi]
            t.keyboard_x = round(k.x + k.w / 2, 1)
        _verdict(t)
        out.append(t)
    return out


def _clef_name(clef) -> str:
    if clef is None:
        return "?"
    name = {"G": "Sol", "F": "Fa", "C": "Do"}.get(clef.sign, clef.sign)
    text = f"{name} ({clef.line}.ª línea)"
    if clef.octave_change:
        text += f" {'+' if clef.octave_change > 0 else '−'}{abs(clef.octave_change)} octava"
    return text


def _perf_pitch(perf) -> dict[str, int]:
    if perf is None:
        return {}
    res = {}
    for p in perf.notes:
        for nid in p.note_ids:
            res.setdefault(nid, p.midi)
    return res


def _alter_for(score: Score, n: Note, step: str, octave: int) -> tuple[int, str]:
    if step == n.step and n.accidental_shown:
        return n.alter, f"alteración escrita ({n.accidental_shown})"
    previous = None   # la última alteración escrita antes, en el mismo compás y pentagrama
    for m in sorted(score.notes_in_measure(n.measure, include_rests=False), key=lambda x: x.onset):
        if m.onset >= n.onset:
            break
        if m.staff == n.staff and m.step == step and m.octave == octave and m.accidental_shown:
            previous = m
    if previous is not None:
        return previous.alter, f"alteración anterior del compás ({previous.accidental_shown})"
    key = P.key_alterations(score.key_at(n.part, n.measure, n.staff))[step]
    if step == n.step and n.alter != key and n.tie_stop:
        return n.alter, "la de la nota ligada del compás anterior"
    return key, "armadura" if key else "natural (sin alteración)"


def _verdict(t: PitchTrace) -> None:
    if t.expected_midi is None:
        t.status = "sin_imagen"
        t.origin = "La nota no tiene posición exacta en la imagen: no se puede comparar con la partitura."
        return
    final = t.playback_midi if t.playback_midi is not None else t.model_midi
    if t.model_midi == t.expected_midi and (t.export_midi in (None, t.model_midi)):
        t.status = "ok"
        if final != t.model_midi:
            t.origin = (f"Correcta. Al escuchar se oye en {_midi_name(final)} porque está activada la separación "
                        "de manos (solo para escuchar y ver).")
        return
    t.status = "diferencia"
    written = t.written_image_midi
    if t.engine_midi is not None and t.engine_midi % 12 == written % 12 and t.engine_midi != written:
        t.origin = (f"El motor leyó {_midi_name(t.engine_midi)} y en la imagen la cabeza corresponde a "
                    f"{_midi_name(written)}: octava distinta (líneas adicionales o clave).")
    elif t.engine_midi is not None and t.engine_midi != written and t.model_midi == t.engine_midi + t.octave_shift:
        t.origin = (f"El motor leyó {_midi_name(t.engine_midi)} y la imagen indica {_midi_name(written)} "
                    "(posición en el pentagrama, clave o alteración).")
    elif t.model_midi - t.expected_midi in (12, -12, 24, -24) and t.model_midi - written in (0,):
        t.origin = "Falta aplicar la indicación de octava activa sobre este pentagrama."
    elif t.pitch_log:
        t.origin = "La altura cambió después del motor: " + " | ".join(t.pitch_log)
    elif t.export_midi is not None and t.export_midi != t.model_midi:
        t.origin = f"El modelo tiene {_midi_name(t.model_midi)} pero el MIDI exportado {_midi_name(t.export_midi)}."
    else:
        t.origin = (f"Esperada {_midi_name(t.expected_midi)} (escrita {_midi_name(written)} "
                    f"{t.octave_shift:+d} de octava); el modelo tiene {_midi_name(t.model_midi)}.")


def describe(t: PitchTrace) -> list[tuple[str, str]]:
    """Filas legibles (etapa, valor) para mostrar el recorrido de una nota."""
    pos = "—"
    if t.staff_position is not None:
        rel = "sobre" if t.staff_position > 0 else "bajo" if t.staff_position < 0 else "en"
        pos = f"{abs(t.staff_position)} medio(s) espacio(s) {rel} la línea central"
        if t.staff_position == 0:
            pos = "en la línea central"
        if t.ledger_lines:
            pos += f" · {abs(t.ledger_lines)} línea(s) adicional(es) {'arriba' if t.ledger_lines > 0 else 'abajo'}"
    shift = {0: "ninguna", 12: "8va (+12)", -12: "8vb (−12)", 24: "15ma (+24)", -24: "15mb (−24)"}.get(
        t.octave_shift, f"{t.octave_shift:+d}")
    return [
        ("Página y compás", f"página {t.page or '—'}, compás {t.measure}, tiempo {t.beat:g}"),
        ("Pentagrama y mano", f"pentagrama {t.staff} · mano {'derecha' if t.hand == 'R' else 'izquierda' if t.hand == 'L' else '—'}"),
        ("Clave activa", t.clef),
        ("Posición de la cabeza", pos),
        ("Altura escrita (imagen)", t.written_image or "—"),
        ("Alteración aplicada", t.accidental or "—"),
        ("Indicación de octava activa", shift),
        ("Altura esperada", _midi_name(t.expected_midi)),
        ("Altura del motor", _midi_name(t.engine_midi)),
        ("Cambios posteriores", " | ".join(t.pitch_log) or "ninguno"),
        ("Altura en el modelo", _midi_name(t.model_midi)),
        ("Altura en el MIDI exportado", _midi_name(t.export_midi)),
        ("Altura en el reproductor", _midi_name(t.playback_midi)),
        ("Tecla en la cascada (0–1000)", "—" if t.keyboard_x is None else f"{t.keyboard_x:g}"),
        ("Resultado", {"ok": "✔ coincide con la partitura", "diferencia": "✖ no coincide",
                       "sin_imagen": "sin posición en la imagen"}.get(t.status, t.status)),
        ("Dónde se origina", t.origin or "—"),
    ]


def to_csv(traces: list[PitchTrace]) -> str:
    buf = io.StringIO()
    if not traces:
        return ""
    w = csv.DictWriter(buf, fieldnames=list(traces[0].as_row().keys()), delimiter=";")
    w.writeheader()
    for t in traces:
        w.writerow(t.as_row())
    return buf.getvalue()
