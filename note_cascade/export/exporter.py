"""Exportación de resultados con verificación real de cada archivo."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from note_cascade.app.config import AppConfig
from note_cascade.app.errors import ExportError
from note_cascade.midi.verify import verify_midi
from note_cascade.midi.writer import write_midi
from note_cascade.music_model.timeline import PerformanceOptions, build_performance
from note_cascade.musicxml.reader import read_musicxml
from note_cascade.musicxml.writer import write_musicxml
from note_cascade.project.project import Project

log = logging.getLogger(__name__)


@dataclass
class ExportRequest:
    out_dir: Path
    base_name: str
    midi_type1: bool = True
    midi_type0: bool = False
    musicxml: bool = True
    report: bool = True
    project: bool = True
    video: bool = False
    expand_repeats: bool = True
    split_hands: bool = True
    overwrite: bool = True
    video_options: dict = field(default_factory=dict)


@dataclass
class ExportOutcome:
    kind: str
    path: Path | None
    ok: bool
    message: str


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    k = 2
    while True:
        cand = path.with_name(f"{stem} ({k}){suffix}")
        if not cand.exists():
            return cand
        k += 1


def performance_options(cfg: AppConfig, expand: bool, listening: bool = False) -> PerformanceOptions:
    """Opciones de interpretación. ``listening``: incluye la separación de manos elegida al escuchar
    (las exportaciones llevan siempre las notas escritas)."""
    m = cfg.music
    return PerformanceOptions(expand_repeats=expand, default_tempo=m.default_tempo_bpm,
                              dynamics=m.dynamics_to_velocity, articulations=m.articulation_effects,
                              fermata_stretch=m.fermata_stretch, pedal=m.sustain_pedal,
                              chord_symbols_to_notes=cfg.processing.chord_symbols_to_notes,
                              separate_hands=cfg.playback.separate_hands if listening else "no")


def export_project(project: Project, req: ExportRequest, cfg: AppConfig,
                   progress: Callable[[float, str], None] | None = None) -> list[ExportOutcome]:
    out: list[ExportOutcome] = []
    req.out_dir.mkdir(parents=True, exist_ok=True)
    base = "".join(c for c in req.base_name if c not in '<>:"/\\|?*').strip() or "partitura"

    def target(suffix: str) -> Path:
        p = req.out_dir / f"{base}{suffix}"
        return p if req.overwrite else unique_path(p)

    def step(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    if not project.score.pitched_notes():
        raise ExportError("La partitura no contiene notas: no hay nada que exportar.",
                          "Revisa el reconocimiento o vuelve a analizar con otras opciones.")
    perf = build_performance(project.score, performance_options(cfg, req.expand_repeats))
    jobs = []
    if req.midi_type1:
        jobs.append(("midi1", ".mid"))
    if req.midi_type0:
        jobs.append(("midi0", "_tipo0.mid" if req.midi_type1 else ".mid"))
    if req.musicxml:
        jobs.append(("musicxml", ".musicxml"))
    if req.report:
        jobs.append(("report", "_informe.html"))
    if req.project:
        jobs.append(("project", ".ncsproj"))
    if req.video:
        jobs.append(("video", ".mp4"))
    for k, (kind, suffix) in enumerate(jobs):
        step(k / max(1, len(jobs)), f"Exportando {kind}")
        path = target(suffix)
        try:
            if kind in ("midi1", "midi0"):
                mtype = 1 if kind == "midi1" else 0
                res = write_midi(project.score, perf, path, midi_type=mtype, split_hands=req.split_hands)
                v = verify_midi(path, res.expected_notes)
                msg = f"MIDI tipo {mtype}: " + v.message
                if v.ok:
                    msg += " Pistas: " + ", ".join(res.tracks) + "."
                out.append(ExportOutcome(kind, path, v.ok, msg))
            elif kind == "musicxml":
                write_musicxml(project.score, path)
                rr = read_musicxml(path, source="verificacion")
                expected = len([n for n in project.score.notes if not n.rest])
                got = len([n for n in rr.score.notes if not n.rest])
                ok = got >= expected and len(rr.score.measures) == len(project.score.measures)
                msg = (f"MusicXML verificado: {len(rr.score.measures)} compases y {got} notas al volver a leerlo."
                       if ok else f"MusicXML con diferencias al releerlo: {got} notas de {expected} esperadas.")
                out.append(ExportOutcome(kind, path, ok, msg))
            elif kind == "report":
                from note_cascade.export.report import write_report
                write_report(project, path, perf, cfg)
                ok = path.exists() and path.stat().st_size > 500
                out.append(ExportOutcome(kind, path, ok, "Informe de reconocimiento creado." if ok
                                         else "No se pudo crear el informe."))
            elif kind == "project":
                saved = project.save(path)
                ok = saved.exists() and saved.stat().st_size > 100
                out.append(ExportOutcome(kind, saved, ok, "Proyecto guardado (se puede seguir corrigiendo más tarde). Es un archivo comprimido que se abre "
                                         "con Note Cascade Studio (Importar → Abrir proyecto), no con el Bloc de notas."
                                         if ok else "No se pudo guardar el proyecto."))
            elif kind == "video":
                from note_cascade.export.video import export_video
                ok, msg = export_video(project, perf, path, cfg, req.video_options,
                                       lambda f, m: step((k + f) / max(1, len(jobs)), m))
                out.append(ExportOutcome(kind, path if ok else None, ok, msg))
        except Exception as exc:
            log.exception("Fallo exportando %s", kind)
            out.append(ExportOutcome(kind, path, False, f"Error al exportar: {exc}"))
    project.exports.append({"dir": str(req.out_dir), "files": [str(o.path) for o in out if o.ok],
                            "errors": [o.message for o in out if not o.ok]})
    step(1.0, "Exportación terminada")
    return out
