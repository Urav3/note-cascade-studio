"""Paso 5 — Exportar: MIDI (tipo 1 y 0), MusicXML, informe, proyecto y vídeo."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from note_cascade.export.exporter import ExportRequest, export_project
from note_cascade.ui import theme
from note_cascade.ui.widgets.common import Card, hbox, muted_label, text_button, title_label
from note_cascade.ui.workers import Task


class ExportPage(QWidget):
    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.task: Task | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        root.addWidget(title_label("Exportar"))
        root.addWidget(muted_label("Cada archivo se vuelve a leer después de guardarlo para comprobar que es válido "
                                   "y contiene la música esperada."))

        form_card = Card()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.e_dir = QLineEdit()
        b_dir = text_button("Elegir…", "folder")
        b_dir.clicked.connect(self._choose_dir)
        form.addRow("Carpeta de salida", hbox(self.e_dir, b_dir))
        self.e_name = QLineEdit()
        form.addRow("Nombre de los archivos", self.e_name)
        e = self.c.cfg.export
        self.ck_mid1 = QCheckBox("MIDI estándar tipo 1 (pistas separadas: mano derecha, mano izquierda…)")
        self.ck_mid0 = QCheckBox("MIDI tipo 0 (una sola pista, para dispositivos antiguos)")
        self.ck_xml = QCheckBox("MusicXML editable (MuseScore, Finale, Sibelius, Dorico…)")
        self.ck_rep = QCheckBox("Informe de reconocimiento (HTML) con todas las advertencias")
        self.ck_proj = QCheckBox("Proyecto (.ncsproj) para seguir corrigiendo más tarde en Note Cascade Studio")
        self.ck_proj.setToolTip("Archivo comprimido (ZIP) con la partitura corregida, las imágenes de las páginas, "
                                "los resultados de los motores y tus decisiones. Se abre con Note Cascade Studio; "
                                "en el Bloc de notas solo se ven caracteres sin sentido.")
        self.ck_video = QCheckBox("Vídeo del visualizador en cascada (MP4, opcional y más lento)")
        for ck, val in ((self.ck_mid1, e.midi_type1), (self.ck_mid0, e.midi_type0), (self.ck_xml, e.musicxml),
                        (self.ck_rep, e.report), (self.ck_proj, e.project), (self.ck_video, e.video)):
            ck.setChecked(val)
        box = QVBoxLayout()
        for ck in (self.ck_mid1, self.ck_mid0, self.ck_xml, self.ck_rep, self.ck_proj, self.ck_video):
            box.addWidget(ck)
        form.addRow("Formatos", box)
        self.ck_hands = QCheckBox("Separar mano derecha e izquierda en pistas distintas (piano)")
        self.ck_hands.setChecked(True)
        self.ck_repeats = QCheckBox("Expandir repeticiones en el MIDI")
        self.ck_repeats.setChecked(self.c.expand_repeats)
        self.sp_tempo = QDoubleSpinBox()
        self.sp_tempo.setDecimals(0)
        self.sp_tempo.setRange(20, 320)
        self.sp_tempo.setSuffix(" negras por minuto")
        self.sp_tempo.setValue(self.c.cfg.music.default_tempo_bpm)
        self.sp_tempo.setToolTip("Se usa solo donde la partitura no tiene una indicación metronómica.")
        form.addRow("Opciones", self.ck_hands)
        form.addRow("", self.ck_repeats)
        form.addRow("Tempo predeterminado", self.sp_tempo)
        form_card.layout_.addLayout(form)
        root.addWidget(form_card)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        root.addWidget(self.warning)
        self.b_export = text_button("Exportar", "export", primary=True)
        self.b_export.clicked.connect(self.export)
        self.b_open = text_button("Abrir carpeta de salida", "folder")
        self.b_open.clicked.connect(self.open_folder)
        self.progress = muted_label("", wrap=False)
        root.addLayout(hbox(self.b_export, self.b_open, self.progress, None))
        self.results = QListWidget()
        self.results.setWordWrap(True)
        root.addWidget(self.results, 1)
        self.c.project_changed.connect(self.load)
        self.c.issues_changed.connect(self._update_warning)

    def load(self) -> None:
        project = self.c.project
        self.e_dir.setText(str(self.c.cfg.output_dir()))
        if project is not None:
            src = Path(project.source_path) if project.source_path else None
            self.e_name.setText(src.stem if src else project.name)
        self.results.clear()
        self._update_warning()

    def _update_warning(self) -> None:
        project = self.c.project
        if project is None:
            self.warning.setText("No hay ninguna partitura abierta.")
            self.b_export.setEnabled(False)
            return
        self.b_export.setEnabled(True)
        errors = sum(1 for i in project.issues if i.status == "abierta" and i.severity == "error")
        warns = sum(1 for i in project.issues if i.status == "abierta" and i.severity == "aviso")
        pal = theme.current()
        if errors or warns:
            self.warning.setText(f"⚠ Quedan {errors} errores probables y {warns} elementos para revisar. Puedes "
                                 "exportar igualmente; el informe los enumera.")
            self.warning.setStyleSheet(f"color: {pal.warning};")
        else:
            self.warning.setText("✔ No quedan avisos pendientes.")
            self.warning.setStyleSheet(f"color: {pal.success};")

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Carpeta de salida", self.e_dir.text() or str(Path.home()))
        if d:
            self.e_dir.setText(d)

    def open_folder(self) -> None:
        d = Path(self.e_dir.text() or self.c.cfg.output_dir())
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def export(self) -> None:
        project = self.c.project
        if project is None or (self.task and self.task.is_running()):
            return
        out_dir = Path(self.e_dir.text().strip() or self.c.cfg.output_dir())
        name = self.e_name.text().strip() or project.name
        existing = [s for s in (".mid", ".musicxml", "_informe.html", ".ncsproj") if (out_dir / f"{name}{s}").exists()]
        overwrite = True
        if existing:
            ans = QMessageBox.question(self, "Archivos existentes",
                                       f"Ya existen archivos con el nombre «{name}» en esa carpeta. ¿Reemplazarlos?\n\n"
                                       "«No» guarda copias con un número al final.",
                                       QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                return
            overwrite = ans == QMessageBox.Yes
        cfg = self.c.cfg
        cfg.export.output_dir = str(out_dir)
        cfg.export.midi_type1 = self.ck_mid1.isChecked()
        cfg.export.midi_type0 = self.ck_mid0.isChecked()
        cfg.export.musicxml = self.ck_xml.isChecked()
        cfg.export.report = self.ck_rep.isChecked()
        cfg.export.project = self.ck_proj.isChecked()
        cfg.export.video = self.ck_video.isChecked()
        if abs(cfg.music.default_tempo_bpm - self.sp_tempo.value()) > 1e-6:
            cfg.music.default_tempo_bpm = self.sp_tempo.value()
            self.c.rebuild_performance()
        req = ExportRequest(out_dir, name, cfg.export.midi_type1, cfg.export.midi_type0, cfg.export.musicxml,
                            cfg.export.report, cfg.export.project, cfg.export.video, self.ck_repeats.isChecked(),
                            self.ck_hands.isChecked(), overwrite)
        self.results.clear()
        self.b_export.setEnabled(False)

        def work(progress, message, cancel):
            return export_project(project, req, cfg, progress)

        self.task = Task(work, self)
        self.task.signals.progress.connect(lambda f, m: self.progress.setText(f"{int(f * 100)} % · {m}"))
        self.task.signals.finished.connect(self._done)
        self.task.signals.failed.connect(self._failed)
        self.task.start()
        self.c.save_config()

    def _done(self, outcomes) -> None:
        self.b_export.setEnabled(True)
        pal = theme.current()
        all_ok = True
        for o in outcomes:
            item = QListWidgetItem(f"{'✔' if o.ok else '✖'}  {o.path.name if o.path else o.kind}\n{o.message}")
            item.setForeground(Qt.white if o.ok else Qt.red)
            if o.ok:
                item.setForeground(theme_color(pal.success))
            else:
                item.setForeground(theme_color(pal.danger))
            self.results.addItem(item)
            all_ok = all_ok and o.ok
        self.progress.setText("Exportación completada y verificada." if all_ok else
                              "Algunos archivos no se pudieron exportar (ver detalles).")
        if self.c.project is not None and any(o.kind == "project" and o.ok for o in outcomes):
            self.c.project.dirty = False
        if self.c.cfg.export.open_folder_after and all_ok:
            self.open_folder()

    def _failed(self, msg: str, details: str) -> None:
        self.b_export.setEnabled(True)
        self.progress.setText("")
        QMessageBox.warning(self, "No se pudo exportar", msg)


def theme_color(hex_color: str):
    from PySide6.QtGui import QColor
    return QColor(hex_color)
