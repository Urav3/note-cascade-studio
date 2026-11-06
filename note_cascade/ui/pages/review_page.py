"""Paso 3 — Revisar: imagen original, avisos, propiedades de la nota y editor."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from note_cascade.layout.text_classifier import CATEGORY_LABELS
from note_cascade.music_model import edits
from note_cascade.music_model import pitch as P
from note_cascade.music_model.hands import LEFT, RIGHT
from note_cascade.music_model.model import TYPE_NAME_ES, describe_duration, dotted, TYPE_QUARTERS
from note_cascade.music_validation import learning
from note_cascade.music_validation.fixes import set_clef, set_key
from note_cascade.music_validation.issues import CATEGORY_LABEL, EVIDENCE_LABEL, SEVERITY_LABEL
from note_cascade.piano_roll.editor import PianoRollEditor
from note_cascade.ui import theme
from note_cascade.ui.widgets.common import IconButton, Pill, hbox, muted_label, text_button, title_label
from note_cascade.ui.widgets.page_view import CATEGORY_COLORS, PageView

DURATIONS = [
    ("Redonda con puntillo (6)", Fraction(6)),
    ("Redonda (4 tiempos)", Fraction(4)), ("Blanca con puntillo (3)", Fraction(3)), ("Blanca (2)", Fraction(2)),
    ("Negra con puntillo (1½)", Fraction(3, 2)), ("Negra (1)", Fraction(1)), ("Corchea con puntillo (¾)", Fraction(3, 4)),
    ("Corchea (½)", Fraction(1, 2)), ("Tresillo de negra (⅔)", Fraction(2, 3)), ("Semicorchea (¼)", Fraction(1, 4)),
    ("Tresillo de corchea (⅓)", Fraction(1, 3)), ("Fusa (⅛)", Fraction(1, 8)),
]
GRIDS = [("Rejilla: semicorchea", Fraction(1, 4)), ("Rejilla: corchea", Fraction(1, 2)),
         ("Rejilla: negra", Fraction(1)), ("Rejilla: tresillo", Fraction(1, 3)), ("Rejilla: fusa", Fraction(1, 8))]
WINDOWS = [("1 compás", 1), ("2 compases", 2), ("4 compases", 4), ("8 compases", 8), ("Sistema completo", 0)]


class IssuesPanel(QWidget):
    go_to = Signal(object)        # Issue
    play = Signal(object)
    preview_changed = Signal(object)   # FixPreview o None

    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.filter = QComboBox()
        self.filter.addItems(["Pendientes", "Errores probables", "Para revisar", "Información", "Resueltos y rechazados",
                              "Todos"])
        self.filter.currentIndexChanged.connect(self.refresh)
        self.summary = muted_label("", wrap=False)
        lay.addLayout(hbox(self.filter, None, self.summary))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", "Compás", "Aviso"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.currentItemChanged.connect(self._on_current)
        self.tree.itemDoubleClicked.connect(lambda *_: self._go())
        lay.addWidget(self.tree, 3)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(110)
        lay.addWidget(self.detail, 2)
        self.b_go = text_button("Ir al compás", "eye")
        self.b_play = text_button("Escuchar", "play")
        self.b_accept = text_button("Aceptar corrección", "check", primary=True)
        self.b_reject = text_button("Ignorar", "cancel")
        self.b_reopen = text_button("Reabrir", "undo")
        self.b_all = text_button("Aceptar todas las de este tipo", "check")
        self.b_all.setToolTip("Aplica la misma corrección en todos los avisos pendientes de este tipo. "
                              "Se deshace con un solo Ctrl+Z.")
        self.b_all.clicked.connect(self._accept_all)
        self.b_go.clicked.connect(self._go)
        self.b_play.clicked.connect(lambda: self.current() and self.play.emit(self.current()))
        self.b_accept.clicked.connect(self._accept)
        self.b_reject.clicked.connect(self._reject)
        self.b_reopen.clicked.connect(self._reopen)
        lay.addLayout(hbox(self.b_go, self.b_play, None))
        # Antes y después: escuchar y ver el cambio sin aplicarlo
        self.ck_preview = QCheckBox("Ver el cambio en el editor")
        self.ck_preview.setChecked(True)
        self.ck_preview.setToolTip("Contorno naranja: cómo quedaría. Atenuado y tachado: lo que cambia.")
        self.ck_preview.toggled.connect(lambda *_: self._on_current())
        self.b_before = text_button("Antes", "play", tooltip="Escucha el fragmento tal como está ahora")
        self.b_after = text_button("Después", "play", tooltip="Escucha el fragmento con la corrección, sin aplicarla")
        self.b_before.clicked.connect(lambda: self._listen(False))
        self.b_after.clicked.connect(lambda: self._listen(True))
        lay.addLayout(hbox(QLabel("Vista previa:"), self.b_before, self.b_after, self.ck_preview, None))
        self._preview = None
        lay.addLayout(hbox(self.b_accept, self.b_reject, self.b_reopen, None))
        lay.addLayout(hbox(self.b_all, None))
        self._update_buttons()

    def current(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def select_issue(self, issue_id: str) -> bool:
        """Selecciona un aviso por su id (si el filtro lo oculta, se muestran todos)."""
        for attempt in range(2):
            for k in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(k)
                issue = item.data(0, Qt.UserRole)
                if issue is not None and issue.id == issue_id:
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                    return True
            if attempt == 0:
                self.filter.setCurrentIndex(self.filter.count() - 1)   # «Todos» (vuelve a llenar la lista)
        return False

    def refresh(self) -> None:
        project = self.c.project
        cur = self.current()
        cur_key = cur.key() if cur else None
        self.tree.clear()
        if project is None:
            self.detail.clear()
            return
        mode = self.filter.currentIndex()
        shown = 0
        for issue in project.issues:
            if mode == 0 and issue.status != "abierta":
                continue
            if mode == 1 and not (issue.severity == "error" and issue.status == "abierta"):
                continue
            if mode == 2 and not (issue.severity == "aviso" and issue.status == "abierta"):
                continue
            if mode == 3 and not (issue.severity == "info" and issue.status == "abierta"):
                continue
            if mode == 4 and issue.status == "abierta":
                continue
            m = project.score.measures[issue.measure].number if issue.measure is not None and issue.measure < len(
                project.score.measures) else "—"
            item = QTreeWidgetItem(["●", str(m), issue.title])
            color = QColor(theme.severity_color(issue.severity))
            if issue.status != "abierta":
                color = QColor(theme.current().neutral)
            item.setForeground(0, color)
            item.setToolTip(2, issue.message)
            item.setData(0, Qt.UserRole, issue)
            self.tree.addTopLevelItem(item)
            shown += 1
            if cur_key and issue.key() == cur_key:
                self.tree.setCurrentItem(item)
        counts = {"error": 0, "aviso": 0}
        for i in project.issues:
            if i.status == "abierta" and i.severity in counts:
                counts[i.severity] += 1
        self.summary.setText(f"{counts['error']} errores · {counts['aviso']} por revisar")
        self._update_buttons()

    def _on_current(self, *_):
        issue = self.current()
        self._preview = None
        if issue is None:
            self.detail.clear()
        else:
            lines = [f"{SEVERITY_LABEL.get(issue.severity)} · {CATEGORY_LABEL.get(issue.category, issue.category)} · "
                     f"{EVIDENCE_LABEL.get(issue.evidence, issue.evidence)}", "", issue.message]
            if issue.fix:
                lines += ["", f"Corrección propuesta: {issue.fix.description}"]
                if issue.status == "abierta" and self.c.project is not None:
                    try:
                        from note_cascade.music_validation.preview import preview_fix
                        self._preview = preview_fix(self.c.project.score, issue.fix)
                    except Exception as exc:  # una vista previa que falla no debe impedir revisar
                        self._preview = None
                        lines += ["", f"(No se pudo calcular la vista previa: {exc})"]
                    if self._preview is not None:
                        shown = self._preview.lines[:14]
                        lines += ["", "Qué cambiaría (antes → después):"] + [f"  • {x}" for x in shown]
                        if len(self._preview.lines) > len(shown):
                            lines.append(f"  … y {len(self._preview.lines) - len(shown)} cambio(s) más")
            if issue.status != "abierta":
                lines += ["", f"Estado: {issue.status}"]
            self.detail.setPlainText("\n".join(lines))
            if issue.measure is not None:
                self.go_to.emit(issue)
        self.preview_changed.emit(self._preview if self.ck_preview.isChecked() else None)
        self._update_buttons()

    def _preview_range(self) -> tuple[int, int] | None:
        issue = self.current()
        if self._preview is not None and self._preview.measures:
            return self._preview.measures[0], self._preview.measures[-1]
        if issue is not None and issue.measure is not None:
            return issue.measure, issue.measure_end if issue.measure_end is not None else issue.measure
        return None

    def _listen(self, after: bool) -> None:
        from note_cascade.player.preview import play_segment
        rng = self._preview_range()
        if rng is None or self.c.project is None:
            return
        score = self._preview.after if (after and self._preview is not None) else self.c.project.score
        self.c.player.pause()
        play_segment(score, rng[0], rng[1], self.c.cfg.playback.timbre)

    def _update_buttons(self) -> None:
        issue = self.current()
        self.b_go.setEnabled(issue is not None and issue.measure is not None)
        self.b_play.setEnabled(issue is not None and issue.measure is not None)
        self.b_accept.setEnabled(issue is not None and issue.fix is not None and issue.status == "abierta")
        self.b_reject.setEnabled(issue is not None and issue.status == "abierta")
        self.b_reopen.setEnabled(issue is not None and issue.status != "abierta")
        self.b_before.setEnabled(issue is not None and issue.measure is not None)
        self.b_after.setEnabled(self._preview is not None)
        group = learning.same_kind(issue, self.c.project.issues) if (issue is not None and self.c.project) else []
        self.b_all.setEnabled(len(group) > 1 and issue.status == "abierta")
        self.b_all.setText(f"Aceptar todas las de este tipo ({len(group)})" if len(group) > 1
                           else "Aceptar todas las de este tipo")

    def _go(self) -> None:
        issue = self.current()
        if issue is not None:
            self.go_to.emit(issue)

    def _accept(self) -> None:
        issue = self.current()
        if issue is None or issue.fix is None:
            return
        msg = self.c.accept_issue(issue)
        if msg:
            self.detail.appendPlainText("\n✔ " + msg)

    def _accept_all(self) -> None:
        issue = self.current()
        if issue is None or issue.fix is None:
            return
        group = learning.same_kind(issue, self.c.project.issues)
        answer = QMessageBox.question(
            self, "Aceptar todas",
            f"Se aplicará «{issue.fix.description}» en {len(group)} avisos de este tipo.\n\n"
            "Revisa antes uno o dos escuchándolos. Se puede deshacer todo con Ctrl+Z.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        msg = self.c.accept_same_kind(issue)
        if msg:
            self.detail.appendPlainText("\n✔ " + msg)

    def _reject(self) -> None:
        issue = self.current()
        if issue is not None:
            self.c.reject_issue(issue)

    def _reopen(self) -> None:
        issue = self.current()
        if issue is not None:
            self.c.reopen_issue(issue)


class NoteInspector(QWidget):
    def __init__(self, controller, review) -> None:
        super().__init__()
        self.c = controller
        self.review = review
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.big = QLabel("Ninguna nota seleccionada")
        self.big.setObjectName("PageTitle")
        self.sub = muted_label("Haz clic en una nota de la imagen o del editor de abajo.")
        self.status_pill = Pill("")
        lay.addWidget(self.big)
        lay.addLayout(hbox(self.sub, None, self.status_pill))
        # Por qué tiene ese color: avisos que la incluyen (se abren con un clic), confianza, imagen…
        self.why = QLabel("")
        self.why.setTextFormat(Qt.RichText)
        self.why.setWordWrap(True)
        self.why.setOpenExternalLinks(False)
        self.why.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.why.linkActivated.connect(self._open_issue)
        lay.addWidget(self.why)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.f_midi = QSpinBox()
        self.f_midi.setRange(0, 127)
        self.f_midi.setSpecialValueText("—")   # varias notas seleccionadas
        self.f_midi.setToolTip("Número MIDI (60 = Do central)")
        self.f_midi.editingFinished.connect(self._set_midi)
        self.f_acc = QComboBox()
        self.f_acc.addItems(["♮ natural", "♯ sostenido", "♭ bemol", "𝄪 doble sostenido", "𝄫 doble bemol"])
        self.f_acc.activated.connect(self._set_accidental)
        self.f_dur = QComboBox()
        for label, _q in DURATIONS:
            self.f_dur.addItem(label)
        self.f_dur.activated.connect(self._set_duration)
        self.f_hand = QComboBox()
        self.f_hand.addItems(["Mano derecha", "Mano izquierda"])
        self.f_hand.activated.connect(self._set_hand)
        self.l_measure = QLabel("—")
        self.l_source = muted_label("—")
        form.addRow("Altura (MIDI)", self.f_midi)
        form.addRow("Alteración", self.f_acc)
        form.addRow("Duración", self.f_dur)
        form.addRow("Mano", self.f_hand)
        form.addRow("Posición", self.l_measure)
        form.addRow("Origen", self.l_source)
        lay.addLayout(form)
        b_up = text_button("Semitono ↑", "up")
        b_down = text_button("Semitono ↓", "down")
        b_oup = text_button("Octava ↑", "up")
        b_odown = text_button("Octava ↓", "down")
        b_up.clicked.connect(lambda: self.review.transpose(1))
        b_down.clicked.connect(lambda: self.review.transpose(-1))
        b_oup.clicked.connect(lambda: self.review.transpose(12))
        b_odown.clicked.connect(lambda: self.review.transpose(-12))
        lay.addLayout(hbox(b_up, b_down, b_oup, b_odown, None))
        b_del = text_button("Eliminar nota", "delete")
        b_del.setObjectName("Danger")
        b_del.clicked.connect(self.review.delete_selected)
        b_note = text_button("Oír nota", "audio")
        b_note.setToolTip("Toca la nota (o el acorde) seleccionada para reconocerla de oído")
        b_note.clicked.connect(self._hear)
        self.b_listen = text_button("Escuchar compás", "play")
        self.b_listen.clicked.connect(self.review.play_focus)
        lay.addLayout(hbox(b_note, self.b_listen, b_del, None))
        b_trace = text_button("Diagnóstico de altura", "health")
        b_trace.setToolTip("Muestra por qué la nota suena a esa altura: clave, posición en el pentagrama, "
                           "alteraciones, indicación de octava, MIDI exportado y tecla de la cascada.")
        b_trace.clicked.connect(self._trace)
        self.b_arp = text_button("Arpegiar acorde", "music")
        self.b_arp.setToolTip("Línea ondulada delante del acorde: se toca de la nota más grave a la más aguda, "
                              "muy seguidas, y cada una se mantiene hasta el final del acorde.")
        self.b_arp.clicked.connect(self._toggle_arpeggio)
        lay.addLayout(hbox(b_trace, self.b_arp, None))
        lay.addSpacing(10)
        lay.addWidget(title_label("Compás", "SectionTitle"))
        b_key = text_button("Armadura desde aquí…", "music")
        b_clef = text_button("Clave desde aquí…", "music")
        b_tempo = text_button("Tempo desde aquí…", "audio")
        b_ts = text_button("Indicación de compás…", "music")
        b_key.clicked.connect(self.review.change_key)
        b_clef.clicked.connect(self.review.change_clef)
        b_tempo.clicked.connect(self.review.change_tempo)
        b_ts.clicked.connect(self.review.change_time_signature)
        lay.addLayout(hbox(b_key, b_clef, None))
        lay.addLayout(hbox(b_tempo, b_ts, None))
        lay.addStretch(1)
        self.setEnabled_controls(False)

    def setEnabled_controls(self, on: bool) -> None:  # noqa: N802
        for w in (self.f_midi, self.f_acc, self.f_dur, self.f_hand):
            w.setEnabled(on)

    def show_notes(self, ids: list[str]) -> None:
        score = self.c.score
        notes = [score.note_by_id(i) for i in ids] if score else []
        notes = [n for n in notes if n is not None]
        self._show_reasons(notes)
        self._update_arpeggio_button(notes)
        if not notes:
            self.big.setText("Ninguna nota seleccionada")
            self.sub.setText("Haz clic en una nota de la imagen o del editor de abajo.")
            self.status_pill.setText("")
            self.setEnabled_controls(False)
            return
        if len(notes) > 1:
            self.big.setText(f"{len(notes)} notas seleccionadas")
            self.sub.setText(", ".join(n.name() for n in notes[:12]) + ("…" if len(notes) > 12 else ""))
            self.status_pill.setText("")
            self.setEnabled_controls(True)
            # nada de valores de la nota anterior: solo lo que comparten todas
            self.f_midi.blockSignals(True)
            self.f_midi.setValue(self.f_midi.minimum())
            self.f_midi.blockSignals(False)
            self.f_midi.setEnabled(False)
            self.f_acc.setCurrentIndex(-1)
            self.f_acc.setEnabled(False)
            durs = {n.duration for n in notes}
            idx = next((i for i, (_l, q) in enumerate(DURATIONS) if durs == {q}), -1)
            self.f_dur.setCurrentIndex(idx)
            self.f_dur.setPlaceholderText("varias" if len(durs) > 1 else (
                describe_duration(notes[0].duration, notes[0].tuplet) if idx < 0 else ""))
            hands = {(n.hand or "R") == RIGHT for n in notes}
            self.f_hand.setCurrentIndex((0 if hands == {True} else 1) if len(hands) == 1 else -1)
            self.l_measure.setText("—")
            self.l_source.setText(", ".join(sorted({n.source or "motor" for n in notes})))
            return
        n = notes[0]
        self.setEnabled_controls(not n.rest)
        if n.rest:
            self.big.setText("Silencio")
            self.sub.setText(describe_duration(n.duration, n.tuplet))
        else:
            self.big.setText(f"{n.name()}  ")
            black = "tecla negra" if P.is_black_key(n.midi) else "tecla blanca"
            self.sub.setText(f"{n.name('en')} · MIDI {n.midi} · {black} · {self._octave_hint(n.midi)}")
            self.f_midi.blockSignals(True)
            self.f_midi.setValue(n.midi)
            self.f_midi.blockSignals(False)
            self.f_acc.setCurrentIndex({0: 0, 1: 1, -1: 2, 2: 3, -2: 4}.get(n.alter, 0))
        idx = next((i for i, (_l, q) in enumerate(DURATIONS) if q == n.duration), -1)
        self.f_dur.setCurrentIndex(idx)
        self.f_dur.setPlaceholderText(describe_duration(n.duration, n.tuplet) if idx < 0 else "")
        self.f_hand.setCurrentIndex(0 if (n.hand or "R") == RIGHT else 1)
        m = self.c.score.measures[n.measure]
        beat = float(n.onset) + 1
        self.l_measure.setText(f"compás {m.number}, tiempo {beat:g} · pentagrama {n.staff}, voz {n.voice}"
                               + (" · nota de adorno" if n.grace else ""))
        conf = f", confianza {n.confidence:.2f}" if n.confidence is not None else ", sin puntuación de confianza"
        self.l_source.setText(f"{n.source or 'motor'}{conf}{' · modificada por ti' if n.user_modified else ''}")
        st = self.c.note_status(n.id)
        label = {"ok": "verificada", "revisar": "revisar", "error": "error probable", "sin_datos": "sin datos"}.get(st, st)
        self.status_pill.setText(label)
        self.status_pill.set_color(theme.status_color(st))

    def _show_reasons(self, notes: list) -> None:
        """Explica el color: «revisar» o «error probable» siempre con su motivo (y el aviso, para abrirlo)."""
        from html import escape

        from note_cascade.music_validation.explain import note_reasons, selection_reasons
        project = self.c.project
        if not notes or project is None:
            self.why.setText("")
            return
        pal = theme.current()
        colors = {"error": theme.status_color("error"), "revisar": theme.status_color("revisar"),
                  "info": pal.accent, "ok": theme.status_color("ok")}
        if len(notes) == 1:
            st = self.c.note_status(notes[0].id)
            reasons = note_reasons(project.score, project.issues, notes[0], st)
            head = {"revisar": "Por qué hay que revisarla:", "error": "Por qué puede estar mal:",
                    "ok": "Por qué está verificada:", "sin_datos": "Sin datos:"}.get(st, "")
        else:
            from note_cascade.music_validation.explain import count_text
            counts, reasons = selection_reasons(project.score, project.issues, notes, self.c.note_status)
            head = count_text(counts)
        lines = [f"<b>{escape(head)}</b>"] if head else []
        prev = None
        for r in reasons[:8]:
            if r.level == "info" and prev in ("error", "revisar", "ok"):
                lines.append("<b>Además:</b>")
            prev = r.level
            dot = f'<span style="color:{colors.get(r.level, pal.neutral)}">●</span>'
            text = escape(r.text)
            if r.issue is not None:
                text += f' — <a href="{r.issue.id}">ver aviso</a>'
            lines.append(f"{dot} {text}")
        if len(reasons) > 8:
            lines.append(f"… y {len(reasons) - 8} motivo(s) más en la pestaña «Avisos».")
        self.why.setText("<br>".join(lines))

    def _open_issue(self, issue_id: str) -> None:
        self.review.open_issue(issue_id)

    def _update_arpeggio_button(self, notes: list) -> None:
        from note_cascade.music_model.arpeggio import chord_of, is_arpeggiated
        score = self.c.score
        chords = [chord_of(score, n) for n in notes if not n.rest] if score else []
        playable = [c for c in chords if len({x.midi for x in c}) >= 2]
        self.b_arp.setEnabled(bool(playable))
        on = bool(playable) and all(is_arpeggiated(score, c[0]) for c in playable)
        self.b_arp.setText("Quitar arpegio" if on else "Arpegiar acorde")

    def _toggle_arpeggio(self) -> None:
        from note_cascade.music_model.arpeggio import is_arpeggiated, set_arpeggio
        score = self.c.score
        ids = self._ids()
        if score is None or not ids:
            return
        notes = [n for n in (score.note_by_id(i) for i in ids) if n is not None and not n.rest]
        on = not (notes and all(is_arpeggiated(score, n) for n in notes))
        self.c.apply_edit("Arpegiar acorde" if on else "Quitar arpegio", lambda s: set_arpeggio(s, ids, on))
        self._update_arpeggio_button(notes)

    @staticmethod
    def _octave_hint(midi: int) -> str:
        diff = midi - 60
        if diff == 0:
            return "Do central"
        octs = diff / 12
        where = "por encima" if diff > 0 else "por debajo"
        return f"{abs(diff)} semitonos {where} del Do central"

    def _ids(self) -> list[str]:
        return list(self.c.selection)

    def _hear(self) -> None:
        from note_cascade.player.preview import play_notes
        score = self.c.score
        if score is None:
            return
        midis = [n.midi for n in (score.note_by_id(i) for i in self._ids()) if n is not None and not n.rest]
        play_notes(midis, timbre=self.c.cfg.playback.timbre if self.c.cfg.playback.backend == "internal" else "piano")

    def _trace(self) -> None:
        from note_cascade.ui.widgets.pitch_trace_dialog import PitchTraceDialog
        PitchTraceDialog(self.c, self._ids(), self.window()).show()

    def _set_midi(self) -> None:
        ids = self._ids()
        if len(ids) != 1:
            return
        value = self.f_midi.value()
        n = self.c.score.note_by_id(ids[0])
        if n is None or n.midi == value:
            return
        self.c.apply_edit(f"Cambiar altura a {P.midi_name(value)}", lambda s: edits.set_pitch(s, ids, {ids[0]: value}))

    def _set_accidental(self, index: int) -> None:
        ids = self._ids()
        if len(ids) != 1:
            return
        n = self.c.score.note_by_id(ids[0])
        if n is None or n.rest:
            return
        alter = [0, 1, -1, 2, -2][index]
        self.c.apply_edit("Cambiar alteración", lambda s: edits.set_spelling(s, n.id, n.step, alter, n.octave))

    def _set_duration(self, index: int) -> None:
        ids = self._ids()
        if not ids or index < 0:
            return
        q = DURATIONS[index][1]
        self.c.apply_edit(f"Duración: {DURATIONS[index][0]}", lambda s: edits.set_duration(s, ids, q))

    def _set_hand(self, index: int) -> None:
        ids = self._ids()
        if ids:
            hand = RIGHT if index == 0 else LEFT
            self.c.apply_edit("Cambiar mano", lambda s: edits.set_hand(s, ids, hand))


class ReviewPage(QWidget):
    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.page_index = 0
        self.focus_measure = 0
        self.show_processed = False
        self._play_stop_at: float | None = None
        self._play_range: tuple[float, float] = (0.0, 0.0)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(8)

        # Barra superior
        self.title = title_label("Revisar", "SectionTitle")
        self.b_prev_page = IconButton("left", "Página anterior")
        self.b_next_page = IconButton("right", "Página siguiente")
        self.page_label = QLabel("—")
        self.b_prev_page.clicked.connect(lambda: self.go_page(self.page_index - 1))
        self.b_next_page.clicked.connect(lambda: self.go_page(self.page_index + 1))
        self.cb_view = QComboBox()
        self.cb_view.addItems(["Imagen original", "Imagen procesada"])
        self.cb_view.currentIndexChanged.connect(self._toggle_view)
        self.ck_measures = QCheckBox("Compases")
        self.ck_notes = QCheckBox("Notas")
        self.ck_issues = QCheckBox("Avisos")
        self.ck_text = QCheckBox("Textos")
        for ck in (self.ck_measures, self.ck_notes, self.ck_issues):
            ck.setChecked(True)
        for ck in (self.ck_measures, self.ck_notes, self.ck_issues, self.ck_text):
            ck.toggled.connect(lambda *_: self.show_page(self.page_index))
        b_zin = IconButton("zoom_in", "Ampliar (Ctrl + rueda)")
        b_zout = IconButton("zoom_out", "Reducir")
        b_fit = IconButton("fit", "Ajustar al ancho")
        self.b_undo = IconButton("undo", "Deshacer (Ctrl+Z)")
        self.b_redo = IconButton("redo", "Rehacer (Ctrl+Y)")
        self.b_undo.clicked.connect(self.c.undo)
        self.b_redo.clicked.connect(self.c.redo)
        root.addLayout(hbox(self.title, 12, self.b_prev_page, self.page_label, self.b_next_page, 12, self.cb_view,
                            self.ck_measures, self.ck_notes, self.ck_issues, self.ck_text, None, self.b_undo,
                            self.b_redo, b_zout, b_zin, b_fit))

        self.view = PageView()
        b_zin.clicked.connect(lambda: self.view.zoom_by(1.25))
        b_zout.clicked.connect(lambda: self.view.zoom_by(0.8))
        b_fit.clicked.connect(self.view.fit_width)
        self.view.note_clicked.connect(self._note_clicked_on_page)
        self.view.notes_selected.connect(self._notes_selected_on_page)
        self.view.measure_clicked.connect(self.set_focus_measure)

        self.tabs = QTabWidget()
        self.issues = IssuesPanel(controller)
        self.issues.go_to.connect(self._go_issue)
        self.issues.play.connect(self._play_issue)
        self.issues.preview_changed.connect(self._show_preview)
        self.inspector = NoteInspector(controller, self)
        self.text_list = QTreeWidget()
        self.text_list.setHeaderLabels(["Texto", "Clasificación", "Motivo"])
        self.text_list.setRootIsDecorated(False)
        self.text_list.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.steps = QPlainTextEdit()
        self.steps.setReadOnly(True)
        self.tabs.addTab(self.issues, "Avisos")
        self.tabs.addTab(self.inspector, "Nota")
        self.tabs.addTab(self.text_list, "Textos de la página")
        self.tabs.addTab(self.steps, "Preparación")

        # Leyenda: qué significa cada color de la página
        pal = theme.current()

        def chip(color: str, text: str, border: bool = False) -> str:
            style = (f"border:2px solid {color};" if border else f"background:{color};")
            return (f'<span style="{style} color:transparent;">&nbsp;&nbsp;&nbsp;</span>'
                    f'&nbsp;{text}&nbsp;&nbsp;&nbsp;')
        self.legend = QLabel(
            chip(pal.right_hand, "mano derecha") + chip(pal.left_hand, "mano izquierda") +
            chip(pal.neutral, "silencio") + chip(theme.status_color("revisar"), "revisar", True) +
            chip(theme.status_color("error"), "error probable", True) + chip(pal.accent, "compás actual", True) +
            "· sin borde de color = nota comprobada<br>Ctrl+clic: añadir notas a la selección · Mayús+arrastrar: "
            "seleccionar todas las de un área")
        self.legend.setObjectName("Muted")
        self.legend.setTextFormat(Qt.RichText)
        self.legend.setWordWrap(True)
        page_box = QWidget()
        pbl = QVBoxLayout(page_box)
        pbl.setContentsMargins(0, 0, 0, 0)
        pbl.setSpacing(4)
        pbl.addWidget(self.view, 1)
        pbl.addWidget(self.legend)

        top_split = QSplitter(Qt.Horizontal)
        top_split.addWidget(page_box)
        top_split.addWidget(self.tabs)
        top_split.setStretchFactor(0, 3)
        top_split.setStretchFactor(1, 2)
        top_split.setSizes([900, 520])

        # Editor
        editor_box = QWidget()
        el = QVBoxLayout(editor_box)
        el.setContentsMargins(0, 6, 0, 0)
        el.setSpacing(6)
        self.b_prev_m = IconButton("left", "Compases anteriores")
        self.b_next_m = IconButton("right", "Compases siguientes")
        self.b_prev_m.clicked.connect(lambda: self.set_focus_measure(self.focus_measure - max(1, self._window())))
        self.b_next_m.clicked.connect(lambda: self.set_focus_measure(self.focus_measure + max(1, self._window())))
        self.range_label = QLabel("—")
        self.cb_window = QComboBox()
        for label, _n in WINDOWS:
            self.cb_window.addItem(label)
        self.cb_window.setCurrentIndex(2)
        self.cb_window.currentIndexChanged.connect(lambda *_: self.set_focus_measure(self.focus_measure))
        self.cb_grid = QComboBox()
        for label, _g in GRIDS:
            self.cb_grid.addItem(label)
        self.cb_grid.currentIndexChanged.connect(self._grid_changed)
        self.cb_newdur = QComboBox()
        for label, _q in DURATIONS:
            self.cb_newdur.addItem("Nueva nota: " + label.split(" (")[0].lower())
        self.cb_newdur.setCurrentIndex(next(i for i, (_l, q) in enumerate(DURATIONS) if q == 1))   # negra
        self.cb_newdur.currentIndexChanged.connect(self._newdur_changed)
        b_play_m = text_button("Escuchar estos compases", "play")
        b_play_m.clicked.connect(self.play_window)
        b_stop = IconButton("stop", "Detener")
        b_stop.clicked.connect(self._stop_play)
        b_hand = text_button("Cambiar mano (H)", "hand")
        b_hand.clicked.connect(lambda: self._toggle_hand(list(self.c.selection)))
        hint = muted_label("Ctrl+clic: añadir a la selección · Arrastrar en vacío: seleccionar varias · Doble clic: "
                           "añadir nota · Arrastrar nota: mover · Borde derecho: duración · Supr: borrar · ↑↓: semitono "
                           "(Mayús: octava)", wrap=False)
        el.addLayout(hbox(self.b_prev_m, self.range_label, self.b_next_m, self.cb_window, self.cb_grid,
                          self.cb_newdur, b_hand, None, b_play_m, b_stop))
        self.editor = PianoRollEditor()
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidget(self.editor)
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(QScrollArea.NoFrame)
        el.addWidget(self.editor_scroll, 1)
        el.addWidget(hint)
        self.editor.selection_changed.connect(self._editor_selection)
        self.editor.edit_move.connect(self._edit_move)
        self.editor.edit_duration.connect(self._edit_duration)
        self.editor.edit_add.connect(self._edit_add)
        self.editor.edit_delete.connect(self._edit_delete)
        self.editor.edit_hand_toggle.connect(self._toggle_hand)
        self.editor.measure_clicked.connect(self.set_focus_measure)

        split = QSplitter(Qt.Vertical)
        split.addWidget(top_split)
        split.addWidget(editor_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([560, 330])
        root.addWidget(split, 1)

        QShortcut(QKeySequence.Undo, self, activated=self.c.undo)
        QShortcut(QKeySequence.Redo, self, activated=self.c.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.c.redo)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._space)

        self.c.project_changed.connect(self.load_project)
        self.c.score_changed.connect(lambda *_: self.refresh(keep_view=True))
        self.c.issues_changed.connect(self._issues_changed)
        self.c.selection_changed.connect(self._selection_changed)
        self.c.history_changed.connect(self._history_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ------------------------------------------------------------------ carga
    def load_project(self) -> None:
        project = self.c.project
        self.page_index = 0
        self.focus_measure = 0
        if project is None:
            self.view.clear()
            self.editor.set_score(None)
            return
        self.title.setText(f"Revisar · {project.score.title or project.name}")
        self.refresh()
        self.show_page(0)
        QTimer.singleShot(50, self.view.fit_width)
        self.set_focus_measure(0)

    def refresh(self, keep_view: bool = False) -> None:
        project = self.c.project
        if project is None:
            return
        status = dict(project.options.get("status", {}).get("notes", {}))
        for k, v in project.options.get("status", {}).get("measures", {}).items():
            status[f"m{k}"] = v
        self.editor.set_score(project.score, status)
        self.set_focus_measure(self.focus_measure, move_page=False)
        if keep_view:
            center = self.view.mapToScene(self.view.viewport().rect().center())
            transform = self.view.transform()
            self.show_page(self.page_index)
            self.view.setTransform(transform)
            self.view.centerOn(center)
        self.inspector.show_notes(self.c.selection)
        self.issues.refresh()

    def _issues_changed(self) -> None:
        self.issues.refresh()

    def _history_changed(self) -> None:
        self.b_undo.setEnabled(self.c.history.can_undo())
        self.b_redo.setEnabled(self.c.history.can_redo())
        self.b_undo.setToolTip(f"Deshacer: {self.c.history.undo_label()}" if self.c.history.can_undo() else "Deshacer")
        self.b_redo.setToolTip(f"Rehacer: {self.c.history.redo_label()}" if self.c.history.can_redo() else "Rehacer")

    # ------------------------------------------------------------------ página
    def _toggle_view(self, idx: int) -> None:
        self.show_processed = idx == 1
        self.show_page(self.page_index)

    def show_page(self, index: int) -> None:
        project = self.c.project
        if project is None or not project.pages:
            self.view.clear()
            self.page_label.setText("sin imagen (archivo estructurado)")
            self.b_prev_page.setEnabled(False)
            self.b_next_page.setEnabled(False)
            return
        index = max(0, min(index, len(project.pages) - 1))
        self.page_index = index
        rec = project.pages[index]
        self.page_label.setText(f"{rec.label} ({index + 1} de {len(project.pages)})")
        self.b_prev_page.setEnabled(index > 0)
        self.b_next_page.setEnabled(index < len(project.pages) - 1)
        rel = rec.processed_file if self.show_processed else rec.original_file
        pix = QPixmap(str(project.page_path(rel)))
        matrix = np.array(rec.matrix) if self.show_processed else None
        self.view.set_image(pix, tuple(rec.original_size), matrix)
        self._draw_overlays(rec)
        self._fill_text_list(rec)
        self.steps.setPlainText("\n".join(["Preparación de la imagen:"] + [f"• {s}" for s in rec.steps] +
                                          ([""] + [f"⚠ {w}" for w in rec.warnings] if rec.warnings else []) +
                                          ["", f"Tipo detectado: {rec.quality_kind}",
                                           f"Resolución: {rec.dpi:.0f} ppp" if rec.dpi else "Resolución: original"]))

    def go_page(self, index: int) -> None:
        """Cambia de página y lleva el compás de trabajo al primero de esa página."""
        self.show_page(index)
        project = self.c.project
        if project is None or not project.pages:
            return
        page_no = project.pages[self.page_index].index
        first = next((m.index for m in project.score.measures
                      if m.position is not None and m.position.page == page_no), None)
        if first is not None:
            self.set_focus_measure(first, move_page=False)

    def _draw_overlays(self, rec) -> None:
        project = self.c.project
        score = project.score
        pal = theme.current()
        if self.ck_measures.isChecked():
            for m in score.measures:
                if m.position is None or m.position.page != rec.index:
                    continue
                st = self.c.measure_status(m.index)
                color = theme.status_color(st) if st in ("error", "revisar") else pal.accent
                r = self.view.norm_rect(m.position.x, m.position.y, m.position.w, m.position.h)
                self.view.add_measure_box(m.index, r, color, m.number)
        if self.ck_notes.isChecked():
            for n in score.notes:
                if n.position is None or n.position.page != rec.index:
                    continue
                st = self.c.note_status(n.id)
                r = self.view.norm_rect(n.position.x, n.position.y, n.position.w, n.position.h)
                color = pal.neutral if n.rest else theme.hand_color(n.hand, n.part)
                self.view.add_note_marker(n.id, r, color, theme.status_color(st) if st in ("error", "revisar") else None,
                                          n.position.approximate)
        if self.ck_issues.isChecked():
            done = set()
            for issue in project.issues:
                if issue.status != "abierta" or issue.severity == "info" or issue.measure is None:
                    continue
                if issue.measure in done or issue.measure >= len(score.measures):
                    continue
                m = score.measures[issue.measure]
                if m.position is None or m.position.page != rec.index:
                    continue
                done.add(issue.measure)
                r = self.view.norm_rect(m.position.x, m.position.y, m.position.w, m.position.h)
                self.view.add_issue_marker(r, theme.severity_color(issue.severity))
        if self.ck_text.isChecked():
            for t in rec.text:
                if t.get("category") == "simbolo_musical":
                    continue
                r = self.view.norm_rect(t["x0"], t["y0"], t["x1"] - t["x0"], t["y1"] - t["y0"])
                color = CATEGORY_COLORS.get(t.get("category"), "#94a3b8")
                self.view.add_rect(r, color, 1.5, fill_alpha=40, z=2)
                self.view.add_label(r.bottomLeft(), CATEGORY_LABELS.get(t.get("category"), ""), color, 12)
        if self.c.selection:
            self.view.highlight_notes(self.c.selection)

    def _fill_text_list(self, rec) -> None:
        self.text_list.clear()
        for t in rec.text:
            if t.get("category") == "simbolo_musical":
                continue
            item = QTreeWidgetItem([t.get("text", ""), CATEGORY_LABELS.get(t.get("category"), t.get("category", "")) +
                                    (" · efecto musical" if t.get("musical") else ""), t.get("reason", "")])
            item.setForeground(1, QColor(CATEGORY_COLORS.get(t.get("category"), "#94a3b8")))
            self.text_list.addTopLevelItem(item)

    # ------------------------------------------------------------------ compases
    def _window(self) -> int:
        return WINDOWS[self.cb_window.currentIndex()][1]

    def set_focus_measure(self, index: int, move_page: bool = True) -> None:
        score = self.c.score
        if score is None or not score.measures:
            return
        index = max(0, min(index, len(score.measures) - 1))
        self.focus_measure = index
        w = self._window()
        if w == 0:
            a = index
            while a > 0 and not score.measures[a].new_system:
                a -= 1
            b = index
            while b + 1 < len(score.measures) and not score.measures[b + 1].new_system:
                b += 1
        else:
            a = (index // w) * w
            b = min(len(score.measures) - 1, a + w - 1)
        self.editor.set_range(a, b)
        self.range_label.setText(f"Compases {score.measures[a].number}–{score.measures[b].number} de "
                                 f"{len(score.measures)}")
        m = score.measures[index]
        self.inspector.b_listen.setText(f"Escuchar compás {m.number}")
        if move_page and m.position is not None and m.position.page != self.page_index and self.c.project.pages:
            page_list = [p.index for p in self.c.project.pages]
            if m.position.page in page_list:
                self.show_page(page_list.index(m.position.page))
        self.view.highlight_measure(index)

    def open_issue(self, issue_id: str) -> None:
        """Desde el panel «Nota»: abre el aviso en la pestaña «Avisos» (con su explicación y su corrección)."""
        self.tabs.setCurrentWidget(self.issues)
        self.issues.select_issue(issue_id)

    def _go_issue(self, issue) -> None:
        if issue.measure is not None:
            self.set_focus_measure(issue.measure)
        if issue.note_ids:
            self.c.select([i for i in issue.note_ids if self.c.score.note_by_id(i)][:40])

    def _grid_changed(self, idx: int) -> None:
        self.editor.grid = GRIDS[idx][1]

    def _newdur_changed(self, idx: int) -> None:
        self.editor.default_duration = DURATIONS[idx][1]

    # ------------------------------------------------------------------ selección
    def _note_clicked_on_page(self, note_id: str, add: bool) -> None:
        sel = list(self.c.selection) if add else []
        if note_id in sel:
            sel.remove(note_id)
        else:
            sel.append(note_id)
        self.c.select(sel)
        n = self.c.score.note_by_id(note_id)
        if n is not None and not (self.editor.m_from <= n.measure <= self.editor.m_to):
            self.set_focus_measure(n.measure, move_page=False)
        self.tabs.setCurrentWidget(self.inspector)

    def _show_preview(self, preview) -> None:
        if preview is None:
            self.editor.set_preview([], set())
            return
        self.editor.set_preview(preview.added, {n.id for n in preview.removed})
        if preview.measures and not (self.editor.m_from <= preview.measures[0] <= self.editor.m_to):
            self.set_focus_measure(preview.measures[0], move_page=False)

    def _notes_selected_on_page(self, ids: list, add: bool) -> None:
        sel = list(self.c.selection) if add else []
        sel += [i for i in ids if i not in sel]
        self.c.select(sel)
        self.tabs.setCurrentWidget(self.inspector)

    def _editor_selection(self, ids: list) -> None:
        # La selección viene del propio editor: no hay que mover su vista (con Ctrl+clic se
        # siguen añadiendo notas donde está el ratón).
        self._selecting_in_editor = True
        try:
            self.c.select(ids)
        finally:
            self._selecting_in_editor = False
        if ids:
            self.tabs.setCurrentWidget(self.inspector)

    def _selection_changed(self, ids: list) -> None:
        score = self.c.score
        if score is not None and ids and not getattr(self, "_selecting_in_editor", False):
            n = score.note_by_id(ids[-1])
            if n is not None and not (self.editor.m_from <= n.measure <= self.editor.m_to):
                self.set_focus_measure(n.measure, move_page=False)
        self.editor.set_selection(ids)
        self.view.highlight_notes(ids)
        self.inspector.show_notes(ids)
        if score is not None and ids and not getattr(self, "_selecting_in_editor", False):
            n = score.note_by_id(ids[-1])
            if n is not None and not n.rest and self.editor.m_from <= n.measure <= self.editor.m_to:
                r = self.editor._note_rect(n)
                self.editor_scroll.ensureVisible(int(r.center().x()), int(r.center().y()), 80, 60)

    # ------------------------------------------------------------------ ediciones
    def transpose(self, semis: int) -> None:
        ids = list(self.c.selection)
        if ids:
            self.c.apply_edit(f"Mover {semis:+d} semitonos", lambda s: edits.set_pitch(s, ids, delta=semis))

    def delete_selected(self) -> None:
        ids = list(self.c.selection)
        if ids:
            self._edit_delete(ids)

    def _edit_move(self, ids: list, semis: int, dq) -> None:
        def do(s):
            if semis:
                edits.set_pitch(s, ids, delta=semis)
            if dq:
                edits.move_notes(s, ids, dq)
        self.c.apply_edit("Mover nota(s)", do)

    def _edit_duration(self, ids: list, q) -> None:
        self.c.apply_edit(f"Duración: {describe_duration(q)}", lambda s: edits.set_duration(s, ids, q))

    def _edit_add(self, measure: int, onset, midi: int, dur) -> None:
        score = self.c.score
        part = 0
        staff = 1
        if score.parts and score.parts[0].is_piano and score.parts[0].staves >= 2:
            staff = 1 if midi >= 60 else 2
        holder = {}

        def do(s):
            holder["n"] = edits.add_note(s, measure, onset, midi, dur, part, staff)
        self.c.apply_edit(f"Añadir {P.midi_name(midi)}", do)
        if "n" in holder:
            self.c.select([holder["n"].id])

    def _edit_delete(self, ids: list) -> None:
        self.c.apply_edit(f"Eliminar {len(ids)} figura(s)", lambda s: edits.delete_notes(s, ids))
        self.c.select([])

    def _toggle_hand(self, ids: list) -> None:
        if not ids:
            return
        score = self.c.score
        first = score.note_by_id(ids[0])
        new = LEFT if (first and first.hand == RIGHT) else RIGHT
        self.c.apply_edit("Cambiar mano", lambda s: edits.set_hand(s, ids, new))

    # ------------------------------------------------------------------ compás: armadura, clave, tempo
    def change_key(self) -> None:
        score = self.c.score
        if score is None:
            return
        m = self.focus_measure
        current = score.key_at(0, m)
        items = [P.key_signature_text(f) for f in range(-7, 8)]
        choice, ok = QInputDialog.getItem(self, "Armadura", f"Armadura desde el compás {score.measures[m].number} "
                                          "(las notas sin alteración escrita se reinterpretan):", items, current + 7, False)
        if ok:
            fifths = items.index(choice) - 7
            self.c.apply_edit(f"Armadura: {choice}", lambda s: set_key(s, 0, m, None, fifths))

    def change_clef(self) -> None:
        score = self.c.score
        if score is None or not score.parts:
            return
        m = self.focus_measure
        staves = score.parts[0].staves
        staff = 1
        if staves > 1:
            staff, ok = QInputDialog.getInt(self, "Clave", "Pentagrama (1 = superior):", 1, 1, staves)
            if not ok:
                return
        options = [("Clave de sol", "G", 2), ("Clave de fa", "F", 4), ("Clave de do en tercera", "C", 3),
                   ("Clave de do en cuarta", "C", 4)]
        choice, ok = QInputDialog.getItem(self, "Clave", "Nueva clave (las notas se reinterpretan por su posición):",
                                          [o[0] for o in options], 0, False)
        if ok:
            _l, sign, line = next(o for o in options if o[0] == choice)
            self.c.apply_edit(f"{choice} en el pentagrama {staff}", lambda s: set_clef(s, 0, staff, m, sign, line))

    def change_tempo(self) -> None:
        score = self.c.score
        if score is None:
            return
        m = self.focus_measure
        bpm, ok = QInputDialog.getDouble(self, "Tempo", f"Negras por minuto desde el compás {score.measures[m].number}:",
                                         self.c.performance.bpm_at(0) if self.c.performance else 100, 20, 320, 1)
        if ok:
            self.c.apply_edit(f"Tempo {bpm:g}", lambda s: edits.set_tempo(s, m, bpm))

    def change_time_signature(self) -> None:
        score = self.c.score
        if score is None:
            return
        m = self.focus_measure
        cur = score.measures[m].time_sig or (4, 4)
        text, ok = QInputDialog.getText(self, "Indicación de compás",
                                        f"Compás desde el compás {score.measures[m].number} (por ejemplo 3/4, 6/8, 12/8):",
                                        text=f"{cur[0]}/{cur[1]}")
        if not ok:
            return
        try:
            num, den = (int(x) for x in text.strip().split("/"))
            assert num > 0 and den in (1, 2, 4, 8, 16, 32)
        except Exception:
            QMessageBox.warning(self, "Compás", "Escribe el compás como dos números separados por «/», por ejemplo 3/4.")
            return
        from note_cascade.music_validation.fixes import apply_fix
        from note_cascade.music_validation.issues import Fix
        self.c.apply_edit(f"Compás {num}/{den}", lambda s: apply_fix(s, Fix("", "set_time_signature",
                                                                               {"from_measure": m, "num": num,
                                                                                "den": den})))

    # ------------------------------------------------------------------ reproducción
    def _play_measures(self, a: int, b: int) -> None:
        perf = self.c.performance
        if perf is None:
            return
        ia = perf.first_play_index_of_measure(a)
        ib = perf.first_play_index_of_measure(b)
        if ia is None:
            return
        ib = ib if ib is not None else ia
        start = perf.measure_start_s(ia)
        end = perf.measure_end_s(ib)
        player = self.c.player
        player.set_loop(None, None)
        player.seek(start)
        player.play()
        self._play_range = (start, end)
        self._play_stop_at = end

    def play_window(self) -> None:
        self._play_measures(self.editor.m_from, self.editor.m_to)

    def play_focus(self) -> None:
        self._play_measures(self.focus_measure, self.focus_measure)

    def _play_issue(self, issue) -> None:
        if issue.measure is not None:
            end = issue.measure_end if issue.measure_end is not None else issue.measure
            self._play_measures(issue.measure, end)

    def _stop_play(self) -> None:
        self.c.player.pause()
        self._play_stop_at = None

    def _space(self) -> None:
        if self.c.player.is_playing:
            self._stop_play()
        else:
            self.play_window()

    def _tick(self) -> None:
        try:
            self._tick_inner()
        except (IndexError, KeyError, AttributeError):
            self.editor.playhead_q = None  # estado transitorio mientras se recalcula la partitura

    def _tick_inner(self) -> None:
        player = self.c.player
        perf = self.c.performance
        if self._play_stop_at is not None:
            # La parada solo vale para la reproducción que empezó esta pantalla: se olvida si se
            # detuvo, si otra pantalla movió la posición o si esta pantalla dejó de verse.
            now = player.position()
            start, end = self._play_range
            if not player.is_playing or not self.isVisible() or now < start - 0.5 or now > end + 1.5:
                self._play_stop_at = None
            elif now >= self._play_stop_at:
                player.pause()
                self._play_stop_at = None
        if not self.isVisible() or perf is None:
            return
        if player.is_playing:
            now = player.position()
            q = perf.s_to_q(now)
            idx = perf.play_index_at_q(q)
            if idx < len(perf.play_measures):
                pm = perf.play_measures[idx]
                written_q = float(self.editor._starts[pm.measure]) + (q - pm.start_q) if self.editor._starts else None
                self.editor.playhead_q = written_q
                self.editor.update()
        elif self.editor.playhead_q is not None:
            self.editor.playhead_q = None
            self.editor.update()
