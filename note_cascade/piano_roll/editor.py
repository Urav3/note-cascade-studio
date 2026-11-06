"""Editor de rollo de piano para corregir notas sin saber leer partituras.

Cada nota es un rectángulo: su altura vertical es la tecla del piano y su
longitud es la duración. Se puede seleccionar, arrastrar (altura y tiempo),
alargar/acortar desde el borde derecho, añadir con doble clic y borrar con
Supr. Los colores indican la mano y el borde indica el estado de revisión.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from note_cascade.music_model.model import Note, Score, describe_duration
from note_cascade.music_model.pitch import is_black_key, midi_name
from note_cascade.music_model.timeline import measure_lengths
from note_cascade.ui import theme

KEYS_W = 54
HEADER_H = 22
REST_LANE_H = 16
MIN_ROW_H = 11


@dataclass
class _Drag:
    kind: str           # move | resize | band
    start: QPointF
    note_ids: list[str]
    origin_note: Note | None = None
    dx_q: Fraction = Fraction(0)
    dmidi: int = 0
    new_duration: Fraction | None = None
    current: QPointF | None = None


class PianoRollEditor(QWidget):
    selection_changed = Signal(list)
    edit_move = Signal(list, int, object)       # ids, semitonos, desplazamiento (Fraction)
    edit_duration = Signal(list, object)        # ids, nueva duración
    edit_add = Signal(int, object, int, object)  # compás, onset, midi, duración
    edit_delete = Signal(list)
    edit_hand_toggle = Signal(list)
    measure_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.score: Score | None = None
        self.m_from = 0
        self.m_to = 0
        self.grid = Fraction(1, 4)      # semicorchea
        self.default_duration = Fraction(1)
        self.selected: set[str] = set()
        # vista previa de una corrección: notas que cambian (se atenúan) y cómo quedarían (contorno)
        self.preview_add: list[Note] = []
        self.preview_remove: set[str] = set()
        self.status: dict[str, str] = {}
        self.playhead_q: float | None = None
        self._drag: _Drag | None = None
        self._lengths: list[Fraction] = []
        self._starts: list[Fraction] = []
        self.low, self.high = 48, 84
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ------------------------------------------------------------------ datos
    def set_score(self, score: Score | None, status: dict[str, str] | None = None) -> None:
        self.score = score
        self.status = status or {}
        if score is not None:
            self._lengths = measure_lengths(score)
            self._starts = [Fraction(0)]
            for L in self._lengths:
                self._starts.append(self._starts[-1] + L)
            self.m_to = min(self.m_to, len(score.measures) - 1)
        self.selected = {i for i in self.selected if score and score.note_by_id(i)}
        self._fit_pitch()
        self.update()

    def set_range(self, m_from: int, m_to: int) -> None:
        if self.score is None or not self.score.measures:
            return
        n = len(self.score.measures)
        self.m_from = max(0, min(m_from, n - 1))
        self.m_to = max(self.m_from, min(m_to, n - 1))
        self._fit_pitch()
        self.update()

    def set_preview(self, added: list[Note], removed_ids: set[str]) -> None:
        self.preview_add = list(added)
        self.preview_remove = set(removed_ids)
        self.update()

    def set_selection(self, ids: list[str]) -> None:
        self.selected = set(ids)
        self.update()

    def visible_notes(self) -> list[Note]:
        if self.score is None:
            return []
        return [n for n in self.score.notes if self.m_from <= n.measure <= self.m_to]

    def _fit_pitch(self) -> None:
        notes = [n for n in self.visible_notes() if not n.rest]
        if notes:
            lo = min(n.midi for n in notes) - 3
            hi = max(n.midi for n in notes) + 3
        else:
            lo, hi = 55, 79
        if hi - lo < 24:
            mid = (lo + hi) // 2
            lo, hi = mid - 12, mid + 12
        self.low, self.high = max(0, lo), min(127, hi)
        # Altura mínima por tecla para que las notas se puedan pulsar; si no cabe, el
        # contenedor muestra una barra de desplazamiento vertical.
        self.setMinimumHeight(int(HEADER_H + REST_LANE_H * 2 + 8 + (self.high - self.low + 1) * MIN_ROW_H))

    # ------------------------------------------------------------------ geometría
    def _area(self) -> QRectF:
        return QRectF(KEYS_W, HEADER_H, max(10, self.width() - KEYS_W - 4), max(10, self.height() - HEADER_H - REST_LANE_H * 2 - 4))

    def _q_range(self) -> tuple[Fraction, Fraction]:
        if not self._starts:
            return Fraction(0), Fraction(4)
        return self._starts[self.m_from], self._starts[self.m_to + 1]

    def _x(self, q: Fraction | float) -> float:
        a = self._area()
        q0, q1 = self._q_range()
        span = float(q1 - q0) or 1.0
        return a.left() + (float(q) - float(q0)) / span * a.width()

    def _q_at(self, x: float) -> Fraction:
        a = self._area()
        q0, q1 = self._q_range()
        span = (q1 - q0) or Fraction(1)
        frac_x = Fraction((x - a.left()) / max(1.0, a.width())).limit_denominator(960)
        return q0 + frac_x * span

    def _row_h(self) -> float:
        return self._area().height() / max(1, self.high - self.low + 1)

    def _y(self, midi: int) -> float:
        a = self._area()
        return a.top() + (self.high - midi) * self._row_h()

    def _midi_at(self, y: float) -> int:
        a = self._area()
        return max(0, min(127, self.high - int((y - a.top()) // self._row_h())))

    def _note_rect(self, n: Note) -> QRectF:
        q = self._starts[n.measure] + n.onset
        dur = n.duration if n.duration > 0 else Fraction(1, 8)
        x0, x1 = self._x(q), self._x(q + dur)
        if n.rest:
            lane = self.height() - REST_LANE_H * (2 if n.staff <= 1 else 1) - 2
            return QRectF(x0 + 1, lane + 2, max(3.0, x1 - x0 - 2), REST_LANE_H - 4)
        y = self._y(n.midi)
        return QRectF(x0 + 1, y + 1, max(4.0, x1 - x0 - 2), max(3.0, self._row_h() - 2))

    def _note_at(self, pos: QPointF) -> Note | None:
        for n in reversed(self.visible_notes()):
            if self._note_rect(n).contains(pos):
                return n
        return None

    def _measure_at(self, q: Fraction) -> tuple[int, Fraction]:
        for i in range(self.m_from, self.m_to + 1):
            if self._starts[i] <= q < self._starts[i + 1]:
                return i, q - self._starts[i]
        return self.m_to, q - self._starts[self.m_to]

    def _snap(self, q: Fraction) -> Fraction:
        return (q / self.grid).__round__() * self.grid

    # ------------------------------------------------------------------ dibujo
    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        pal = theme.current()
        p.fillRect(self.rect(), QColor(pal.surface))
        if self.score is None or not self.score.measures:
            p.setPen(QColor(pal.muted))
            p.drawText(self.rect(), Qt.AlignCenter, "Sin partitura cargada")
            return
        a = self._area()
        rh = self._row_h()
        # filas (teclas)
        for m in range(self.low, self.high + 1):
            y = self._y(m)
            if is_black_key(m):
                p.fillRect(QRectF(a.left(), y, a.width(), rh), QColor(pal.surface2))
            if m % 12 == 0:
                p.setPen(QPen(QColor(pal.border), 1))
                p.drawLine(QPointF(a.left(), y + rh), QPointF(a.right(), y + rh))
        # teclado lateral
        f = QFont(self.font())
        f.setPixelSize(max(8, min(11, int(rh * 0.9))))
        p.setFont(f)
        for m in range(self.low, self.high + 1):
            y = self._y(m)
            r = QRectF(2, y, KEYS_W - 6, rh)
            black = is_black_key(m)
            p.fillRect(r, QColor("#1a1c22") if black else QColor("#eceef3"))
            if m % 12 == 0 or (rh >= 12 and not black):
                p.setPen(QColor("#e6e6e6") if black else QColor("#3a3f4b"))
                p.drawText(r.adjusted(2, 0, -2, 0), Qt.AlignVCenter | Qt.AlignRight, midi_name(m))
        # compases y tiempos
        for mi in range(self.m_from, self.m_to + 1):
            x = self._x(self._starts[mi])
            beat = Fraction(1)
            ts = self.score.measures[mi].time_sig
            if ts and ts[1] == 8 and ts[0] % 3 == 0:
                beat = Fraction(3, 2)
            q = self._starts[mi] + beat
            while q < self._starts[mi + 1]:
                xb = self._x(q)
                p.setPen(QPen(QColor(pal.border), 1, Qt.DotLine))
                p.drawLine(QPointF(xb, a.top()), QPointF(xb, a.bottom()))
                q += beat
            st = self.status.get(f"m{mi}")
            p.setPen(QPen(QColor(pal.muted), 1.5))
            p.drawLine(QPointF(x, HEADER_H - 4), QPointF(x, self.height() - 2))
            p.setPen(QColor(pal.text))
            header = QRectF(x + 4, 2, self._x(self._starts[mi + 1]) - x - 8, HEADER_H - 4)
            label = f"Compás {self.score.measures[mi].number}"
            if st:
                p.fillRect(QRectF(x + 1, 2, 4, HEADER_H - 6), QColor(theme.status_color(st)))
            p.drawText(header.adjusted(6, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
        # carriles de silencios
        p.setPen(QColor(pal.muted))
        f2 = QFont(self.font())
        f2.setPixelSize(10)
        p.setFont(f2)
        for staff, label in ((1, "silencios ↑"), (2, "silencios ↓")):
            lane = self.height() - REST_LANE_H * (2 if staff == 1 else 1) - 2
            p.drawText(QRectF(2, lane, KEYS_W - 6, REST_LANE_H), Qt.AlignVCenter | Qt.AlignRight, label)
        # notas
        for n in self.visible_notes():
            r = self._note_rect(n)
            if self._drag and self._drag.kind == "move" and n.id in self._drag.note_ids:
                r = r.translated(self._x(self._starts[0] + self._drag.dx_q) - self._x(self._starts[0]),
                                 -self._drag.dmidi * rh)
            if self._drag and self._drag.kind == "resize" and n.id in self._drag.note_ids and self._drag.new_duration:
                r.setRight(self._x(self._starts[n.measure] + n.onset + self._drag.new_duration) - 1)
            if n.rest:
                p.fillRect(r, QColor(pal.surface3))
                if n.id in self.selected:
                    p.setPen(QPen(QColor(pal.text), 1.5))
                    p.drawRect(r)
                continue
            color = QColor(theme.hand_color(n.hand, n.part))
            if n.grace:
                color.setAlpha(170)
            if n.id in self.preview_remove:
                # antes de la corrección: atenuada y tachada
                faded = QColor(color)
                faded.setAlpha(70)
                p.setBrush(faded)
                p.setPen(QPen(QColor(theme.status_color("error")), 1.6, Qt.DashLine))
                p.drawRoundedRect(r, 3, 3)
                p.drawLine(r.topLeft(), r.bottomRight())
                continue
            p.setBrush(color)
            st = self.status.get(n.id, "sin_datos")
            if n.id in self.selected:
                pen = QPen(QColor("#ffffff"), 2.2)
            elif st in ("error", "revisar"):
                pen = QPen(QColor(theme.status_color(st)), 2.2)
            else:
                pen = QPen(color.darker(170), 1)
            p.setPen(pen)
            p.drawRoundedRect(r, 3, 3)
            if r.width() > 34 and rh >= 11:
                p.setPen(QColor(12, 14, 18))
                p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, n.name())
        # después de la corrección: contorno naranja
        if self.preview_add:
            accent = QColor(theme.current().warning)
            fill = QColor(accent)
            fill.setAlpha(60)
            for n in self.preview_add:
                if not (self.m_from <= n.measure <= self.m_to):
                    continue
                r = self._note_rect(n)
                if n.rest:
                    p.setBrush(fill)
                    p.setPen(QPen(accent, 1.6, Qt.DashLine))
                    p.drawRect(r)
                    continue
                p.setBrush(fill)
                p.setPen(QPen(accent, 2.4))
                p.drawRoundedRect(r, 3, 3)
                if r.width() > 34 and rh >= 11:
                    p.setPen(accent.lighter(130))
                    p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, n.name())
        # cabezal de reproducción
        if self.playhead_q is not None:
            q0, q1 = self._q_range()
            if float(q0) <= self.playhead_q <= float(q1):
                x = self._x(self.playhead_q)
                p.setPen(QPen(QColor(pal.accent), 2))
                p.drawLine(QPointF(x, HEADER_H), QPointF(x, self.height()))
        if self._drag and self._drag.kind == "band" and self._drag.current is not None:
            p.setPen(QPen(QColor(pal.accent), 1, Qt.DashLine))
            p.setBrush(QColor(124, 108, 255, 40))
            p.drawRect(QRectF(self._drag.start, self._drag.current).normalized())
        p.end()

    # ------------------------------------------------------------------ ratón
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if self.score is None:
            return
        pos = ev.position()
        if pos.y() < HEADER_H:
            mi, _ = self._measure_at(self._q_at(pos.x()))
            self.measure_clicked.emit(mi)
            return
        n = self._note_at(pos)
        if ev.button() == Qt.RightButton:
            if n is not None and n.id not in self.selected:
                self.selected = {n.id}
                self.selection_changed.emit(list(self.selected))
            return
        if n is None:
            if not (ev.modifiers() & Qt.ControlModifier):
                self.selected.clear()
            self._drag = _Drag("band", pos, [])
            self.selection_changed.emit(list(self.selected))
            self.update()
            return
        if ev.modifiers() & Qt.ControlModifier:
            self.selected.symmetric_difference_update({n.id})
        elif n.id not in self.selected:
            self.selected = {n.id}
        self.selection_changed.emit(list(self.selected))
        r = self._note_rect(n)
        kind = "resize" if (r.right() - pos.x() < 7 and not n.rest) else "move"
        self._drag = _Drag(kind, pos, list(self.selected), n)
        self.update()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        pos = ev.position()
        if self._drag is None:
            n = self._note_at(pos)
            if n is not None:
                r = self._note_rect(n)
                self.setCursor(Qt.SizeHorCursor if (r.right() - pos.x() < 7 and not n.rest) else Qt.OpenHandCursor)
                QToolTip.showText(ev.globalPosition().toPoint(), self._tooltip(n), self)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        d = self._drag
        d.current = pos
        if d.kind == "move":
            dq = self._q_at(pos.x()) - self._q_at(d.start.x())
            d.dx_q = self._snap(dq)
            d.dmidi = int(round((d.start.y() - pos.y()) / self._row_h()))
            if d.origin_note is not None and d.origin_note.rest:
                d.dmidi = 0
        elif d.kind == "resize" and d.origin_note is not None:
            n = d.origin_note
            end_q = self._snap(self._q_at(pos.x()) - self._starts[n.measure])
            d.new_duration = max(self.grid, end_q - n.onset)
        self.update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d.kind == "band" and d.current is not None:
            band = QRectF(d.start, d.current).normalized()
            if band.width() > 4 and band.height() > 4:
                for n in self.visible_notes():
                    if band.intersects(self._note_rect(n)):
                        self.selected.add(n.id)
                self.selection_changed.emit(list(self.selected))
        elif d.kind == "move" and (d.dx_q != 0 or d.dmidi != 0):
            self.edit_move.emit(d.note_ids, d.dmidi, d.dx_q)
        elif d.kind == "resize" and d.new_duration and d.origin_note and d.new_duration != d.origin_note.duration:
            self.edit_duration.emit(d.note_ids, d.new_duration)
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        if self.score is None:
            return
        pos = ev.position()
        if self._note_at(pos) is not None or pos.y() < HEADER_H or pos.x() < KEYS_W:
            return
        q = self._snap(self._q_at(pos.x()))
        mi, onset = self._measure_at(q)
        midi = self._midi_at(pos.y())
        self.edit_add.emit(mi, onset, midi, self.default_duration)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        ids = list(self.selected)
        key = ev.key()
        if not ids:
            return super().keyPressEvent(ev)
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.edit_delete.emit(ids)
        elif key == Qt.Key_Up:
            self.edit_move.emit(ids, 12 if ev.modifiers() & Qt.ShiftModifier else 1, Fraction(0))
        elif key == Qt.Key_Down:
            self.edit_move.emit(ids, -12 if ev.modifiers() & Qt.ShiftModifier else -1, Fraction(0))
        elif key == Qt.Key_Left:
            self.edit_move.emit(ids, 0, -self.grid)
        elif key == Qt.Key_Right:
            self.edit_move.emit(ids, 0, self.grid)
        elif key == Qt.Key_H:
            self.edit_hand_toggle.emit(ids)
        else:
            super().keyPressEvent(ev)

    def _tooltip(self, n: Note) -> str:
        if n.rest:
            return f"Silencio · {describe_duration(n.duration, n.tuplet)} · compás {self.score.measures[n.measure].number}"
        hand = {"R": "mano derecha", "L": "mano izquierda"}.get(n.hand or "", "")
        conf = f" · confianza del motor {n.confidence:.2f}" if n.confidence is not None else ""
        return (f"{n.name()} ({n.name('en')}, MIDI {n.midi}) · {describe_duration(n.duration, n.tuplet)} · "
                f"compás {self.score.measures[n.measure].number}{' · ' + hand if hand else ''}{conf}")
