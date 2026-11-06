"""Tema visual (oscuro/claro), colores y tipografía."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface2: str
    surface3: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    accent2: str
    danger: str
    warning: str
    success: str
    info: str
    neutral: str
    right_hand: str
    left_hand: str
    other: str
    paper: str


DARK = Palette("oscuro", bg="#121419", surface="#1a1d24", surface2="#22262f", surface3="#2b303b", border="#323846",
               text="#e9ebf1", muted="#9aa1b2", accent="#7c6cff", accent_text="#ffffff", accent2="#21c7a8",
               danger="#ef5350", warning="#f5a524", success="#34c77b", info="#5b9dff", neutral="#8b93a7",
               right_hand="#3fd68a", left_hand="#4f8ff7", other="#f59e0b", paper="#ffffff")
LIGHT = Palette("claro", bg="#f3f4f8", surface="#ffffff", surface2="#f0f1f6", surface3="#e5e7ef", border="#d8dbe5",
                text="#1b1e27", muted="#5e6475", accent="#5b4bff", accent_text="#ffffff", accent2="#0e9f86",
                danger="#d32f2f", warning="#c27c00", success="#1f9d57", info="#2f6fe0", neutral="#7b8194",
                right_hand="#16a862", left_hand="#2f6fe0", other="#d97706", paper="#ffffff")

_current = DARK
ICON_FONT_FAMILY = ""

# Glifos de Segoe Fluent Icons / Segoe MDL2 Assets (incluidas en Windows 10/11)
ICONS = {
    "open": "", "folder": "", "paste": "", "play": "", "pause": "", "stop": "",
    "prev": "", "next": "", "start": "", "loop": "", "volume": "", "mute": "",
    "save": "", "settings": "", "zoom_in": "", "zoom_out": "", "fit": "",
    "undo": "", "redo": "", "delete": "", "add": "", "warning": "", "error": "",
    "info": "", "check": "", "music": "", "audio": "", "export": "", "video": "",
    "page": "", "document": "", "refresh": "", "cancel": "", "help": "",
    "up": "", "down": "", "left": "", "right": "", "eye": "", "health": "",
    "import": "", "analyze": "", "review": "", "listen": "", "batch": "",
    "back": "", "forward": "", "hand": "", "clean": "", "link": "",
}


def current() -> Palette:
    return _current


def icon_font(size: int = 14) -> QFont:
    f = QFont(ICON_FONT_FAMILY or "Segoe MDL2 Assets")
    f.setPixelSize(size)
    return f


def icon_text(name: str) -> str:
    return ICONS.get(name, "")


def status_color(status: str) -> str:
    p = _current
    return {"ok": p.success, "revisar": p.warning, "error": p.danger, "sin_datos": p.neutral}.get(status, p.neutral)


def hand_color(hand: str | None, part: int = 0) -> str:
    p = _current
    if hand == "R":
        return p.right_hand
    if hand == "L":
        return p.left_hand
    others = [p.other, "#ec4899", "#22d3ee", "#a3e635", "#f472b6", "#facc15"]
    return others[part % len(others)]


def severity_color(sev: str) -> str:
    p = _current
    return {"error": p.danger, "aviso": p.warning, "info": p.info}.get(sev, p.neutral)


def apply_theme(app: QApplication, name: str = "oscuro") -> Palette:
    global _current, ICON_FONT_FAMILY
    _current = LIGHT if name == "claro" else DARK
    families = set(QFontDatabase.families())
    ICON_FONT_FAMILY = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else (
        "Segoe MDL2 Assets" if "Segoe MDL2 Assets" in families else "")
    base_font = QFont("Segoe UI Variable Text" if "Segoe UI Variable Text" in families else "Segoe UI")
    base_font.setPointSizeF(10)
    app.setFont(base_font)
    p = _current
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(p.bg))
    pal.setColor(QPalette.WindowText, QColor(p.text))
    pal.setColor(QPalette.Base, QColor(p.surface))
    pal.setColor(QPalette.AlternateBase, QColor(p.surface2))
    pal.setColor(QPalette.Text, QColor(p.text))
    pal.setColor(QPalette.Button, QColor(p.surface2))
    pal.setColor(QPalette.ButtonText, QColor(p.text))
    pal.setColor(QPalette.Highlight, QColor(p.accent))
    pal.setColor(QPalette.HighlightedText, QColor(p.accent_text))
    pal.setColor(QPalette.ToolTipBase, QColor(p.surface3))
    pal.setColor(QPalette.ToolTipText, QColor(p.text))
    pal.setColor(QPalette.PlaceholderText, QColor(p.muted))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(p))
    return p


def stylesheet(p: Palette) -> str:
    return f"""
    QWidget {{ color: {p.text}; }}
    QMainWindow, QDialog {{ background: {p.bg}; }}
    QToolTip {{ background: {p.surface3}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; border-radius: 6px; }}
    #Sidebar {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
    #AppTitle {{ font-size: 16px; font-weight: 700; }}
    #AppSubtitle {{ color: {p.muted}; font-size: 11px; }}
    QPushButton#StepButton {{ text-align: left; padding: 10px 12px; border: none; border-radius: 10px;
        background: transparent; color: {p.muted}; font-size: 13px; }}
    QPushButton#StepButton:hover {{ background: {p.surface2}; color: {p.text}; }}
    QPushButton#StepButton:checked {{ background: {p.surface3}; color: {p.text}; font-weight: 600; }}
    QPushButton#StepButton:disabled {{ color: {p.border}; }}
    #Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px; }}
    #CardFlat {{ background: {p.surface2}; border-radius: 12px; }}
    #PageTitle {{ font-size: 22px; font-weight: 700; }}
    #PageSubtitle {{ color: {p.muted}; font-size: 12px; }}
    #SectionTitle {{ font-size: 14px; font-weight: 600; }}
    #Muted {{ color: {p.muted}; }}
    #BigNumber {{ font-size: 26px; font-weight: 700; }}
    QPushButton {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 9px; padding: 7px 14px; }}
    QPushButton:hover {{ background: {p.surface3}; }}
    QPushButton:pressed {{ background: {p.border}; }}
    QPushButton:disabled {{ color: {p.muted}; background: {p.surface}; border-color: {p.surface3}; }}
    QPushButton:checked {{ background: {p.surface3}; border-color: {p.accent}; }}
    QPushButton#Primary {{ background: {p.accent}; color: {p.accent_text}; border: none; font-weight: 600; padding: 10px 20px; }}
    QPushButton#Primary:hover {{ background: {QColor(p.accent).lighter(112).name()}; }}
    QPushButton#Primary:disabled {{ background: {p.surface3}; color: {p.muted}; }}
    QPushButton#Danger {{ color: {p.danger}; }}
    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
    QToolButton:hover {{ background: {p.surface3}; }}
    QToolButton:checked {{ background: {p.surface3}; border-color: {p.accent}; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{ background: {p.surface2};
        border: 1px solid {p.border}; border-radius: 8px; padding: 5px 8px; selection-background-color: {p.accent}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {p.surface2}; border: 1px solid {p.border}; selection-background-color: {p.accent}; }}
    QListWidget, QTreeWidget, QTableWidget {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px;
        padding: 4px; outline: none; }}
    QListWidget::item, QTreeWidget::item {{ padding: 6px; border-radius: 6px; }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {p.surface3}; color: {p.text}; }}
    QHeaderView::section {{ background: {p.surface2}; border: none; padding: 6px; color: {p.muted}; }}
    QProgressBar {{ background: {p.surface2}; border: none; border-radius: 6px; height: 12px; text-align: center; color: transparent; }}
    QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {p.accent}, stop:1 {p.accent2}); border-radius: 6px; }}
    QSlider::groove:horizontal {{ height: 6px; background: {p.surface3}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: {p.text}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {p.border}; background: {p.surface2}; }}
    QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
    QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 10px; top: -1px; background: {p.surface}; }}
    QTabBar::tab {{ background: transparent; padding: 8px 14px; color: {p.muted}; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.surface3}; border-radius: 4px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {p.surface3}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QSplitter::handle {{ background: {p.border}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}
    QStatusBar {{ background: {p.surface}; border-top: 1px solid {p.border}; color: {p.muted}; }}
    QGroupBox {{ border: 1px solid {p.border}; border-radius: 10px; margin-top: 14px; padding: 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p.muted}; }}
    QMenu {{ background: {p.surface2}; border: 1px solid {p.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p.surface3}; }}
    #DropZone {{ border: 2px dashed {p.border}; border-radius: 16px; background: {p.surface}; }}
    #DropZone[active="true"] {{ border-color: {p.accent}; background: {p.surface2}; }}
    #Pill {{ border-radius: 10px; padding: 2px 8px; font-size: 11px; }}
    """
