"""Diálogos: opciones avanzadas, diagnóstico y licencias."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from note_cascade import APP_NAME, ISSUES_URL, REPO_URL, VERSION_LABEL
from note_cascade.app import paths
from note_cascade.app.config import AppConfig
from note_cascade.ui.widgets.common import hbox, muted_label, text_button
from note_cascade.ui.workers import Task


def _combo(items: list[tuple[str, str]], current: str) -> QComboBox:
    cb = QComboBox()
    for label, value in items:
        cb.addItem(label, value)
    idx = max(0, next((i for i, (_l, v) in enumerate(items) if v == current), 0))
    cb.setCurrentIndex(idx)
    return cb


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Opciones avanzadas")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(muted_label("Los valores automáticos son adecuados en la mayoría de los casos. Cambia estas "
                                  "opciones solo si el resultado no es bueno."))
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        p = cfg.processing
        w1 = QWidget()
        f1 = QFormLayout(w1)
        self.dpi = _combo([("Automática (recomendado)", "auto"), ("200 ppp", "200"), ("300 ppp", "300"),
                           ("400 ppp", "400"), ("600 ppp (partituras muy densas)", "600")], p.dpi)
        self.correction = _combo([("Automática (según el tipo de imagen)", "auto"), ("Ninguna", "ninguna"),
                                  ("Suave (escaneos)", "suave"), ("Fuerte (fotos con sombras o perspectiva)", "fuerte")],
                                 p.image_correction)
        self.engine = _combo([("Automático (recomendado)", "auto"), ("Audiveris", "audiveris"), ("HOMR", "homr"),
                              ("oemer (si está instalado)", "oemer")], p.engine)
        self.cross = _combo([("Solo si hace falta (recomendado)", "auto"), ("Siempre (más lento, más evidencia)", "siempre"),
                             ("Nunca", "nunca")], p.cross_check)
        self.detect_text = QCheckBox("Detectar y clasificar textos (títulos, tempos, letras…)")
        self.detect_text.setChecked(p.detect_text)
        self.chords = QCheckBox("Convertir cifrados de acordes (C, Am, F#m7…) en notas en una pista aparte")
        self.chords.setChecked(p.chord_symbols_to_notes)
        self.hands = QCheckBox("Identificar mano derecha e izquierda en partituras de piano")
        self.hands.setChecked(p.piano_two_hands)
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 3600)
        self.timeout.setSuffix(" s por página")
        self.timeout.setValue(p.engine_timeout_per_page)
        f1.addRow("Resolución de procesamiento", self.dpi)
        f1.addRow("Corrección de imagen", self.correction)
        f1.addRow("Motor de reconocimiento", self.engine)
        f1.addRow("Segundo motor (verificación)", self.cross)
        f1.addRow("", self.detect_text)
        f1.addRow("", self.chords)
        f1.addRow("", self.hands)
        f1.addRow("Tiempo máximo", self.timeout)
        tabs.addTab(w1, "Reconocimiento")

        m = cfg.music
        w2 = QWidget()
        f2 = QFormLayout(w2)
        self.tempo = QDoubleSpinBox()
        self.tempo.setRange(20, 320)
        self.tempo.setValue(m.default_tempo_bpm)
        self.tempo.setSuffix(" negras por minuto")
        self.repeats = _combo([("Expandir repeticiones", "expandir"), ("No expandir", "no_expandir")], m.repeats)
        self.dyn = QCheckBox("Traducir dinámicas (pp, f, reguladores) a intensidad MIDI")
        self.dyn.setChecked(m.dynamics_to_velocity)
        self.pedal = QCheckBox("Pedal de resonancia (cuando se detecta)")
        self.pedal.setChecked(m.sustain_pedal)
        self.fermata = QCheckBox("Alargar los calderones")
        self.fermata.setChecked(m.fermata_stretch)
        self.arts = QCheckBox("Aplicar articulaciones (staccato acorta, acentos refuerzan, arpegios de abajo arriba)")
        self.arts.setChecked(m.articulation_effects)
        self.low = QSpinBox()
        self.low.setRange(0, 127)
        self.low.setValue(m.range_low)
        self.high = QSpinBox()
        self.high.setRange(0, 127)
        self.high.setValue(m.range_high)
        f2.addRow("Tempo predeterminado", self.tempo)
        f2.addRow("Repeticiones", self.repeats)
        f2.addRow("", self.dyn)
        f2.addRow("", self.pedal)
        f2.addRow("", self.fermata)
        f2.addRow("", self.arts)
        f2.addRow("Rango de notas válido (MIDI)", hbox(self.low, self.high))
        tabs.addTab(w2, "Música")

        e = cfg.export
        w3 = QWidget()
        f3 = QFormLayout(w3)
        self.out = QLineEdit(str(cfg.output_dir()))
        b_out = text_button("Elegir…", "folder")
        b_out.clicked.connect(lambda: self._pick_dir(self.out))
        self.open_after = QCheckBox("Abrir la carpeta al terminar de exportar")
        self.open_after.setChecked(e.open_folder_after)
        self.batch_auto = QCheckBox("En la conversión por lotes, exportar automáticamente cada archivo")
        self.batch_auto.setChecked(e.auto_export_batch)
        f3.addRow("Carpeta de salida", hbox(self.out, b_out))
        f3.addRow("", self.open_after)
        f3.addRow("", self.batch_auto)
        tabs.addTab(w3, "Exportación")

        g = cfg.engines
        w4 = QWidget()
        f4 = QFormLayout(w4)
        self.aud = QLineEdit(g.audiveris_path)
        self.aud.setPlaceholderText("automático (tools\\audiveris o instalación de Windows)")
        b_aud = text_button("Buscar…", "open")
        b_aud.clicked.connect(lambda: self._pick_file(self.aud, "Audiveris.exe (Audiveris.exe)"))
        self.homr = QLineEdit(g.homr_python)
        self.homr.setPlaceholderText("automático (el mismo Python de la aplicación)")
        self.oemer = QLineEdit(g.oemer_command)
        self.oemer.setPlaceholderText("automático (tools\\oemer o PATH)")
        self.tess = QLineEdit(g.tessdata_dir)
        self.tess.setPlaceholderText("automático (tools\\tessdata)")
        f4.addRow("Audiveris", hbox(self.aud, b_aud))
        f4.addRow("Python para HOMR", self.homr)
        f4.addRow("Comando oemer", self.oemer)
        f4.addRow("Datos de OCR (tessdata)", self.tess)
        tabs.addTab(w4, "Motores")

        w5 = QWidget()
        f5 = QFormLayout(w5)
        self.theme = _combo([("Oscuro", "oscuro"), ("Claro", "claro")], cfg.ui.theme)
        f5.addRow("Tema (requiere reiniciar)", self.theme)
        tabs.addTab(w5, "Apariencia")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _pick_dir(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta", edit.text())
        if d:
            edit.setText(d)

    def _pick_file(self, edit: QLineEdit, flt: str) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Elegir archivo", edit.text(), flt)
        if f:
            edit.setText(f)

    def _save(self) -> None:
        c = self.cfg
        c.processing.dpi = self.dpi.currentData()
        c.processing.image_correction = self.correction.currentData()
        c.processing.engine = self.engine.currentData()
        c.processing.cross_check = self.cross.currentData()
        c.processing.detect_text = self.detect_text.isChecked()
        c.processing.chord_symbols_to_notes = self.chords.isChecked()
        c.processing.piano_two_hands = self.hands.isChecked()
        c.processing.engine_timeout_per_page = self.timeout.value()
        c.music.default_tempo_bpm = self.tempo.value()
        c.music.repeats = self.repeats.currentData()
        c.music.dynamics_to_velocity = self.dyn.isChecked()
        c.music.sustain_pedal = self.pedal.isChecked()
        c.music.fermata_stretch = self.fermata.isChecked()
        c.music.articulation_effects = self.arts.isChecked()
        c.music.range_low, c.music.range_high = sorted((self.low.value(), self.high.value()))
        c.export.output_dir = self.out.text().strip()
        c.export.open_folder_after = self.open_after.isChecked()
        c.export.auto_export_batch = self.batch_auto.isChecked()
        c.engines.audiveris_path = self.aud.text().strip()
        c.engines.homr_python = self.homr.text().strip()
        c.engines.oemer_command = self.oemer.text().strip()
        c.engines.tessdata_dir = self.tess.text().strip()
        c.ui.theme = self.theme.currentData()
        self.accept()


class DiagnosticsDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.setWindowTitle("Diagnóstico del sistema")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(muted_label("Comprueba que estén disponibles los motores, los modelos y las dependencias. "
                                  "Nada de esto envía información a internet."))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        f = self.text.font()
        f.setFamily("Consolas")
        self.text.setFont(f)
        lay.addWidget(self.text, 1)
        b_run = text_button("Volver a comprobar", "refresh")
        b_run.clicked.connect(self.run)
        self.b_models = text_button("Descargar modelos de HOMR", "import")
        self.b_models.setToolTip("Descarga única (≈ 300 MB) desde el repositorio oficial de HOMR en GitHub.")
        self.b_models.clicked.connect(self.download_models)
        b_clean = text_button("Limpiar archivos temporales", "clean")
        b_clean.clicked.connect(self.clean)
        self.b_pitch = text_button("Diagnóstico de alturas…", "music")
        self.b_pitch.setToolTip("Revisa nota a nota que la altura de la partitura abierta coincide con la imagen "
                                "y exporta el registro técnico.")
        self.b_pitch.setEnabled(controller.project is not None and bool(controller.project.score.notes))
        self.b_pitch.clicked.connect(self.open_pitch_trace)
        b_logs = text_button("Abrir registros", "folder")
        b_logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir()))))
        self.b_report = text_button("Informar de un problema…", "link")
        self.b_report.setToolTip("Copia este diagnóstico (sin el nombre de tu carpeta de usuario) y abre la "
                                 "página de GitHub para contar el problema. No se envía nada automáticamente.")
        self.b_report.setEnabled(False)
        self.b_report.clicked.connect(self.report_problem)
        b_close = text_button("Cerrar")
        b_close.clicked.connect(self.accept)
        lay.addLayout(hbox(b_run, self.b_models, b_clean, self.b_pitch, b_logs, None, self.b_report, b_close))
        self.report = None
        self.task = None
        self.run()

    def report_problem(self) -> None:
        if self.report is None:
            return
        QGuiApplication.clipboard().setText(self.report.text(anonymous=True))
        ans = QMessageBox.information(
            self, "Informar de un problema",
            "El diagnóstico se copió al portapapeles, sin el nombre de tu carpeta de usuario.\n\n"
            "Se abrirá GitHub en el navegador (necesitas una cuenta gratuita). Elige «Informar de un error», "
            "cuenta qué pasó y pega el diagnóstico con Ctrl+V.\n\n"
            "Adjunta la partitura solo si es tuya o de dominio público; si no, basta una captura del compás "
            "que falla.", QMessageBox.Ok | QMessageBox.Cancel)
        if ans == QMessageBox.Ok:
            QDesktopServices.openUrl(QUrl(ISSUES_URL))

    def _show_report(self, rep) -> None:
        self.report = rep
        self.b_report.setEnabled(True)
        self.text.setPlainText(rep.text())
        self.c.refresh_engines()

    def open_pitch_trace(self) -> None:
        from note_cascade.ui.widgets.pitch_trace_dialog import PitchTraceDialog
        dlg = PitchTraceDialog(self.c, list(self.c.selection), self)
        dlg.show()
        dlg.check_all()

    def run(self) -> None:
        self.text.setPlainText("Comprobando…")
        cfg = self.c.cfg

        def work(progress, message, cancel):
            from note_cascade.app.diagnostics import run_diagnostics
            return run_diagnostics(cfg)

        self.task = Task(work, self)
        self.task.signals.finished.connect(self._show_report)
        self.task.signals.failed.connect(lambda m, d: self.text.setPlainText(m + "\n\n" + d))
        self.task.start()

    def download_models(self) -> None:
        engine = self.c.engines.get("homr")
        if engine is None:
            return
        ans = QMessageBox.question(self, "Descargar modelos",
                                   "Se descargarán los modelos de HOMR (≈ 300 MB) desde "
                                   "github.com/liebharc/homr. Solo se necesita una vez. ¿Continuar?")
        if ans != QMessageBox.Yes:
            return
        self.text.appendPlainText("\nDescargando modelos de HOMR…")

        def work(progress, message, cancel):
            return engine.download_models(progress, cancel)

        self.task = Task(work, self)
        self.task.signals.progress.connect(lambda f, m: None)
        self.task.signals.finished.connect(lambda r: (self.text.appendPlainText(r[1]), self.run()))
        self.task.signals.failed.connect(lambda m, d: self.text.appendPlainText(m))
        self.task.start()

    def clean(self) -> None:
        from note_cascade.project.project import clean_work_dirs, workspace_size
        keep = {self.c.project.workspace} if self.c.project else set()
        total = sum(workspace_size(d) for d in paths.work_dir().iterdir() if d.is_dir())
        ans = QMessageBox.question(self, "Limpiar temporales",
                                   f"Las carpetas de trabajo ocupan {total / 1e6:.0f} MB. Se borrarán las de las "
                                   "conversiones anteriores (no se tocan los proyectos .ncsproj guardados ni la "
                                   "conversión abierta). ¿Continuar?")
        if ans == QMessageBox.Yes:
            freed = clean_work_dirs(keep)
            cache = paths.user_data_dir() / "cache"
            if cache.exists():
                import shutil
                freed += sum(f.stat().st_size for f in cache.rglob("*") if f.is_file())
                shutil.rmtree(cache, ignore_errors=True)
            self.text.appendPlainText(f"\nSe liberaron {freed / 1e6:.0f} MB.")


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Acerca de {APP_NAME}")
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        text = f"<h2>{APP_NAME} {VERSION_LABEL}</h2>"
        text += ("<p>Conversor local de partituras a MIDI con revisión asistida y visualizador en cascada. "
                 "Licencia: <b>GNU AGPL v3 o posterior</b>.</p>")
        text += (f"<p><b>Código fuente y versiones nuevas:</b> <a href='{REPO_URL}'>{REPO_URL}</a><br>"
                 f"<b>Informar de un problema o proponer una mejora:</b> <a href='{ISSUES_URL}'>abrir en GitHub</a> "
                 "(en <i>Diagnóstico → Informar de un problema</i> se copia antes el diagnóstico).<br>"
                 "Una copia del código de esta versión está también en <code>codigo_fuente.zip</code>, junto al "
                 "programa.</p>")
        lic = paths.install_root() / "LICENCIAS_TERCEROS.md"
        if lic.exists():
            body = lic.read_text(encoding="utf-8")
            text += "<pre style='white-space: pre-wrap'>" + body.replace("<", "&lt;") + "</pre>"
        else:
            text += "<p>Consulta LICENCIAS_TERCEROS.md en la carpeta de instalación.</p>"
        browser.setHtml(text)
        lay.addWidget(browser, 1)
        b = text_button("Cerrar")
        b.clicked.connect(self.accept)
        lay.addLayout(hbox(None, b))
