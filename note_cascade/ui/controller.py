"""Estado central de la aplicación y operaciones sobre el proyecto abierto."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from note_cascade.app.config import AppConfig, save_config
from note_cascade.importers.document import SourceDocument
from note_cascade.music_model.edits import EditHistory
from note_cascade.music_model.model import Score
from note_cascade.music_model.timeline import Performance, build_performance
from note_cascade.music_validation import learning
from note_cascade.music_validation.fixes import apply_fix
from note_cascade.music_validation.issues import Issue
from note_cascade.omr.registry import create_engines, engine_statuses
from note_cascade.pipeline.conversion import revalidate_project
from note_cascade.player.audio_engine import PlaybackEngine
from note_cascade.project.project import Project

log = logging.getLogger(__name__)


class AppController(QObject):
    project_changed = Signal()
    score_changed = Signal(str)
    issues_changed = Signal()
    selection_changed = Signal(list)
    measure_focus = Signal(int)
    performance_changed = Signal()
    history_changed = Signal()
    engines_changed = Signal()
    status = Signal(str)

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.engines = create_engines(cfg)
        self.engine_status = {}
        self.project: Project | None = None
        self.document: SourceDocument | None = None
        self.history = EditHistory()
        self.performance: Performance | None = None
        self.selection: list[str] = []
        self.player = PlaybackEngine(cfg.playback.backend, cfg.playback.timbre, cfg.playback.volume)
        self.expand_repeats = cfg.music.repeats != "no_expandir"
        self._autosave = QTimer(self)
        self._autosave.setInterval(45_000)
        self._autosave.timeout.connect(self.autosave)
        self._autosave.start()

    # ------------------------------------------------------------------ motores
    def refresh_engines(self, refresh: bool = False) -> None:
        self.engines = create_engines(self.cfg)
        self.engine_status = engine_statuses(self.engines, refresh)
        self.engines_changed.emit()

    # ------------------------------------------------------------------ proyecto
    def set_project(self, project: Project | None, document: SourceDocument | None = None) -> None:
        self.project = project
        if document is not None:
            self.document = document
        self.history.clear()
        self.selection = []
        self.rebuild_performance()
        self.project_changed.emit()
        self.history_changed.emit()
        self.issues_changed.emit()

    @property
    def score(self) -> Score | None:
        return self.project.score if self.project else None

    def note_status(self, note_id: str) -> str:
        if not self.project:
            return "sin_datos"
        return self.project.options.get("status", {}).get("notes", {}).get(note_id, "sin_datos")

    def measure_status(self, index: int) -> str:
        if not self.project:
            return "sin_datos"
        return self.project.options.get("status", {}).get("measures", {}).get(str(index), "sin_datos")

    def rebuild_performance(self) -> None:
        if not self.project or not self.project.score.measures:
            self.performance = None
            self.performance_changed.emit()
            return
        from note_cascade.export.exporter import performance_options
        self.performance = build_performance(self.project.score,
                                             performance_options(self.cfg, self.expand_repeats, listening=True))
        self.player.set_performance(self.performance, keep_position=True)
        self.performance_changed.emit()

    # ------------------------------------------------------------------ ediciones
    def apply_edit(self, label: str, fn: Callable[[Score], object], revalidate: bool = True) -> object:
        if not self.project:
            return None
        self.history.record(self.project.score, label)
        result = fn(self.project.score)
        self.project.dirty = True
        self.project.add_log(f"Edición: {label}")
        self._after_change(label, revalidate)
        return result

    def _after_change(self, label: str, revalidate: bool = True) -> None:
        if revalidate:
            self.revalidate()
        self.rebuild_performance()
        self.score_changed.emit(label)
        self.history_changed.emit()

    def undo(self) -> None:
        if not self.project:
            return
        label = self.history.undo_label()
        restored = self.history.undo(self.project.score)
        if restored is not None:
            self.project.score = restored
            self.project.dirty = True
            self.selection = [i for i in self.selection if restored.note_by_id(i)]
            self._after_change(f"Deshacer: {label}")
            self.status.emit(f"Deshecho: {label}")

    def redo(self) -> None:
        if not self.project:
            return
        label = self.history.redo_label()
        restored = self.history.redo(self.project.score)
        if restored is not None:
            self.project.score = restored
            self.project.dirty = True
            self._after_change(f"Rehacer: {label}")
            self.status.emit(f"Rehecho: {label}")

    def revalidate(self) -> None:
        if not self.project:
            return
        revalidate_project(self.project, self.cfg)
        self.issues_changed.emit()

    def accept_issue(self, issue: Issue) -> str:
        if not self.project or issue.fix is None:
            return ""
        message = ""

        def do(score: Score) -> None:
            nonlocal message
            message = apply_fix(score, issue.fix)

        self.project.decisions[issue.key()] = "aceptada"
        self.apply_edit(f"Corrección: {issue.fix.description}", do)
        self.status.emit(message)
        return message

    def accept_same_kind(self, issue: Issue) -> str:
        """Acepta de una vez todas las correcciones abiertas del mismo tipo (se deshace con un solo paso)."""
        if not self.project:
            return ""
        group = learning.same_kind(issue, self.project.issues)
        if not group:
            return ""
        done = 0

        def do(score: Score) -> None:
            nonlocal done
            for i in group:
                try:
                    apply_fix(score, i.fix)
                except Exception as exc:  # una corrección que ya no encaja no debe frenar las demás
                    log.warning("No se aplicó «%s»: %s", i.title, exc)
                    continue
                self.project.decisions[i.key()] = "aceptada"
                done += 1

        self.apply_edit(f"Corrección ×{len(group)}: {issue.fix.description}", do)
        msg = f"{done} corrección(es) aplicadas. Puedes deshacerlas todas con Ctrl+Z."
        self.status.emit(msg)
        return msg

    def reject_issue(self, issue: Issue) -> None:
        if not self.project:
            return
        self.project.decisions[issue.key()] = "rechazada"
        issue.status = "rechazada"
        self.project.dirty = True
        self.revalidate()

    def reopen_issue(self, issue: Issue) -> None:
        if not self.project:
            return
        self.project.decisions.pop(issue.key(), None)
        self.revalidate()

    # ------------------------------------------------------------------ selección
    def select(self, note_ids: list[str]) -> None:
        self.selection = list(note_ids)
        self.selection_changed.emit(self.selection)

    def focus_measure(self, index: int) -> None:
        self.measure_focus.emit(index)

    # ------------------------------------------------------------------ guardado
    def save_project(self, path: Path) -> Path:
        assert self.project is not None
        saved = self.project.save(path)
        self.status.emit(f"Proyecto guardado en {saved}")
        return saved

    def autosave(self) -> None:
        if self.project and self.project.dirty:
            try:
                self.project.save_autosave()
            except Exception as exc:
                log.warning("No se pudo autoguardar: %s", exc)

    def save_config(self) -> None:
        try:
            save_config(self.cfg)
        except OSError as exc:
            log.warning("No se pudo guardar la configuración: %s", exc)

    def shutdown(self) -> None:
        self._autosave.stop()
        self.player.close()
        self.save_config()
