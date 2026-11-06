"""Paso 2 — Analizar: progreso real de cada etapa, cancelación y resumen."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from note_cascade.export.exporter import ExportRequest, export_project
from note_cascade.importers.document import SourceDocument
from note_cascade.pipeline.conversion import ConversionOptions, ConversionPipeline
from note_cascade.ui import theme
from note_cascade.ui.widgets.common import Card, StatTile, hbox, muted_label, text_button, title_label
from note_cascade.ui.workers import Task

STAGES = [
    (0.0, "Preparar páginas", "rasterizado, calidad, inclinación, perspectiva y resolución"),
    (0.15, "Leer textos", "títulos, tempos, letras y referencias (no se convierten en notas)"),
    (0.22, "Reconocer la música", "motor de reconocimiento óptico"),
    (0.75, "Verificar", "segundo motor solo si hace falta"),
    (0.94, "Validar", "ritmo, alturas, estructura y manos"),
]


class AnalyzePage(QWidget):
    finished = Signal(object)          # Project
    review_requested = Signal()
    listen_requested = Signal()
    export_requested = Signal()
    reanalyze_requested = Signal()

    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.task: Task | None = None
        self.doc: SourceDocument | None = None
        self._t0 = 0.0
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        self.title = title_label("Analizar")
        self.subtitle = muted_label("Importa una partitura y pulsa «Analizar partitura».")
        root.addWidget(self.title)
        root.addWidget(self.subtitle)

        prog = Card()
        self.stage_label = QLabel("En espera")
        self.stage_label.setObjectName("SectionTitle")
        self.detail_label = muted_label("")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(12)
        self.elapsed = muted_label("", wrap=False)
        self.b_cancel = text_button("Cancelar", "cancel")
        self.b_cancel.setObjectName("Danger")
        self.b_cancel.clicked.connect(self.cancel)
        self.b_cancel.setEnabled(False)
        prog.add(self.stage_label)
        prog.add(self.detail_label)
        prog.add(self.bar)
        prog.layout_.addLayout(hbox(self.elapsed, None, self.b_cancel))
        self.stage_rows: list[tuple[QLabel, QLabel]] = []
        stages_box = QGridLayout()
        stages_box.setHorizontalSpacing(10)
        for i, (_start, name, desc) in enumerate(STAGES):
            mark = QLabel("○")
            mark.setFixedWidth(18)
            lbl = QLabel(f"<b>{name}</b> <span style='color:{theme.current().muted}'>— {desc}</span>")
            stages_box.addWidget(mark, i, 0)
            stages_box.addWidget(lbl, i, 1)
            self.stage_rows.append((mark, lbl))
        prog.layout_.addLayout(stages_box)
        root.addWidget(prog)

        self.result = Card()
        self.result.add(title_label("Resultado", "SectionTitle"))
        tiles = QHBoxLayout()
        self.t_measures = StatTile("compases")
        self.t_notes = StatTile("notas")
        self.t_errors = StatTile("errores probables", color=theme.current().danger)
        self.t_warn = StatTile("para revisar", color=theme.current().warning)
        self.t_ok = StatTile("compases verificados", color=theme.current().success)
        for t in (self.t_measures, self.t_notes, self.t_errors, self.t_warn, self.t_ok):
            tiles.addWidget(t)
        self.result.layout_.addLayout(tiles)
        self.engine_label = muted_label("")
        self.result.add(self.engine_label)
        b_review = text_button("Revisar resultados", "review", primary=True)
        b_review.clicked.connect(self.review_requested.emit)
        b_listen = text_button("Escuchar y ver en cascada", "listen")
        b_listen.clicked.connect(self.listen_requested.emit)
        b_export = text_button("Exportar ahora", "export")
        b_export.clicked.connect(self.export_requested.emit)
        b_again = text_button("Volver a analizar con otras opciones…", "refresh")
        b_again.clicked.connect(self.reanalyze_requested.emit)
        self.result.layout_.addLayout(hbox(b_review, b_listen, b_export, None, b_again))
        self.result.setVisible(False)
        root.addWidget(self.result)

        root.addWidget(title_label("Registro", "SectionTitle"))
        self.log = QListWidget()
        self.log.setWordWrap(True)
        root.addWidget(self.log, 1)

        self.clock = QTimer(self)
        self.clock.setInterval(500)
        self.clock.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    def is_busy(self) -> bool:
        return self.task is not None and self.task.is_running()

    def start(self, doc: SourceDocument, options: ConversionOptions | None = None) -> None:
        if self.is_busy():
            return
        self.doc = doc
        self.title.setText(f"Analizando «{doc.name}»")
        self.subtitle.setText("El análisis se ejecuta en segundo plano; puedes cancelarlo en cualquier momento.")
        self.result.setVisible(False)
        self.log.clear()
        self.bar.setValue(0)
        for mark, _ in self.stage_rows:
            mark.setText("○")
        options = options or ConversionOptions.from_config(self.c.cfg)
        engines = self.c.engines

        def work(progress, message, cancel):
            pipeline = ConversionPipeline(self.c.cfg, engines, progress, cancel, message)
            return pipeline.convert(doc, options)

        self.task = Task(work, self)
        self.task.signals.progress.connect(self._progress)
        self.task.signals.message.connect(self._message)
        self.task.signals.finished.connect(self._done)
        self.task.signals.failed.connect(self._failed)
        self.task.signals.cancelled.connect(self._cancelled)
        self._t0 = time.monotonic()
        self.clock.start()
        self.b_cancel.setEnabled(True)
        self.stage_label.setText("Iniciando…")
        self.task.start()

    def start_batch(self, docs: list[SourceDocument]) -> None:
        if self.is_busy():
            return
        self.title.setText(f"Conversión por lotes ({len(docs)} archivos)")
        self.subtitle.setText(f"Los resultados se guardan en {self.c.cfg.output_dir()}")
        self.result.setVisible(False)
        self.log.clear()
        cfg = self.c.cfg
        engines = self.c.engines
        n = len(docs)

        def work(progress, message, cancel):
            results = []
            for k, doc in enumerate(docs):
                cancel.check()
                message(f"── {doc.name} ({k + 1} de {n})")

                def sub(frac, msg, k=k):
                    progress((k + frac * 0.92) / n, f"[{k + 1}/{n}] {msg}")

                try:
                    pipeline = ConversionPipeline(cfg, engines, sub, cancel, message)
                    project = pipeline.convert(doc, ConversionOptions.from_config(cfg))
                    e = cfg.export
                    req = ExportRequest(cfg.output_dir(), doc.name, e.midi_type1, e.midi_type0, e.musicxml,
                                        e.report, e.project, False, cfg.music.repeats != "no_expandir",
                                        overwrite=False)
                    outcomes = export_project(project, req, cfg)
                    ok = all(o.ok for o in outcomes)
                    open_issues = sum(1 for i in project.issues if i.status == "abierta" and i.severity != "info")
                    message(f"{'✔' if ok else '✖'} {doc.name}: {len(project.score.measures)} compases, "
                            f"{open_issues} elementos para revisar. " + " ".join(o.message for o in outcomes if not o.ok))
                    results.append((doc.name, ok, project))
                except Exception as exc:  # un archivo con problemas no detiene el lote
                    from note_cascade.app.errors import CancelledError, NoteCascadeError
                    if isinstance(exc, CancelledError):
                        raise
                    text = exc.user_text() if isinstance(exc, NoteCascadeError) else str(exc)
                    message(f"✖ {doc.name}: {text}")
                    results.append((doc.name, False, None))
            progress(1.0, "Lote terminado")
            return results

        self.task = Task(work, self)
        self.task.signals.progress.connect(self._progress)
        self.task.signals.message.connect(self._message)
        self.task.signals.finished.connect(self._batch_done)
        self.task.signals.failed.connect(self._failed)
        self.task.signals.cancelled.connect(self._cancelled)
        self._t0 = time.monotonic()
        self.clock.start()
        self.b_cancel.setEnabled(True)
        self.task.start()

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.stage_label.setText("Cancelando…")
            self.b_cancel.setEnabled(False)

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        self.elapsed.setText(f"Tiempo transcurrido: {int(time.monotonic() - self._t0)} s")

    def _progress(self, frac: float, msg: str) -> None:
        self.bar.setValue(int(frac * 1000))
        self.stage_label.setText(msg)
        for i, (start, _name, _d) in enumerate(STAGES):
            nxt = STAGES[i + 1][0] if i + 1 < len(STAGES) else 1.01
            mark = self.stage_rows[i][0]
            if frac >= nxt or frac >= 1.0:
                mark.setText("✔")
                mark.setStyleSheet(f"color: {theme.current().success};")
            elif frac >= start:
                mark.setText("●")
                mark.setStyleSheet(f"color: {theme.current().accent};")

    def _message(self, text: str) -> None:
        item = QListWidgetItem(text)
        self.log.addItem(item)
        self.log.scrollToBottom()

    def _finish_common(self) -> None:
        self.clock.stop()
        self._tick()
        self.b_cancel.setEnabled(False)

    def _done(self, project) -> None:
        self._finish_common()
        self.bar.setValue(1000)
        self._progress(1.0, "Análisis terminado")
        self.show_summary(project)
        self.finished.emit(project)

    def show_summary(self, project) -> None:
        s = project.score
        counts = {"error": 0, "aviso": 0, "info": 0}
        for i in project.issues:
            if i.status == "abierta":
                counts[i.severity] += 1
        statuses = project.options.get("status", {}).get("measures", {})
        ok = sum(1 for v in statuses.values() if v == "ok")
        self.t_measures.set(str(len(s.measures)))
        self.t_notes.set(str(len(s.pitched_notes())))
        self.t_errors.set(str(counts["error"]))
        self.t_warn.set(str(counts["aviso"]))
        self.t_ok.set(f"{ok}")
        engines = ", ".join(f"{r.engine_name} {r.version} ({r.role}{', ' + r.positions if r.positions else ''})"
                            for r in project.engine_runs) or "importado directamente"
        self.engine_label.setText(f"Motores: {engines}. Los compases «verificados» tienen evidencia real "
                                  "(confianza del motor, acuerdo entre motores o datos estructurados).")
        self.title.setText(f"«{s.title or project.name}» analizado")
        self.subtitle.setText("Revisa los elementos marcados o exporta directamente.")
        self.result.setVisible(True)

    def _batch_done(self, results) -> None:
        self._finish_common()
        ok = sum(1 for _, good, _ in results if good)
        self.stage_label.setText(f"Lote terminado: {ok} de {len(results)} archivos convertidos y verificados.")
        last = next((p for _, good, p in reversed(results) if p is not None), None)
        if last is not None:
            self.show_summary(last)
            self.finished.emit(last)

    def _failed(self, msg: str, details: str) -> None:
        self._finish_common()
        self.stage_label.setText("No se pudo completar el análisis")
        self.detail_label.setText(msg)
        self._message("✖ " + msg)
        self.title.setText("El análisis no se completó")

    def _cancelled(self) -> None:
        self._finish_common()
        self.stage_label.setText("Análisis cancelado")
        self._message("Operación cancelada por el usuario.")
