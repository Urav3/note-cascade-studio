"""Visor de página con zoom y superposiciones (pentagramas, compases, notas, avisos, textos)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from note_cascade.image_processing import transforms as T
from note_cascade.ui import theme

CATEGORY_COLORS = {
    "titulo": "#a78bfa", "subtitulo": "#a78bfa", "compositor": "#a78bfa", "credito": "#a78bfa",
    "instrumento": "#94a3b8", "numero_pagina": "#64748b", "numero_compas": "#64748b", "referencia": "#64748b",
    "nota_editorial": "#64748b", "letra": "#38bdf8", "cifrado": "#f472b6", "tempo": "#f59e0b",
    "cambio_tempo": "#f59e0b", "caracter": "#2dd4bf", "dinamica": "#fb7185", "ensayo": "#e879f9",
    "navegacion": "#facc15", "octava": "#f97316", "pedal": "#a3e635", "digitacion": "#64748b",
    "compases_espera": "#facc15", "compas": "#facc15", "simbolo_musical": "#475569", "dudoso": "#ef4444",
}


class _NoteMarker(QGraphicsRectItem):
    def __init__(self, rect: QRectF, note_id: str) -> None:
        super().__init__(rect)
        self.note_id = note_id
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)


class _MeasureBox(QGraphicsRectItem):
    def __init__(self, rect: QRectF, index: int) -> None:
        super().__init__(rect)
        self.measure_index = index


class PageView(QGraphicsView):
    note_clicked = Signal(str, bool)   # id, añadir a la selección
    notes_selected = Signal(list, bool)  # ids dentro de un rectángulo (Mayús + arrastrar), añadir
    measure_clicked = Signal(int)
    zoom_changed = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(theme.current().bg))
        self._pix_item: QGraphicsPixmapItem | None = None
        self._size = (1, 1)
        self._matrix = np.eye(3)
        self._original_size = (1, 1)
        self._use_matrix = False
        self._overlays: list[QGraphicsItem] = []
        self._markers: dict[str, _NoteMarker] = {}
        self._measure_boxes: dict[int, _MeasureBox] = {}
        self._highlight: list[QGraphicsItem] = []
        self._zoom = 1.0
        self._band_start: QPointF | None = None
        self._band_item: QGraphicsRectItem | None = None
        self._auto_fit = True   # ajustar al ancho al cambiar el tamaño, hasta que el usuario use el zoom

    # ------------------------------------------------------------------ imagen
    def set_image(self, pixmap: QPixmap, original_size: tuple[int, int], matrix: np.ndarray | None = None) -> None:
        sc = self.scene()
        sc.clear()
        self._overlays.clear()
        self._markers.clear()
        self._measure_boxes.clear()
        self._highlight.clear()
        self._pix_item = sc.addPixmap(pixmap)
        self._pix_item.setZValue(-10)
        self._size = (pixmap.width(), pixmap.height())
        self._original_size = original_size
        self._use_matrix = matrix is not None
        self._matrix = matrix if matrix is not None else np.eye(3)
        sc.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        if self._auto_fit:
            self._fit()
            QTimer.singleShot(0, self._refit)  # el tamaño definitivo del visor puede llegar después

    def clear(self) -> None:
        self.scene().clear()
        self._pix_item = None
        self._markers.clear()
        self._measure_boxes.clear()

    def to_scene(self, nx: float, ny: float) -> QPointF:
        w, h = self._original_size
        x, y = nx * w, ny * h
        if self._use_matrix:
            x, y = T.apply_matrix(self._matrix, x, y)
        return QPointF(x, y)

    def norm_rect(self, x: float, y: float, w: float, h: float) -> QRectF:
        a = self.to_scene(x, y)
        b = self.to_scene(x + w, y + h)
        return QRectF(a, b).normalized()

    # ------------------------------------------------------------------ superposiciones
    def add_rect(self, rect: QRectF, color: str, width: float = 2.0, fill_alpha: int = 0, z: float = 1,
                 dash: bool = False) -> QGraphicsRectItem:
        item = QGraphicsRectItem(rect)
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        if dash:
            pen.setStyle(Qt.DashLine)
        item.setPen(pen)
        if fill_alpha:
            c = QColor(color)
            c.setAlpha(fill_alpha)
            item.setBrush(QBrush(c))
        item.setZValue(z)
        self.scene().addItem(item)
        self._overlays.append(item)
        return item

    def add_label(self, pos: QPointF, text: str, color: str, size: float = 14) -> None:
        item = QGraphicsSimpleTextItem(text)
        item.setBrush(QColor(color))
        f = item.font()
        f.setPixelSize(int(size))
        f.setBold(True)
        item.setFont(f)
        item.setPos(pos)
        item.setZValue(5)
        self.scene().addItem(item)
        self._overlays.append(item)

    def add_measure_box(self, index: int, rect: QRectF, color: str, label: str = "") -> None:
        box = _MeasureBox(rect, index)
        pen = QPen(QColor(color), 1.5)
        pen.setCosmetic(True)
        box.setPen(pen)
        c = QColor(color)
        c.setAlpha(18)
        box.setBrush(c)
        box.setZValue(0.5)
        self.scene().addItem(box)
        self._measure_boxes[index] = box
        if label:
            self.add_label(QPointF(rect.left() + 3, rect.top() - 22), label, color, 14)

    def add_note_marker(self, note_id: str, rect: QRectF, color: str, status_color: str | None,
                        approximate: bool) -> None:
        m = _NoteMarker(rect.adjusted(-2, -2, 2, 2), note_id)
        pen = QPen(QColor(status_color or color), 2.4 if status_color else 1.4)
        pen.setCosmetic(True)
        if approximate:
            pen.setStyle(Qt.DotLine)
        m.setPen(pen)
        c = QColor(color)
        c.setAlpha(70)
        m.setBrush(c)
        m.setZValue(3)
        self.scene().addItem(m)
        self._markers[note_id] = m

    def add_issue_marker(self, rect: QRectF, color: str) -> None:
        r = 13
        center = QPointF(rect.right(), rect.top())
        dot = QGraphicsEllipseItem(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r))
        dot.setBrush(QColor(color))
        pen = QPen(QColor("#ffffff"), 1.5)
        pen.setCosmetic(True)
        dot.setPen(pen)
        dot.setZValue(6)
        self.scene().addItem(dot)
        txt = QGraphicsSimpleTextItem("!")
        f = txt.font()
        f.setPixelSize(16)
        f.setBold(True)
        txt.setFont(f)
        txt.setBrush(QColor("#ffffff"))
        txt.setPos(center.x() - 3, center.y() - 11)
        txt.setZValue(7)
        self.scene().addItem(txt)
        self._overlays += [dot, txt]

    def highlight_notes(self, note_ids: list[str]) -> None:
        for item in self._highlight:
            try:
                self.scene().removeItem(item)
            except RuntimeError:
                pass
        self._highlight.clear()
        rects = []
        for nid in note_ids:
            m = self._markers.get(nid)
            if m is None:
                continue
            r = m.rect().adjusted(-5, -5, 5, 5)
            item = QGraphicsRectItem(r)
            pen = QPen(QColor("#ffffff"), 3)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(9)
            self.scene().addItem(item)
            self._highlight.append(item)
            rects.append(r)
        if rects:
            union = rects[0]
            for r in rects[1:]:
                union = union.united(r)
            self.ensureVisible(union, 80, 80)

    def highlight_measure(self, index: int) -> None:
        box = self._measure_boxes.get(index)
        for item in list(self._highlight):
            if getattr(item, "_is_measure_hl", False):
                try:
                    self.scene().removeItem(item)
                except RuntimeError:
                    pass
                self._highlight.remove(item)
        if box is None:
            return
        item = QGraphicsRectItem(box.rect().adjusted(-4, -4, 4, 4))
        pen = QPen(QColor(theme.current().accent), 3)
        pen.setCosmetic(True)
        item.setPen(pen)
        c = QColor(theme.current().accent)
        c.setAlpha(30)
        item.setBrush(c)
        item.setZValue(8)
        item._is_measure_hl = True  # type: ignore[attr-defined]
        self.scene().addItem(item)
        self._highlight.append(item)
        self.ensureVisible(box.rect(), 60, 60)

    # ------------------------------------------------------------------ zoom
    def fit_width(self) -> None:
        """Ajusta la página al ancho del visor y lo mantiene ajustado al cambiar de tamaño."""
        self._auto_fit = True
        self._fit()

    def _fit(self) -> None:
        if self._pix_item is None:
            return
        vw = self.viewport().width()
        if vw < 50:
            return  # todavía sin tamaño real (pestaña oculta)
        w = self._size[0]
        scale = (vw - 20) / max(1, w)
        if abs(scale - self._zoom) < 1e-4 and not self.transform().isIdentity():
            return
        self.resetTransform()
        self.scale(scale, scale)
        self._zoom = scale
        self.zoom_changed.emit(scale)

    def _refit(self) -> None:
        if self._auto_fit:
            self._fit()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._refit()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        QTimer.singleShot(0, self._refit)

    @property
    def zoom(self) -> float:
        return self._zoom

    def zoom_by(self, factor: float) -> None:
        self._auto_fit = False
        new = max(0.05, min(6.0, self._zoom * factor))
        f = new / self._zoom
        self.scale(f, f)
        self._zoom = new
        self.zoom_changed.emit(new)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        if ev.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super().wheelEvent(ev)

    def _marker_near(self, view_pos, tolerance: float = 10.0) -> _NoteMarker | None:
        """Marcador bajo el ratón o, si no hay, el más cercano a menos de ``tolerance`` píxeles de pantalla."""
        for it in self.items(view_pos):
            if isinstance(it, _NoteMarker):
                return it
        best, best_d = None, None
        for m in self._markers.values():
            r = self.mapFromScene(m.sceneBoundingRect()).boundingRect()
            dx = max(r.left() - view_pos.x(), 0, view_pos.x() - r.right())
            dy = max(r.top() - view_pos.y(), 0, view_pos.y() - r.bottom())
            d = (dx * dx + dy * dy) ** 0.5
            if d <= tolerance and (best_d is None or d < best_d):
                best, best_d = m, d
        return best

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            mods = ev.modifiers()
            vp = ev.position().toPoint()
            if mods & Qt.ShiftModifier:
                # Mayús + arrastrar: rectángulo de selección (en vez de mover la página)
                self._band_start = self.mapToScene(vp)
                self._band_item = QGraphicsRectItem(QRectF(self._band_start, self._band_start))
                pen = QPen(QColor(theme.current().accent), 1.5, Qt.DashLine)
                pen.setCosmetic(True)
                self._band_item.setPen(pen)
                fill = QColor(theme.current().accent)
                fill.setAlpha(30)
                self._band_item.setBrush(fill)
                self._band_item.setZValue(20)
                self.scene().addItem(self._band_item)
                return
            marker = self._marker_near(vp)
            if marker is not None:
                self.note_clicked.emit(marker.note_id, bool(mods & Qt.ControlModifier))
                return
            if not (mods & Qt.ControlModifier):  # con Ctrl se está seleccionando: no cambiar de compás
                for it in self.items(vp):
                    if isinstance(it, _MeasureBox):
                        self.measure_clicked.emit(it.measure_index)
                        break
            else:
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._band_start is not None and self._band_item is not None:
            self._band_item.setRect(QRectF(self._band_start, self.mapToScene(ev.position().toPoint())).normalized())
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if self._band_start is not None and self._band_item is not None:
            band = self._band_item.rect()
            try:
                self.scene().removeItem(self._band_item)
            except RuntimeError:
                pass
            self._band_start = None
            self._band_item = None
            ids = [nid for nid, m in self._markers.items() if band.intersects(m.sceneBoundingRect())]
            if ids:
                self.notes_selected.emit(ids, bool(ev.modifiers() & Qt.ControlModifier))
            return
        super().mouseReleaseEvent(ev)
