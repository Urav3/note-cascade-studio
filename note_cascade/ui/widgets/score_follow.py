"""Ventana pequeña que sigue la partitura mientras suena la cascada.

Muestra la página original y resalta, en el color de cada mano, las notas que
están sonando en ese momento, además del compás actual. Sirve para comparar lo
que se oye y se ve en la cascada con lo que está escrito.
"""

from __future__ import annotations

import bisect

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QGraphicsRectItem, QLabel, QVBoxLayout, QWidget

from note_cascade.ui import theme
from note_cascade.ui.widgets.common import IconButton, hbox, muted_label
from note_cascade.ui.widgets.page_view import PageView


class ScoreFollowWindow(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent, Qt.Window | Qt.Tool)
        self.c = controller
        self.player = controller.player
        self.setWindowTitle("Partitura — seguimiento")
        self.resize(560, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.view = PageView()
        self.page_label = QLabel("—")
        self.b_prev = IconButton("left", "Página anterior")
        self.b_next = IconButton("right", "Página siguiente")
        self.b_prev.clicked.connect(lambda: self._manual_page(self.page_pos - 1))
        self.b_next.clicked.connect(lambda: self._manual_page(self.page_pos + 1))
        b_zin = IconButton("zoom_in", "Acercar (Ctrl + rueda)")
        b_zout = IconButton("zoom_out", "Alejar")
        b_fit = IconButton("fit", "Ajustar al ancho")
        b_zin.clicked.connect(lambda: self.view.zoom_by(1.25))
        b_zout.clicked.connect(lambda: self.view.zoom_by(0.8))
        b_fit.clicked.connect(self.view.fit_width)
        self.ck_follow = QCheckBox("Seguir la reproducción")
        self.ck_follow.setChecked(True)
        self.ck_follow.setToolTip("Pasa de página y desplaza la vista al compás que está sonando")
        root.addLayout(hbox(self.b_prev, self.page_label, self.b_next, None, b_zout, b_zin, b_fit))
        root.addWidget(self.view, 1)
        self.info = muted_label("", wrap=True)
        root.addLayout(hbox(self.ck_follow, None))
        root.addWidget(self.info)

        self.page_pos = -1           # posición en project.pages
        self._notes_by_id: dict = {}
        self._starts: list[float] = []
        self._max_len = 0.0
        self._active: list[QGraphicsRectItem] = []
        self._shown_ids: tuple = ()
        self._measure = -1
        self.c.performance_changed.connect(self.reload)
        self.c.project_changed.connect(self.reload)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self.reload()
        self.timer.start()

    def hideEvent(self, ev) -> None:  # noqa: N802
        self.timer.stop()
        super().hideEvent(ev)

    def reload(self) -> None:
        project = self.c.project
        perf = self.c.performance
        self._notes_by_id = {n.id: n for n in project.score.notes} if project else {}
        notes = perf.notes if perf else []
        self._starts = [n.start_s for n in notes]
        self._max_len = max((n.end_s - n.start_s for n in notes), default=0.0)
        self._shown_ids = ()
        self._measure = -1
        if project is None or not project.pages:
            self.view.clear()
            self.page_label.setText("sin imagen")
            self.info.setText("Este proyecto no tiene páginas escaneadas (se importó un archivo estructurado).")
            return
        has_pos = any(n.position is not None for n in project.score.notes)
        self.info.setText("" if has_pos else "El motor no dio la posición de las notas en la página: solo se "
                                             "resalta el compás.")
        self.page_pos = -1
        self._show_page(0)

    def _manual_page(self, pos: int) -> None:
        self.ck_follow.setChecked(False)  # el usuario manda; se vuelve a activar con la casilla
        self._show_page(pos)

    def _show_page(self, pos: int) -> None:
        project = self.c.project
        if project is None or not project.pages:
            return
        pos = max(0, min(pos, len(project.pages) - 1))
        if pos == self.page_pos:
            return
        self.page_pos = pos
        rec = project.pages[pos]
        self.page_label.setText(f"{rec.label} ({pos + 1} de {len(project.pages)})")
        self.b_prev.setEnabled(pos > 0)
        self.b_next.setEnabled(pos < len(project.pages) - 1)
        self.view.set_image(QPixmap(str(project.page_path(rec.original_file))), tuple(rec.original_size), None)
        self._active.clear()
        self._shown_ids = ()
        pal = theme.current()
        for m in project.score.measures:
            if m.position is not None and m.position.page == rec.index:
                r = self.view.norm_rect(m.position.x, m.position.y, m.position.w, m.position.h)
                self.view.add_measure_box(m.index, r, pal.neutral)
        if self._measure >= 0:
            self.view.highlight_measure(self._measure)

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        try:
            self._tick_inner()
        except (IndexError, KeyError, AttributeError, RuntimeError):
            pass  # estado transitorio mientras se recalcula la interpretación

    def _tick_inner(self) -> None:
        perf = self.c.performance
        project = self.c.project
        if perf is None or project is None or not project.pages or not perf.play_measures:
            return
        now = self.player.position()
        pm = perf.play_measures[perf.play_index_at_s(now + 1e-3)]
        m = project.score.measures[pm.measure]
        if pm.measure != self._measure:
            self._measure = pm.measure
            if self.ck_follow.isChecked() and m.position is not None:
                pages = [p.index for p in project.pages]
                if m.position.page in pages:
                    self._show_page(pages.index(m.position.page))
            self.view.highlight_measure(pm.measure)
        # notas que suenan ahora
        lo = bisect.bisect_left(self._starts, now - self._max_len - 0.01)
        hi = bisect.bisect_right(self._starts, now)
        sounding = [n for n in perf.notes[lo:hi] if n.start_s <= now < n.end_s]
        ids = tuple(sorted(nid for n in sounding for nid in n.note_ids))
        if ids == self._shown_ids:
            return
        self._shown_ids = ids
        self._draw_active(sounding)

    def _draw_active(self, sounding) -> None:
        sc = self.view.scene()
        for item in self._active:
            try:
                sc.removeItem(item)
            except RuntimeError:
                pass
        self._active.clear()
        rec = self.c.project.pages[self.page_pos]
        union: QRectF | None = None
        for pn in sounding:
            notes = [self._notes_by_id.get(nid) for nid in pn.note_ids]
            notes = [n for n in notes if n is not None]
            # Una nota ligada suena una vez pero está escrita en varios compases: se marca solo la
            # parte escrita en el compás que suena ahora.
            here = [n for n in notes if n.measure == self._measure]
            for n in (here or notes[:1]):
                # posición aproximada = no se sabe dónde está en la imagen: mejor no marcar nada
                if n.position is None or n.position.approximate or n.position.page != rec.index:
                    continue
                color = QColor(theme.hand_color(n.hand, n.part))  # la mano de cada nota escrita
                r = self.view.norm_rect(n.position.x, n.position.y, n.position.w, n.position.h).adjusted(-4, -4, 4, 4)
                item = QGraphicsRectItem(r)
                pen = QPen(color, 2.5)
                pen.setCosmetic(True)
                item.setPen(pen)
                fill = QColor(color)
                fill.setAlpha(110)
                item.setBrush(QBrush(fill))
                item.setZValue(10)
                sc.addItem(item)
                self._active.append(item)
                union = r if union is None else union.united(r)
        if union is not None and self.ck_follow.isChecked():
            self.view.ensureVisible(union, 40, 60)
