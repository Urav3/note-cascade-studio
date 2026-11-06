"""Visualizador de notas en cascada (estilo «Synthesia»).

``WaterfallRenderer`` dibuja con QPainter en cualquier superficie (widget o
imagen para el vídeo). ``WaterfallWidget`` lo anima con el reloj del
reproductor sin bloquear la interfaz.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from note_cascade.music_model.pitch import midi_name
from note_cascade.music_model.timeline import Performance, PerfNote
from note_cascade.piano_roll.keyboard import auto_range, key_layout
from note_cascade.player.synth import stem_key
from note_cascade.ui import theme


@dataclass
class WaterfallOptions:
    lookahead: float = 3.0
    low: int = 21
    high: int = 108
    auto: bool = True
    visible: set[str] = field(default_factory=lambda: {"R", "L", "otros", "acordes"})
    show_names: bool = True
    show_measures: bool = True


class WaterfallRenderer:
    def __init__(self) -> None:
        self.options = WaterfallOptions()
        self.notes: list[PerfNote] = []
        self.starts: list[float] = []
        self.max_dur = 0.0
        self.measure_starts: list[tuple[float, str]] = []
        self.loop: tuple[float, float] | None = None
        self.title = ""
        self.highlight_ids: set[str] = set()
        # Estado de revisión por nota (revisar / error): se marca con un borde en la cascada
        self.status: dict[str, str] = {}
        self.show_status = True

    def set_performance(self, perf: Performance | None, measure_numbers: list[str] | None = None) -> None:
        if perf is None:
            self.notes, self.starts, self.measure_starts = [], [], []
            return
        self.notes = sorted(perf.notes, key=lambda n: n.start_s)
        self.starts = [n.start_s for n in self.notes]
        self.max_dur = max((n.end_s - n.start_s for n in self.notes), default=0.0)
        self.measure_starts = []
        for pm in perf.play_measures:
            label = measure_numbers[pm.measure] if measure_numbers and pm.measure < len(measure_numbers) else str(pm.measure + 1)
            self.measure_starts.append((perf.q_to_s(pm.start_q), label))
        if self.options.auto:
            self.options.low, self.options.high = auto_range([n.midi for n in self.notes])

    def current_measure(self, now: float) -> str:
        if not self.measure_starts:
            return ""
        times = [t for t, _ in self.measure_starts]
        i = max(0, bisect.bisect_right(times, now + 1e-6) - 1)
        return self.measure_starts[i][1]

    # ------------------------------------------------------------------
    def paint(self, p: QPainter, rect: QRectF, now: float, draw_header: bool = True) -> None:
        pal = theme.current()
        p.setRenderHint(QPainter.Antialiasing, True)
        bg = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        bg.setColorAt(0.0, QColor("#0b0d12"))
        bg.setColorAt(1.0, QColor("#161a24"))
        p.fillRect(rect, bg)
        opt = self.options
        kb_h = max(56.0, min(rect.height() * 0.17, rect.width() / 7))
        kb_top = rect.bottom() - kb_h
        fall = QRectF(rect.left(), rect.top(), rect.width(), kb_top - rect.top())
        layout = key_layout(opt.low, opt.high, rect.left(), rect.width())
        if not layout:
            return
        pps = fall.height() / max(0.5, opt.lookahead)

        # Guías de octava
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for m, k in layout.items():
            if m % 12 == 0:
                p.drawLine(QPointF(k.x, fall.top()), QPointF(k.x, fall.bottom()))

        # Bucle
        if self.loop:
            a, b = self.loop
            ya = kb_top - (a - now) * pps
            yb = kb_top - (b - now) * pps
            band = QRectF(fall.left(), max(fall.top(), yb), fall.width(), min(fall.bottom(), ya) - max(fall.top(), yb))
            if band.height() > 0:
                p.fillRect(band, QColor(124, 108, 255, 26))

        # Líneas de compás
        if opt.show_measures and self.measure_starts:
            small = QFont(p.font())
            small.setPixelSize(11)
            p.setFont(small)
            times = [t for t, _ in self.measure_starts]
            i0 = max(0, bisect.bisect_left(times, now) - 1)
            for t, label in self.measure_starts[i0:]:
                if t > now + opt.lookahead:
                    break
                y = kb_top - (t - now) * pps
                if y < fall.top() or y > fall.bottom():
                    continue
                p.setPen(QPen(QColor(255, 255, 255, 34), 1))
                p.drawLine(QPointF(fall.left(), y), QPointF(fall.right(), y))
                p.setPen(QColor(255, 255, 255, 90))
                p.drawText(QPointF(fall.left() + 6, y - 4), label)

        # Notas que caen
        active: dict[int, list[str]] = {}
        i_end = bisect.bisect_right(self.starts, now + opt.lookahead)
        i_start = bisect.bisect_left(self.starts, now - self.max_dur - 0.01)
        # Notas de las dos manos en la misma tecla y al mismo tiempo: cada mano ocupa media tecla
        # (izquierda a la izquierda), para que no se tapen ni parezcan una sola.
        window = [n for n in self.notes[i_start:i_end] if n.end_s >= now]
        by_key: dict[int, list] = {}
        for n in window:
            by_key.setdefault(n.midi, []).append(n)
        shared: set[int] = set()
        for group in by_key.values():
            if len({x.hand for x in group}) < 2:
                continue
            for a in group:
                if any(b.hand != a.hand and a.start_s < b.end_s and b.start_s < a.end_s for b in group):
                    shared.add(id(a))
        name_font = QFont(p.font())
        name_font.setPixelSize(10)
        name_font.setBold(True)
        for n in self.notes[i_start:i_end]:
            if n.end_s < now:
                continue
            key = stem_key(n.hand, n.part, n.track)
            if key not in opt.visible:
                continue
            k = layout.get(n.midi)
            if k is None:
                continue
            color = QColor(theme.hand_color(n.hand, n.part))
            y_bottom = kb_top - (n.start_s - now) * pps
            y_top = kb_top - (n.end_s - now) * pps
            top = max(fall.top() - 4, y_top)
            bottom = min(kb_top, y_bottom)
            if bottom <= fall.top() or top >= kb_top:
                continue
            is_active = n.start_s <= now < n.end_s
            if is_active:
                cols = active.setdefault(n.midi, [])
                if color.name() not in cols:
                    if n.hand == "L":
                        cols.insert(0, color.name())  # izquierda en la mitad izquierda de la tecla
                    else:
                        cols.append(color.name())
                color = color.lighter(135)
            inset = 1.5 if not k.black else 1.0
            x, w = k.x + inset, max(2.0, k.w - 2 * inset)
            if id(n) in shared:
                w = max(2.0, w / 2)
                if n.hand != "L":
                    x += w
            r = QRectF(x, top, w, max(3.0, bottom - top - 1))
            path = QPainterPath()
            path.addRoundedRect(r, 4, 4)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0, color.lighter(118))
            grad.setColorAt(1, color.darker(112))
            p.fillPath(path, grad)
            flagged = None
            if self.show_status and self.status:
                states = {self.status.get(i) for i in n.note_ids}
                flagged = "error" if "error" in states else ("revisar" if "revisar" in states else None)
            if any(i in self.highlight_ids for i in n.note_ids):
                p.setPen(QPen(QColor("#ffffff"), 2))
            elif flagged:
                p.setPen(QPen(QColor(theme.status_color(flagged)), 2.5))
            else:
                p.setPen(QPen(color.darker(160), 1))
            p.drawPath(path)
            if opt.show_names and r.height() > 16 and r.width() > 14:
                p.setFont(name_font)
                p.setPen(QColor(10, 12, 16, 220))
                label = midi_name(n.midi).rstrip("0123456789-")
                p.drawText(QRectF(r.left(), r.bottom() - 16, r.width(), 14), Qt.AlignCenter, label)

        # Línea de impacto
        glow = QLinearGradient(QPointF(0, kb_top - 10), QPointF(0, kb_top))
        glow.setColorAt(0, QColor(124, 108, 255, 0))
        glow.setColorAt(1, QColor(124, 108, 255, 90))
        p.fillRect(QRectF(rect.left(), kb_top - 10, rect.width(), 10), glow)

        # Teclado
        p.setPen(QPen(QColor("#23262f"), 1))
        for m, k in layout.items():
            if k.black:
                continue
            r = QRectF(k.x, kb_top, k.w, kb_h)
            fill = QColor(active[m][0]) if m in active else QColor("#f4f4f6")
            p.setBrush(fill)
            p.drawRoundedRect(r.adjusted(0.5, 0, -0.5, 0), 3, 3)
            if m in active and len(active[m]) > 1:  # las dos manos en la misma tecla
                p.fillRect(QRectF(k.x + k.w / 2, kb_top + 1, k.w / 2 - 1, kb_h - 2), QColor(active[m][1]))
            if m % 12 == 0 and k.w > 14:
                p.setPen(QColor("#8a8f9c"))
                f = QFont(p.font())
                f.setPixelSize(9)
                p.setFont(f)
                p.drawText(QRectF(k.x - 4, kb_top + kb_h - 14, k.w + 8, 12), Qt.AlignCenter, f"Do{m // 12 - 1}")
                p.setPen(QPen(QColor("#23262f"), 1))
        for m, k in layout.items():
            if not k.black:
                continue
            r = QRectF(k.x, kb_top, k.w, kb_h * 0.62)
            fill = QColor(active[m][0]).darker(110) if m in active else QColor("#1b1d23")
            p.setBrush(fill)
            p.setPen(QPen(QColor("#0d0e12"), 1))
            p.drawRoundedRect(r, 2, 2)
            if m in active and len(active[m]) > 1:
                p.fillRect(QRectF(k.x + k.w / 2, kb_top + 1, k.w / 2 - 1, kb_h * 0.62 - 2),
                           QColor(active[m][1]).darker(110))
        p.setBrush(Qt.NoBrush)

        if draw_header:
            f = QFont(p.font())
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 210))
            measure = self.current_measure(now)
            text = f"Compás {measure}" if measure else ""
            if self.title:
                text = f"{self.title}   ·   {text}" if text else self.title
            p.drawText(QRectF(rect.left() + 12, rect.top() + 8, rect.width() - 24, 20), Qt.AlignLeft, text)


class WaterfallWidget(QWidget):
    seek_requested = Signal(float)

    def __init__(self, player=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.renderer = WaterfallRenderer()
        self.player = player
        self._now = 0.0
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._last_drawn = None

    def set_performance(self, perf: Performance | None, measure_numbers: list[str] | None = None) -> None:
        self.renderer.set_performance(perf, measure_numbers)
        self.update()

    def set_time(self, now: float) -> None:
        self._now = now
        self.update()

    def _tick(self) -> None:
        if self.player is None:
            return
        now = self.player.position()
        if self.player.is_playing or abs(now - self._now) > 1e-3:
            self._now = now
            self.update()

    def wheelEvent(self, ev) -> None:  # noqa: N802
        # Rueda: desplazarse en el tiempo; Ctrl+rueda: cambiar la ventana visible
        steps = ev.angleDelta().y() / 120
        if ev.modifiers() & Qt.ControlModifier:
            self.renderer.options.lookahead = max(1.0, min(12.0, self.renderer.options.lookahead * (0.85 ** steps)))
            self.update()
        else:
            self.seek_requested.emit(max(0.0, self._now + steps * 0.5))

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        self.renderer.paint(p, QRectF(self.rect()), self._now)
        p.end()
