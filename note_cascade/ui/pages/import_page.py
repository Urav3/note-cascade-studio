"""Paso 1 — Importar: arrastrar, seleccionar o pegar partituras."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from note_cascade.app.errors import NoteCascadeError
from note_cascade.importers.detect import FILE_DIALOG_FILTER
from note_cascade.importers.document import SourceDocument, document_from_image, open_document
from note_cascade.ui import theme
from note_cascade.ui.widgets.common import (
    Card,
    IconButton,
    Pill,
    hbox,
    muted_label,
    numpy_to_pixmap,
    text_button,
    title_label,
    vbox,
)
from note_cascade.ui.workers import Task

log = logging.getLogger(__name__)

KIND_LABEL = {
    "pdf": "PDF", "image": "Imagen", "clipboard": "Captura pegada", "musicxml": "MusicXML", "mxl": "MusicXML comprimido",
    "midi": "MIDI", "project": "Proyecto",
}
PAGE_KIND = {"vector": "vectorial", "raster": "imagen", "mixed": "mixto", "blank": "vacía", "image": "imagen"}


class DropZone(QFrame):
    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(170)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        icon = QLabel(theme.icon_text("import") or "⬇")
        icon.setFont(theme.icon_font(38))
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"color: {theme.current().accent};")
        t = QLabel("Arrastra aquí tus partituras")
        t.setObjectName("SectionTitle")
        t.setAlignment(Qt.AlignCenter)
        s = muted_label("PDF, fotos, capturas (PNG, JPG, WEBP, BMP, TIFF), MusicXML o MIDI.\n"
                        "También puedes hacer clic para elegir archivos o pegar una captura con Ctrl+V.")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon)
        lay.addWidget(t)
        lay.addWidget(s)

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls() or ev.mimeData().hasImage():
            ev.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, ev) -> None:  # noqa: N802
        self._set_active(False)

    def dropEvent(self, ev) -> None:  # noqa: N802
        self._set_active(False)
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        elif ev.mimeData().hasImage():
            self.files_dropped.emit([ev.mimeData().imageData()])

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()


def qimage_to_numpy(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine() // 4, 4)[:, :w]
    rgba = arr.astype(np.float32)
    alpha = rgba[..., 3:4] / 255.0
    rgb = rgba[..., :3] * alpha + 255.0 * (1 - alpha)  # composición sobre blanco (capturas con transparencia)
    return rgb.astype(np.uint8)


class ImportPage(QWidget):
    analyze_requested = Signal(object)        # SourceDocument
    batch_requested = Signal(list)            # lista de SourceDocument
    open_project_requested = Signal(str)
    settings_requested = Signal()
    diagnostics_requested = Signal()

    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.docs: list[SourceDocument] = []
        self._preview_page = 0
        self._tasks: list[Task] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        root.addWidget(title_label("Importar partitura"))
        root.addWidget(muted_label("Todo se procesa en tu ordenador: las partituras nunca se suben a internet."))

        grid = QHBoxLayout()
        grid.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(10)
        self.drop = DropZone()
        self.drop.files_dropped.connect(self.add_items)
        self.drop.clicked.connect(self.browse)
        left.addWidget(self.drop)
        b_browse = text_button("Seleccionar archivos…", "open")
        b_browse.clicked.connect(self.browse)
        b_paste = text_button("Pegar captura (Ctrl+V)", "paste")
        b_paste.clicked.connect(self.paste)
        b_project = text_button("Abrir proyecto…", "folder")
        b_project.clicked.connect(self.open_project)
        left.addLayout(hbox(b_browse, b_paste, b_project, None))
        self.list = QListWidget()
        self.list.setIconSize(QSize(42, 54))
        self.list.currentRowChanged.connect(self._on_select)
        self.list.setMinimumHeight(150)
        left.addWidget(self.list, 1)
        b_remove = text_button("Quitar", "delete")
        b_remove.clicked.connect(self.remove_selected)
        b_clear = text_button("Vaciar lista", "clean")
        b_clear.clicked.connect(self.clear)
        self.count_label = muted_label("", wrap=False)
        left.addLayout(hbox(b_remove, b_clear, None, self.count_label))
        grid.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(12)
        self.preview_card = Card()
        self.preview_card.add(title_label("Vista previa", "SectionTitle"))
        self.preview = QLabel("Selecciona un archivo para verlo aquí.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(260, 320)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet(f"background: {theme.current().surface2}; border-radius: 10px;")
        self.preview_card.add(self.preview, 1)
        self.b_prev = IconButton("left", "Página anterior")
        self.b_next = IconButton("right", "Página siguiente")
        self.b_prev.clicked.connect(lambda: self._step_page(-1))
        self.b_next.clicked.connect(lambda: self._step_page(1))
        self.page_label = muted_label("", wrap=False)
        self.preview_card.layout_.addLayout(hbox(self.b_prev, self.page_label, self.b_next, None))
        self.info = muted_label("")
        self.preview_card.add(self.info)
        right.addWidget(self.preview_card, 1)

        self.engines_card = Card(flat=True, padding=12)
        self.engines_card.add(title_label("Motores de reconocimiento", "SectionTitle"))
        self.engines_box = QVBoxLayout()
        self.engines_card.layout_.addLayout(self.engines_box)
        b_diag = text_button("Diagnóstico", "health")
        b_diag.clicked.connect(self.diagnostics_requested.emit)
        self.engines_card.layout_.addLayout(hbox(b_diag, None))
        right.addWidget(self.engines_card)
        grid.addLayout(right, 2)
        root.addLayout(grid, 1)

        bottom = QHBoxLayout()
        self.options_label = muted_label("", wrap=True)
        b_opts = text_button("Opciones avanzadas…", "settings")
        b_opts.clicked.connect(self.settings_requested.emit)
        self.b_batch = text_button("Convertir todos y exportar", "batch",
                                   tooltip="Procesa todos los archivos de la lista uno tras otro y guarda los "
                                           "resultados en la carpeta de salida.")
        self.b_batch.clicked.connect(self._batch)
        self.b_analyze = text_button("Analizar partitura", "analyze", primary=True)
        self.b_analyze.clicked.connect(self._analyze)
        bottom.addWidget(self.options_label, 1)
        bottom.addWidget(b_opts)
        bottom.addWidget(self.b_batch)
        bottom.addWidget(self.b_analyze)
        root.addLayout(bottom)

        QShortcut(QKeySequence.Paste, self, activated=self.paste)
        self.c.engines_changed.connect(self.refresh_engines)
        self.refresh_options()
        self._update_buttons()

    # ------------------------------------------------------------------
    def refresh_options(self) -> None:
        p = self.c.cfg.processing
        engine = {"auto": "automático", "audiveris": "Audiveris", "homr": "HOMR", "oemer": "oemer"}.get(p.engine, p.engine)
        dpi = "automática" if p.dpi == "auto" else f"{p.dpi} ppp"
        check = {"auto": "si hace falta", "siempre": "siempre", "nunca": "nunca"}.get(p.cross_check, p.cross_check)
        self.options_label.setText(f"Motor: {engine} · Resolución: {dpi} · Corrección de imagen: {p.image_correction}"
                                   f" · Segundo motor: {check}")

    def refresh_engines(self) -> None:
        while self.engines_box.count():
            item = self.engines_box.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        pal = theme.current()
        for st in self.c.engine_status.values():
            pill = Pill("listo" if st.available else ("opcional" if st.id == "oemer" else "no disponible"),
                        pal.success if st.available else (pal.neutral if st.id == "oemer" else pal.danger))
            name = QLabel(f"{st.name} {st.version}".strip())
            detail = muted_label(st.detail)
            detail.setToolTip(st.suggestion or st.location)
            row = hbox(name, pill, None)
            self.engines_box.addLayout(row)
            if not st.available or "sin datos" in st.detail:
                self.engines_box.addWidget(detail)

    # ------------------------------------------------------------------ archivos
    def browse(self) -> None:
        start = self.c.cfg.ui.recent_files[0] if self.c.cfg.ui.recent_files else str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Seleccionar partituras", str(Path(start).parent), FILE_DIALOG_FILTER)
        if paths:
            self.add_items(paths)

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Abrir proyecto", str(Path.home()),
                                              "Proyecto de Note Cascade Studio (*.ncsproj)")
        if path:
            self.open_project_requested.emit(path)

    def paste(self) -> None:
        cb = QGuiApplication.clipboard()
        md = cb.mimeData()
        if md is not None and md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self.add_items(paths)
                return
        img = cb.image()
        if img.isNull():
            QMessageBox.information(self, "Pegar captura",
                                    "El portapapeles no contiene ninguna imagen.\n\nHaz una captura (por ejemplo con "
                                    "Win+Mayús+S), cópiala y vuelve a pulsar Ctrl+V.")
            return
        arr = qimage_to_numpy(img)
        n = sum(1 for d in self.docs if d.kind == "clipboard") + 1
        doc = document_from_image(arr, name=f"Captura {n}")
        self._append(doc)

    def add_items(self, items: list) -> None:
        for it in items:
            if isinstance(it, QImage) or (hasattr(it, "isNull") and not isinstance(it, str)):
                arr = qimage_to_numpy(QImage(it))
                self._append(document_from_image(arr, name="Imagen arrastrada"))
                continue
            path = Path(it)
            if path.suffix.lower() == ".ncsproj":
                self.open_project_requested.emit(str(path))
                continue
            if any(d.path and Path(d.path) == path for d in self.docs):
                continue
            self._open_async(path)

    def _open_async(self, path: Path) -> None:
        placeholder = QListWidgetItem(f"{path.name} — analizando el archivo…")
        self.list.addItem(placeholder)

        def work(progress, message, cancel):
            return open_document(path)

        task = Task(work, self)

        def done(doc):
            row = self.list.row(placeholder)
            self.list.takeItem(row)
            self._append(doc)
            self._remember(path)

        def failed(msg, details):
            row = self.list.row(placeholder)
            self.list.takeItem(row)
            QMessageBox.warning(self, "No se puede abrir el archivo", msg)

        task.signals.finished.connect(done)
        task.signals.failed.connect(failed)
        self._tasks.append(task)
        task.start()

    def _remember(self, path: Path) -> None:
        rec = self.c.cfg.ui.recent_files
        s = str(path)
        if s in rec:
            rec.remove(s)
        rec.insert(0, s)
        del rec[10:]

    def _append(self, doc: SourceDocument) -> None:
        self.docs.append(doc)
        item = QListWidgetItem(self._item_text(doc))
        try:
            if doc.pages:
                thumb = doc.pages[0].thumbnail(120)
                item.setIcon(QIcon(numpy_to_pixmap(thumb)))
        except Exception as exc:
            log.warning("Sin miniatura: %s", exc)
        self.list.addItem(item)
        self.list.setCurrentItem(item)
        self._update_buttons()

    def _item_text(self, doc: SourceDocument) -> str:
        kind = KIND_LABEL.get(doc.kind, doc.kind)
        if doc.pages:
            kinds = {PAGE_KIND.get(p.kind_hint, p.kind_hint) for p in doc.pages}
            pages = f"{len(doc.pages)} página{'s' if len(doc.pages) != 1 else ''}"
            detail = f"{kind} · {pages}" + (f" · {', '.join(sorted(kinds))}" if doc.kind == "pdf" else "")
        else:
            detail = f"{kind} · se usa directamente, sin reconocimiento óptico"
        return f"{doc.name}\n{detail}"

    def remove_selected(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.docs):
            self.docs.pop(row).close()
            self.list.takeItem(row)
        self._update_buttons()

    def clear(self) -> None:
        for d in self.docs:
            d.close()
        self.docs.clear()
        self.list.clear()
        self.preview.setText("Selecciona un archivo para verlo aquí.")
        self.info.setText("")
        self._update_buttons()

    def current_doc(self) -> SourceDocument | None:
        row = self.list.currentRow()
        return self.docs[row] if 0 <= row < len(self.docs) else None

    def _update_buttons(self) -> None:
        self.b_analyze.setEnabled(self.current_doc() is not None)
        self.b_batch.setEnabled(len(self.docs) > 1)
        self.count_label.setText(f"{len(self.docs)} archivo(s)" if self.docs else "")

    # ------------------------------------------------------------------ vista previa
    def _on_select(self, row: int) -> None:
        self._preview_page = 0
        self._show_preview()
        self._update_buttons()

    def _step_page(self, d: int) -> None:
        doc = self.current_doc()
        if doc and doc.pages:
            self._preview_page = max(0, min(len(doc.pages) - 1, self._preview_page + d))
            self._show_preview()

    def _show_preview(self) -> None:
        doc = self.current_doc()
        if doc is None:
            return
        if not doc.pages:
            self.preview.setText(f"{KIND_LABEL.get(doc.kind, doc.kind)}\n\nEste formato ya contiene la música "
                                 "estructurada: se abrirá directamente para escucharla, verla en cascada, editarla "
                                 "y exportarla.")
            self.page_label.setText("")
            self.info.setText(" ".join(doc.warnings))
            return
        page = doc.pages[self._preview_page]
        try:
            thumb = page.thumbnail(700)
            pix = numpy_to_pixmap(thumb)
            self.preview.setPixmap(pix.scaled(self.preview.size() - QSize(16, 16), Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation))
        except Exception as exc:
            self.preview.setText(f"No se pudo mostrar la página: {exc}")
        self.page_label.setText(f"{page.label} de {len(doc.pages)}")
        info = []
        if page.pdf_info is not None:
            pi = page.pdf_info
            kind = {"vector": "PDF vectorial (exportado desde un programa de notación)",
                    "raster": "PDF con imagen (escaneo, foto o captura)", "mixed": "PDF mixto (vectores e imágenes)",
                    "blank": "página sin contenido visible"}.get(pi.kind, pi.kind)
            info.append(kind)
            if pi.image_dpi:
                info.append(f"resolución de la imagen ≈ {pi.image_dpi:.0f} ppp")
            if pi.words:
                info.append(f"capa de texto: {len(pi.words)} palabras (se usan para títulos, tempos, etc.; las notas "
                            "se reconocen ópticamente)")
        elif page.native_dpi:
            info.append(f"imagen de {page.native_dpi:.0f} ppp")
        if doc.warnings:
            info += doc.warnings
        self.info.setText(" · ".join(info))

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._show_preview()

    # ------------------------------------------------------------------
    def _analyze(self) -> None:
        doc = self.current_doc()
        if doc is not None:
            self.analyze_requested.emit(doc)

    def _batch(self) -> None:
        if len(self.docs) > 1:
            self.batch_requested.emit(list(self.docs))
