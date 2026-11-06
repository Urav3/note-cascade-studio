"""Ventana principal y arranque de la interfaz."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QLinearGradient, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from note_cascade import APP_NAME, __version__
from note_cascade.app import paths
from note_cascade.app.config import load_config
from note_cascade.ui import theme
from note_cascade.ui.controller import AppController
from note_cascade.ui.dialogs import AboutDialog, DiagnosticsDialog, SettingsDialog
from note_cascade.ui.pages.analyze_page import AnalyzePage
from note_cascade.ui.pages.export_page import ExportPage
from note_cascade.ui.pages.import_page import ImportPage
from note_cascade.ui.pages.listen_page import ListenPage
from note_cascade.ui.pages.review_page import ReviewPage
from note_cascade.ui.widgets.common import muted_label
from note_cascade.ui.workers import Task

log = logging.getLogger(__name__)

STEPS = [("import", "1  Importar"), ("analyze", "2  Analizar"), ("review", "3  Revisar"),
         ("listen", "4  Escuchar"), ("export", "5  Exportar")]


def make_app_icon() -> QIcon:
    icon = QIcon()
    resource = paths.resources_dir() / "icon.png"
    if resource.exists():
        return QIcon(str(resource))
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor("#7c6cff"))
        grad.setColorAt(1, QColor("#21c7a8"))
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
        p.setBrush(QColor(255, 255, 255, 235))
        bw = size * 0.12
        for i, (x, h) in enumerate(((0.2, 0.42), (0.44, 0.6), (0.68, 0.32))):
            p.drawRoundedRect(int(size * x), int(size * 0.14 + i * size * 0.06), int(bw), int(size * h), bw / 3, bw / 3)
        p.setBrush(QColor(20, 22, 30, 200))
        p.drawRect(0, int(size * 0.8), size, int(size * 0.2))
        p.end()
        icon.addPixmap(pm)
    return icon


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.c = controller
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)
        self._open_task: Task | None = None
        self._init_task: Task | None = None

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.setCentralWidget(central)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 14)
        sl.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(make_app_icon().pixmap(40, 40))
        name = QLabel("Note Cascade")
        name.setObjectName("AppTitle")
        sub = QLabel("Studio · partituras a MIDI")
        sub.setObjectName("AppSubtitle")
        head = QHBoxLayout()
        head.addWidget(logo)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        tbox.addWidget(name)
        tbox.addWidget(sub)
        head.addLayout(tbox)
        sl.addLayout(head)
        sl.addSpacing(18)
        self.step_group = QButtonGroup(self)
        self.step_group.setExclusive(True)
        self.step_buttons: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(STEPS):
            b = QPushButton(f"  {label}")
            b.setObjectName("StepButton")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.step_group.addButton(b, i)
            self.step_buttons[key] = b
            sl.addWidget(b)
        self.step_group.idClicked.connect(self.go_step)
        sl.addStretch(1)
        for text, slot in (("Guardar proyecto", self.save_project), ("Opciones avanzadas", self.open_settings),
                           ("Diagnóstico", self.open_diagnostics), ("Licencias y ayuda", self.open_about)):
            b = QPushButton(text)
            b.setObjectName("StepButton")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            sl.addWidget(b)
        self.engine_summary = muted_label("Comprobando motores…")
        self.engine_summary.setStyleSheet("font-size: 11px;")
        sl.addSpacing(8)
        sl.addWidget(self.engine_summary)
        lay.addWidget(side)

        self.stack = QStackedWidget()
        self.page_import = ImportPage(controller)
        self.page_analyze = AnalyzePage(controller)
        self.page_review = ReviewPage(controller)
        self.page_listen = ListenPage(controller)
        self.page_export = ExportPage(controller)
        for w in (self.page_import, self.page_analyze, self.page_review, self.page_listen, self.page_export):
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)

        self.page_import.analyze_requested.connect(self.start_analysis)
        self.page_import.batch_requested.connect(self.start_batch)
        self.page_import.open_project_requested.connect(self.open_project)
        self.page_import.settings_requested.connect(self.open_settings)
        self.page_import.diagnostics_requested.connect(self.open_diagnostics)
        self.page_analyze.finished.connect(self._analysis_finished)
        self.page_analyze.review_requested.connect(lambda: self.go_step(2))
        self.page_analyze.listen_requested.connect(lambda: self.go_step(3))
        self.page_analyze.export_requested.connect(lambda: self.go_step(4))
        self.page_analyze.reanalyze_requested.connect(self.reanalyze)
        self.c.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.c.engines_changed.connect(self._update_engine_summary)
        self.c.project_changed.connect(self._update_steps)

        QShortcut(QKeySequence.Save, self, activated=self.save_project)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.page_import.browse)
        self.statusBar().showMessage("Listo. Arrastra una partitura para empezar.")
        self.go_step(0)
        self._update_steps()
        QTimer.singleShot(100, self._init_background)

    # ------------------------------------------------------------------
    def _init_background(self) -> None:
        def work(progress, message, cancel):
            from note_cascade.omr.registry import create_engines, engine_statuses
            engines = create_engines(self.c.cfg)
            return engines, engine_statuses(engines, refresh=False)

        self._init_task = Task(work, self)

        def done(result):
            self.c.engines, self.c.engine_status = result
            self.c.engines_changed.emit()
            self._offer_recovery()

        self._init_task.signals.finished.connect(done)
        self._init_task.signals.failed.connect(lambda m, d: self.engine_summary.setText("Error al comprobar motores"))
        self._init_task.start()
        threading.Thread(target=_prewarm_synth, daemon=True).start()

    def _update_engine_summary(self) -> None:
        parts = []
        for st in self.c.engine_status.values():
            mark = "●" if st.available else "○"
            parts.append(f"{mark} {st.name} {st.version if st.available else '(no disponible)'}")
        self.engine_summary.setText("Motores:\n" + "\n".join(parts))

    def _update_steps(self) -> None:
        has = self.c.project is not None
        for key in ("review", "listen", "export"):
            self.step_buttons[key].setEnabled(has)
        title = APP_NAME
        if has:
            title = f"{self.c.project.score.title or self.c.project.name} — {APP_NAME}"
        self.setWindowTitle(title)

    def go_step(self, index: int) -> None:
        keys = [k for k, _ in STEPS]
        if not self.step_buttons[keys[index]].isEnabled():
            return
        self.stack.setCurrentIndex(index)
        self.step_buttons[keys[index]].setChecked(True)
        if index != 3 and self.c.player.is_playing and index != 2:
            self.c.player.pause()
        if index == 4:
            self.page_export.load()

    # ------------------------------------------------------------------ análisis
    def start_analysis(self, doc) -> None:
        if self.page_analyze.is_busy():
            QMessageBox.information(self, "Análisis en curso", "Espera a que termine el análisis actual o cancélalo.")
            self.go_step(1)
            return
        if not self._confirm_discard():
            return
        self._last_doc = doc
        self.go_step(1)
        self.page_analyze.start(doc)

    def start_batch(self, docs) -> None:
        if self.page_analyze.is_busy():
            return
        ans = QMessageBox.question(self, "Conversión por lotes",
                                   f"Se convertirán {len(docs)} archivos uno tras otro y se exportarán a:\n"
                                   f"{self.c.cfg.output_dir()}\n\n¿Continuar?")
        if ans != QMessageBox.Yes:
            return
        self.go_step(1)
        self.page_analyze.start_batch(docs)

    def reanalyze(self) -> None:
        doc = getattr(self, "_last_doc", None) or self.c.document
        if doc is None:
            self.go_step(0)
            return
        if self.open_settings():
            self.page_analyze.start(doc)

    def _analysis_finished(self, project) -> None:
        doc = getattr(self, "_last_doc", None)
        self.c.set_project(project, doc)
        self._update_steps()
        self.statusBar().showMessage("Análisis terminado. Revisa los resultados o exporta directamente.", 8000)

    # ------------------------------------------------------------------ proyectos
    def _confirm_discard(self) -> bool:
        p = self.c.project
        if p is None or not p.dirty or not self.c.history.can_undo():
            return True
        ans = QMessageBox.question(self, "Correcciones sin guardar",
                                   "Tienes correcciones sin guardar en la partitura actual. ¿Guardar el proyecto antes "
                                   "de continuar?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if ans == QMessageBox.Cancel:
            return False
        if ans == QMessageBox.Save:
            return self.save_project()
        return True

    def save_project(self) -> bool:
        p = self.c.project
        if p is None:
            QMessageBox.information(self, "Guardar proyecto", "No hay ninguna partitura abierta.")
            return False
        default = p.project_file or str(self.c.cfg.output_dir() / f"{Path(p.source_path).stem or p.name}.ncsproj")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar proyecto", default,
                                              "Proyecto de Note Cascade Studio (*.ncsproj)")
        if not path:
            return False
        try:
            self.c.save_project(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "No se pudo guardar", f"El proyecto no se pudo guardar:\n{exc}")
            return False
        return True

    def open_project(self, path: str) -> None:
        if not self._confirm_discard():
            return
        cfg = self.c.cfg

        def work(progress, message, cancel):
            from note_cascade.pipeline.conversion import revalidate_project
            from note_cascade.project.project import Project
            project = Project.load(path)
            revalidate_project(project, cfg)
            return project

        self._open_task = Task(work, self)
        self._open_task.signals.finished.connect(self._project_opened)
        self._open_task.signals.failed.connect(lambda m, d: QMessageBox.warning(self, "No se pudo abrir", m))
        self._open_task.start()
        self.statusBar().showMessage("Abriendo proyecto…")

    def _project_opened(self, project) -> None:
        self.c.set_project(project)
        self._update_steps()
        self.page_analyze.show_summary(project)
        self.go_step(2)
        self.statusBar().showMessage(f"Proyecto abierto: {project.name}", 6000)

    def _offer_recovery(self) -> None:
        from note_cascade.project.project import list_autosaves, restore_autosave
        saves = list_autosaves()
        if not saves:
            return
        path, data = saves[0]
        name = data.get("score", {}).get("title") or data.get("name", "partitura")
        ans = QMessageBox.question(self, "Recuperar trabajo",
                                   f"Se encontró una copia de seguridad automática de «{name}» "
                                   f"({data.get('modified', '')}) que no se cerró correctamente.\n\n¿Recuperarla?")
        if ans == QMessageBox.Yes:
            try:
                project = restore_autosave(path)
                from note_cascade.pipeline.conversion import revalidate_project
                revalidate_project(project, self.c.cfg)
                self._project_opened(project)
            except Exception as exc:
                QMessageBox.warning(self, "No se pudo recuperar", str(exc))
        for p, _d in saves:
            try:
                p.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ diálogos
    def open_settings(self) -> bool:
        dlg = SettingsDialog(self.c.cfg, self)
        if dlg.exec():
            self.c.save_config()
            self.c.refresh_engines()
            self.page_import.refresh_options()
            if self.c.project is not None:
                self.c.rebuild_performance()
            return True
        return False

    def open_diagnostics(self) -> None:
        DiagnosticsDialog(self.c, self).exec()

    def open_about(self) -> None:
        AboutDialog(self).exec()

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.page_analyze.is_busy():
            ans = QMessageBox.question(self, "Salir", "Hay un análisis en curso. ¿Cancelarlo y salir?")
            if ans != QMessageBox.Yes:
                ev.ignore()
                return
            self.page_analyze.cancel()
            if self.page_analyze.task:
                self.page_analyze.task.wait(8000)
        p = self.c.project
        if p is not None and p.dirty and self.c.history.can_undo():
            ans = QMessageBox.question(self, "Salir", "Hay correcciones sin guardar. ¿Guardar el proyecto?",
                                       QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                ev.ignore()
                return
            if ans == QMessageBox.Save and not self.save_project():
                ev.ignore()
                return
        if p is not None:
            try:
                (paths.autosave_dir() / f"{p.id}.json").unlink(missing_ok=True)
            except OSError:
                pass
        self.c.shutdown()
        ev.accept()


def _prewarm_synth() -> None:
    try:
        from note_cascade.player import synth
        for m in range(36, 90, 1):
            synth.get_sample(m, 78, "piano")
    except Exception:
        pass


def run_gui(files: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NoteCascadeStudio.App")
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("NoteCascadeStudio")
    cfg = load_config()
    theme.apply_theme(app, cfg.ui.theme)
    app.setWindowIcon(make_app_icon())
    controller = AppController(cfg)
    window = MainWindow(controller)
    window.show()
    if files:
        window.page_import.add_items([f for f in files if Path(f).exists()])

    last_shown = {"t": 0.0, "open": False}

    def excepthook(exc_type, exc, tb):
        import time as _time
        log.error("Error no controlado", exc_info=(exc_type, exc, tb))
        now = _time.monotonic()
        # Un error repetido (p. ej. en un temporizador) no debe abrir diálogos en cadena.
        if last_shown["open"] or now - last_shown["t"] < 10:
            return
        last_shown["t"] = now
        last_shown["open"] = True
        try:
            QMessageBox.critical(window, "Error inesperado",
                                 f"Ocurrió un error inesperado:\n{exc}\n\nLa aplicación sigue abierta; si el problema "
                                 "persiste, revisa el registro en Diagnóstico.")
        except Exception:
            pass
        finally:
            last_shown["open"] = False

    sys.excepthook = excepthook
    return app.exec()
