"""Ventana «Diagnóstico de altura»: por qué una nota suena a la altura que suena."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from note_cascade.ui.widgets.common import hbox, muted_label, text_button
from note_cascade.ui.workers import Task


class PitchTraceDialog(QDialog):
    MAX_COLUMNS = 6

    def __init__(self, controller, note_ids: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.setWindowTitle("Diagnóstico de altura")
        self.resize(980, 640)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        lay.addWidget(muted_label(
            "Recorrido de cada nota desde la imagen hasta la tecla que se ilumina, con números MIDI (el Do central "
            "es MIDI 60; el nombre de la octava puede variar entre programas, el número no). La altura esperada sale "
            "de la posición de la cabeza en el pentagrama, la clave activa, las alteraciones y la indicación de "
            "octava medida en la imagen."))
        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setWordWrap(True)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Compás", "Pent.", "Esperada", "Modelo", "Dónde se origina"])
        self.list.setRootIsDecorated(False)
        self.list.header().setSectionResizeMode(4, QHeaderView.Stretch)
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        split = QSplitter(Qt.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("Notas seleccionadas")
        self.title.setObjectName("SectionTitle")
        tl.addWidget(self.title)
        tl.addWidget(self.table, 1)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("Pulsa «Revisar toda la partitura» para buscar notas que no coinciden con la imagen.")
        self.summary.setWordWrap(True)
        bl.addWidget(self.summary)
        bl.addWidget(self.list, 1)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([380, 220])
        lay.addWidget(split, 1)
        self.b_all = text_button("Revisar toda la partitura", "health")
        self.b_all.clicked.connect(self.check_all)
        self.b_export = text_button("Exportar registro técnico…", "export")
        self.b_export.clicked.connect(self.export)
        b_close = text_button("Cerrar")
        b_close.clicked.connect(self.accept)
        lay.addLayout(hbox(self.b_all, self.b_export, None, b_close))
        self.task = None
        self._all = None
        self.show_notes(note_ids or list(controller.selection))

    # ------------------------------------------------------------------
    def _perfs(self):
        from note_cascade.export.exporter import performance_options
        from note_cascade.music_model.timeline import build_performance
        score = self.c.project.score
        export = build_performance(score, performance_options(self.c.cfg, self.c.expand_repeats))
        return export, self.c.performance

    def _trace(self, note_ids):
        from note_cascade.omr.pitch_trace import trace_notes
        export, playback = self._perfs()
        return trace_notes(self.c.project.score, self.c.project, note_ids, export_perf=export, playback_perf=playback)

    def show_notes(self, note_ids: list[str]) -> None:
        from note_cascade.omr.pitch_trace import describe
        self.table.clear()
        project = self.c.project
        ids = [i for i in note_ids if project and project.score.note_by_id(i) and not project.score.note_by_id(i).rest]
        ids = ids[: self.MAX_COLUMNS]
        if not ids:
            self.title.setText("Selecciona una o varias notas en la página o en el editor para ver su recorrido.")
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        traces = self._trace(ids)
        rows = [describe(t) for t in traces]
        self.title.setText(f"{len(traces)} nota(s) seleccionada(s)"
                           + (f" (se muestran las {self.MAX_COLUMNS} primeras)" if len(note_ids) > self.MAX_COLUMNS else ""))
        self.table.setColumnCount(1 + len(traces))
        self.table.setRowCount(len(rows[0]))
        self.table.setHorizontalHeaderLabels(["Etapa"] + [f"c. {t.measure} · pent. {t.staff}" for t in traces])
        for r, (label, _v) in enumerate(rows[0]):
            self.table.setItem(r, 0, QTableWidgetItem(label))
            for c, desc in enumerate(rows, start=1):
                item = QTableWidgetItem(desc[r][1])
                if label == "Resultado":
                    item.setForeground(Qt.darkGreen if traces[c - 1].status == "ok" else Qt.red
                                       if traces[c - 1].status == "diferencia" else Qt.gray)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()

    def check_all(self) -> None:
        self.summary.setText("Revisando todas las notas con la imagen…")
        self.b_all.setEnabled(False)
        export, playback = self._perfs()
        project = self.c.project

        def work(progress, message, cancel):
            from note_cascade.omr.pitch_trace import trace_notes
            return trace_notes(project.score, project, None, export_perf=export, playback_perf=playback)

        self.task = Task(work, self)
        self.task.signals.finished.connect(self._show_all)
        self.task.signals.failed.connect(lambda m, d: (self.summary.setText(m), self.b_all.setEnabled(True)))
        self.task.start()

    def _show_all(self, traces) -> None:
        self._all = traces
        self.b_all.setEnabled(True)
        self.list.clear()
        ok = sum(t.status == "ok" for t in traces)
        bad = [t for t in traces if t.status == "diferencia"]
        none = sum(t.status == "sin_imagen" for t in traces)
        self.summary.setText(f"{ok} nota(s) coinciden con la imagen · {len(bad)} no coinciden · {none} sin posición "
                             "exacta en la imagen (no se pueden comparar). Haz clic en una para ver su recorrido.")
        for t in bad:
            item = QTreeWidgetItem([t.measure, str(t.staff), str(t.expected_midi), str(t.model_midi), t.origin])
            item.setData(0, Qt.UserRole, t.note_id)
            self.list.addTopLevelItem(item)

    def _pick(self, item, *_args) -> None:
        nid = item.data(0, Qt.UserRole)
        if nid:
            self.c.select([nid])
            self.show_notes([nid])

    def export(self) -> None:
        from note_cascade.omr.pitch_trace import to_csv
        project = self.c.project
        default = str(Path(self.c.cfg.output_dir()) / f"{project.name}_registro_alturas.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Exportar registro técnico", default, "CSV (*.csv)")
        if not path:
            return
        traces = self._all or self._trace(None)
        Path(path).write_text(to_csv(traces), encoding="utf-8-sig")
        self.summary.setText(f"Registro guardado en {path} ({len(traces)} notas).")
