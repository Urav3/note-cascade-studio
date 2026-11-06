"""Componentes visuales reutilizables."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from note_cascade.ui import theme


def icon_label(name: str, size: int = 16, color: str | None = None) -> QLabel:
    lbl = QLabel(theme.icon_text(name))
    lbl.setFont(theme.icon_font(size))
    if color:
        lbl.setStyleSheet(f"color: {color};")
    return lbl


class IconButton(QToolButton):
    def __init__(self, icon: str, tooltip: str = "", text: str = "", size: int = 16, checkable: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_name = icon
        self.setToolTip(tooltip)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        glyph = theme.icon_text(icon)
        if text:
            self.setText(f"{glyph}  {text}" if glyph and theme.ICON_FONT_FAMILY else text)
            self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        else:
            self.setText(glyph or tooltip[:2])
            self.setFont(theme.icon_font(size))
            self.setFixedSize(QSize(size + 18, size + 14))

    def set_icon(self, icon: str) -> None:
        self._icon_name = icon
        self.setText(theme.icon_text(icon))


def text_button(text: str, icon: str = "", primary: bool = False, tooltip: str = "") -> QPushButton:
    glyph = theme.icon_text(icon) if icon and theme.ICON_FONT_FAMILY else ""
    btn = QPushButton(text)
    if glyph:
        btn = IconTextButton(glyph, text)
    if primary:
        btn.setObjectName("Primary")
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


class IconTextButton(QPushButton):
    """Botón con glifo de icono (fuente de iconos de Windows) y texto."""

    def __init__(self, glyph: str, text: str) -> None:
        super().__init__()
        self._glyph = glyph
        self._text = text
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 14, 0)
        lay.setSpacing(8)
        self._icon = QLabel(glyph)
        self._icon.setFont(theme.icon_font(14))
        self._icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._label = QLabel(text)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self._icon)
        lay.addWidget(self._label)
        lay.addStretch(0)
        self.setMinimumHeight(36)
        self._fit()

    def _fit(self) -> None:
        f = QFont(self._label.font())
        f.setBold(True)
        from PySide6.QtGui import QFontMetrics
        width = QFontMetrics(f).horizontalAdvance(self._text)
        self.setMinimumWidth(width + 64)

    def setText(self, text: str) -> None:  # noqa: N802 - API de Qt
        self._text = text
        if hasattr(self, "_label"):
            self._label.setText(text)
            self._fit()

    def text(self) -> str:  # noqa: D401
        return self._text

    def setObjectName(self, name: str) -> None:  # noqa: N802
        super().setObjectName(name)
        if name == "Primary" and hasattr(self, "_label"):
            p = theme.current()
            self._label.setStyleSheet(f"color: {p.accent_text}; font-weight: 600;")
            self._icon.setStyleSheet(f"color: {p.accent_text};")


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, flat: bool = False, padding: int = 16) -> None:
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(padding, padding, padding, padding)
        self.layout_.setSpacing(10)

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.layout_.addWidget(w, stretch)
        return w


def title_label(text: str, kind: str = "PageTitle") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(kind)
    lbl.setWordWrap(True)
    return lbl


def muted_label(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setWordWrap(wrap)
    return lbl


class Pill(QLabel):
    def __init__(self, text: str = "", color: str | None = None) -> None:
        super().__init__(text)
        self.setObjectName("Pill")
        self.set_color(color or theme.current().neutral)

    def set_color(self, color: str) -> None:
        c = QColor(color)
        bg = QColor(c)
        bg.setAlpha(46)
        self.setStyleSheet(f"#Pill {{ color: {c.name()}; background: rgba({bg.red()},{bg.green()},{bg.blue()},46);"
                           f" border-radius: 10px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}")


class StatTile(QFrame):
    def __init__(self, label: str, value: str = "—", color: str | None = None) -> None:
        super().__init__()
        self.setObjectName("CardFlat")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.value = QLabel(value)
        self.value.setObjectName("BigNumber")
        if color:
            self.value.setStyleSheet(f"color: {color};")
        self.label = muted_label(label)
        lay.addWidget(self.value)
        lay.addWidget(self.label)

    def set(self, value: str, color: str | None = None) -> None:
        self.value.setText(value)
        if color:
            self.value.setStyleSheet(f"color: {color};")


class Dot(QWidget):
    def __init__(self, color: str, size: int = 10) -> None:
        super().__init__()
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self._color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


def numpy_to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    if arr.ndim == 2:
        h, w = arr.shape
        img = QImage(arr.data, w, h, arr.strides[0], QImage.Format_Grayscale8)
    else:
        h, w, c = arr.shape
        fmt = QImage.Format_RGB888 if c == 3 else QImage.Format_RGBA8888
        img = QImage(arr.data, w, h, arr.strides[0], fmt)
    return img.copy()


def numpy_to_pixmap(arr: np.ndarray) -> QPixmap:
    return QPixmap.fromImage(numpy_to_qimage(arr))


class HLine(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.HLine)
        self.setStyleSheet(f"color: {theme.current().border};")


def hbox(*widgets, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0), stretch_last: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif hasattr(w, "addWidget") and not isinstance(w, QWidget):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


def vbox(*widgets, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif hasattr(w, "addWidget") and not isinstance(w, QWidget):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"
