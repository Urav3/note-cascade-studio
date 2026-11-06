"""Paso 4 — Escuchar: reproducción y visualizador de notas en cascada."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from note_cascade.piano_roll.keyboard import RANGES
from note_cascade.piano_roll.waterfall import WaterfallWidget
from note_cascade.player import synth
from note_cascade.ui import theme
from note_cascade.ui.widgets.common import IconButton, fmt_time, hbox, muted_label, text_button, title_label


class HandToggle(QCheckBox):
    def __init__(self, text: str, color: str) -> None:
        super().__init__(text)
        self.setChecked(True)
        self.setStyleSheet(f"QCheckBox::indicator:checked {{ background: {color}; border-color: {color}; }}")


class ListenPage(QWidget):
    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.player = controller.player
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        self.title = title_label("Escuchar", "SectionTitle")
        self.status = muted_label("", wrap=False)
        self.b_score = text_button("Ver partitura", "page",
                                   tooltip="Abre una ventana con la partitura que resalta las notas que suenan")
        self.b_score.clicked.connect(self.show_score_window)
        self._score_window = None
        root.addLayout(hbox(self.title, None, self.status, 12, self.b_score))

        self.water = WaterfallWidget(self.player)
        self.water.seek_requested.connect(self.seek)
        root.addWidget(self.water, 1)

        # Línea de tiempo
        self.timeline = QSlider(Qt.Horizontal)
        self.timeline.setRange(0, 1000)
        self.timeline.sliderMoved.connect(lambda v: self.seek(v / 1000 * self.player.duration()))
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setMinimumWidth(110)
        root.addLayout(hbox(self.timeline, self.time_label))

        # Transporte
        pal = theme.current()
        self.b_start = IconButton("prev", "Al principio (Inicio)")
        self.b_prevm = IconButton("left", "Compás anterior (←)")
        self.b_play = IconButton("play", "Reproducir / pausa (Espacio)", size=22)
        self.b_stop = IconButton("stop", "Detener y volver al inicio")
        self.b_nextm = IconButton("right", "Compás siguiente (→)")
        self.b_start.clicked.connect(lambda: self.seek(0))
        self.b_prevm.clicked.connect(lambda: self.step_measure(-1))
        self.b_nextm.clicked.connect(lambda: self.step_measure(1))
        self.b_play.clicked.connect(self.toggle)
        self.b_stop.clicked.connect(self.stop)
        self.measure_label = QLabel("Compás —")
        self.measure_label.setMinimumWidth(120)
        self.sp_measure = QSpinBox()
        self.sp_measure.setRange(1, 1)
        self.sp_measure.setPrefix("Ir al compás ")
        self.sp_measure.editingFinished.connect(lambda: self.go_measure(self.sp_measure.value() - 1))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(25, 200)
        self.speed.setValue(100)
        self.speed.setFixedWidth(150)
        self.speed_label = QLabel("Velocidad 100 %")
        self.speed.setPageStep(25)
        # se aplica poco después del último cambio (arrastrar, clic en la barra o teclado)
        self._speed_timer = QTimer(self)
        self._speed_timer.setSingleShot(True)
        self._speed_timer.setInterval(250)
        self._speed_timer.timeout.connect(lambda: self.player.set_speed(self.speed.value() / 100))
        self.speed.valueChanged.connect(self._speed_changed)
        self.speed.sliderReleased.connect(self._speed_timer.start)
        root.addLayout(hbox(self.b_start, self.b_prevm, self.b_play, self.b_stop, self.b_nextm, 10, self.measure_label,
                            self.sp_measure, None, self.speed_label, self.speed))

        # Bucle, manos y vista
        self.ck_loop = QCheckBox("Repetir compases")
        self.sp_loop_a = QSpinBox()
        self.sp_loop_b = QSpinBox()
        for sp in (self.sp_loop_a, self.sp_loop_b):
            sp.setRange(1, 1)
            sp.valueChanged.connect(self._update_loop)
        self.ck_loop.toggled.connect(self._update_loop)
        self.ck_rh = HandToggle("Oír derecha", pal.right_hand)
        self.ck_lh = HandToggle("Oír izquierda", pal.left_hand)
        self.ck_rh.setToolTip("Silencia o activa el sonido de la mano derecha")
        self.ck_lh.setToolTip("Silencia o activa el sonido de la mano izquierda")
        self.ck_rh.toggled.connect(lambda on: self.player.set_muted("R", not on))
        self.ck_lh.toggled.connect(lambda on: self.player.set_muted("L", not on))
        self.cb_show = QComboBox()
        self.cb_show.addItems(["Ver ambas manos", "Ver solo la derecha", "Ver solo la izquierda"])
        self.cb_show.currentIndexChanged.connect(self._update_visible)
        self.cb_range = QComboBox()
        self.cb_range.addItems(list(RANGES.keys()))
        self.cb_range.setCurrentIndex(len(RANGES) - 1)
        self.cb_range.setToolTip("Teclas visibles en el teclado")
        self.cb_range.currentIndexChanged.connect(self._update_range)
        self.range_info = muted_label("", wrap=False)
        self.range_info.setToolTip("Teclas que se ven y registro real de la pieza. Las notas nunca se cambian de "
                                   "octava para que quepan: si eliges menos teclas, las que quedan fuera no se dibujan.")
        self.ck_names = QCheckBox("Nombres")
        self.ck_names.setToolTip("Mostrar el nombre de cada nota (Do, Re, Mi…)")
        self.ck_names.setChecked(True)
        self.ck_names.toggled.connect(self._update_names)
        self.ck_status = QCheckBox("Marcar dudosas")
        self.ck_status.setChecked(True)
        self.ck_status.setToolTip("Borde amarillo: revisar · borde rojo: error probable (según la validación)")
        self.ck_status.toggled.connect(self._update_status_marks)
        # Separar las manos cuando se juntan en el teclado (solo para escuchar y ver; no cambia lo exportado)
        self.cb_sep = QComboBox()
        self.cb_sep.addItem("Manos como están escritas", "no")
        self.cb_sep.addItem("Separar manos donde se juntan", "auto")
        self.cb_sep.addItem("Separar manos siempre", "siempre")
        self.cb_sep.setToolTip("Donde las dos manos se cruzan o quedan muy cerca, baja la izquierda una octava "
                               "(o sube la derecha) en todo ese pasaje, para verlas y oírlas por separado. Solo "
                               "afecta a lo que ves y oyes aquí: el MIDI y el MusicXML exportados llevan las notas "
                               "escritas.")
        self.cb_sep.setCurrentIndex(max(0, self.cb_sep.findData(self.c.cfg.playback.separate_hands)))
        self.cb_sep.currentIndexChanged.connect(self._separation_changed)
        root.addLayout(hbox(self.ck_loop, QLabel("del"), self.sp_loop_a, QLabel("al"), self.sp_loop_b, 16, self.ck_rh,
                            self.ck_lh, None, self.cb_sep, self.cb_show))

        self.cb_sound = QComboBox()
        for key, label in synth.TIMBRES.items():
            self.cb_sound.addItem(label, ("internal", key))
        self.cb_sound.addItem("Sintetizador MIDI de Windows (piano GS)", ("windows_midi", "piano"))
        self.cb_sound.currentIndexChanged.connect(self._sound_changed)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 120)
        self.volume.setValue(int(self.c.cfg.playback.volume * 100))
        self.volume.setFixedWidth(120)
        self.volume.valueChanged.connect(lambda v: self.player.set_volume(v / 100))
        self.ck_repeats = QCheckBox("Expandir repeticiones")
        self.ck_repeats.setChecked(self.c.expand_repeats)
        self.ck_repeats.setToolTip("Si la partitura tiene barras de repetición, D.C. o D.S., se reproducen las "
                                   "repeticiones. Desactívalo si el resultado es ambiguo.")
        self.ck_repeats.toggled.connect(self._repeats_changed)
        self.cb_sound.setToolTip("Sonido de la reproducción (todo funciona sin conexión)")
        self.water.setToolTip("Rueda del ratón: avanzar o retroceder · Ctrl + rueda: acercar o alejar en el tiempo")
        root.addLayout(hbox(QLabel("Sonido"), self.cb_sound, QLabel("Volumen"), self.volume, 12, self.ck_repeats,
                            None, self.range_info, self.cb_range, self.ck_names, self.ck_status))

        sel = next((i for i in range(self.cb_sound.count())
                    if self.cb_sound.itemData(i) == (self.c.cfg.playback.backend, self.c.cfg.playback.timbre)), 0)
        self.cb_sound.setCurrentIndex(sel)

        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.step_measure(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.step_measure(1))
        QShortcut(QKeySequence(Qt.Key_Home), self, activated=lambda: self.seek(0))

        self.c.performance_changed.connect(self.load_performance)
        self.c.issues_changed.connect(self._update_status_marks)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ------------------------------------------------------------------
    def load_performance(self) -> None:
        perf = self.c.performance
        score = self.c.score
        if perf is None or score is None:
            self.water.set_performance(None)
            return
        self.title.setText(f"Escuchar · {score.title or (self.c.project.name if self.c.project else '')}")
        self.water.renderer.title = ""
        self.water.set_performance(perf, [m.number for m in score.measures])
        n = max(1, len(perf.play_measures))
        for sp in (self.sp_measure, self.sp_loop_a, self.sp_loop_b):
            sp.blockSignals(True)
            sp.setRange(1, n)
            sp.blockSignals(False)
        if self.sp_loop_b.value() < self.sp_loop_a.value():
            self.sp_loop_b.setValue(min(n, self.sp_loop_a.value() + 3))
        self._update_range()
        self._update_loop()
        self._update_status_marks()
        if perf.expanded:
            self.status.setText(f"Repeticiones expandidas: {len(perf.play_measures)} compases interpretados.")
        else:
            self.status.setText("")

    def _watchdog(self, pos: float, dur: float) -> None:
        """Si suena «reproduciendo» pero la posición no avanza durante 2 s, se reabre la salida."""
        import time
        now = time.monotonic()
        last_pos, since = getattr(self, "_wd", (None, now))
        if not self.player.is_playing or not self.player.ready or pos >= dur - 0.1 or last_pos is None                 or abs(pos - last_pos) > 1e-4:
            self._wd = (pos, now)
            return
        if now - since > 2.0:
            self._wd = (None, now)
            self.player.recover()

    def show_score_window(self) -> None:
        from note_cascade.ui.widgets.score_follow import ScoreFollowWindow
        if self._score_window is None:
            self._score_window = ScoreFollowWindow(self.c, self.window())
            geo = self.window().geometry()
            self._score_window.move(geo.right() - self._score_window.width() - 30, geo.top() + 80)
        self._score_window.show()
        self._score_window.raise_()
        self._score_window.activateWindow()

    def _speed_changed(self, v: int) -> None:
        self.speed_label.setText(f"Velocidad {v} %")
        if not self.speed.isSliderDown():
            self._speed_timer.start()

    def toggle(self) -> None:
        if not self.player.ready and self.player.backend == "internal":
            self.status.setText("Preparando el sonido…")
        self.player.toggle()

    def stop(self) -> None:
        self.player.stop()

    def seek(self, seconds: float) -> None:
        self.player.seek(seconds)
        self.water.set_time(self.player.position())

    def step_measure(self, delta: int) -> None:
        perf = self.c.performance
        if perf is None:
            return
        idx = perf.play_index_at_s(self.player.position() + 0.02)
        self.go_measure(max(0, min(len(perf.play_measures) - 1, idx + delta)))

    def go_measure(self, play_index: int) -> None:
        perf = self.c.performance
        if perf is None:
            return
        self.seek(perf.measure_start_s(play_index))

    def _update_loop(self) -> None:
        perf = self.c.performance
        if perf is None:
            return
        if self.ck_loop.isChecked():
            a = self.sp_loop_a.value() - 1
            b = max(a, self.sp_loop_b.value() - 1)
            start, end = perf.measure_start_s(a), perf.measure_end_s(b)
            self.player.set_loop(start, end)
            self.water.renderer.loop = (start, end)
        else:
            self.player.set_loop(None, None)
            self.water.renderer.loop = None
        self.water.update()

    def _update_visible(self, idx: int) -> None:
        vis = {0: {"R", "L", "otros", "acordes"}, 1: {"R", "otros", "acordes"}, 2: {"L", "otros", "acordes"}}[idx]
        self.water.renderer.options.visible = vis
        self.water.update()

    def _update_range(self) -> None:
        key = self.cb_range.currentText()
        rng = RANGES.get(key)
        opts = self.water.renderer.options
        if rng is None:
            opts.auto = True
            from note_cascade.piano_roll.keyboard import auto_range
            opts.low, opts.high = auto_range([n.midi for n in self.water.renderer.notes])
        else:
            opts.auto = False
            opts.low, opts.high = rng
        self._update_range_info()
        self.water.update()

    def _update_range_info(self) -> None:
        from note_cascade.music_model.pitch import midi_name
        from note_cascade.piano_roll.keyboard import snap_white
        opts = self.water.renderer.options
        lo, hi = snap_white(max(0, opts.low), -1), snap_white(min(127, opts.high), +1)
        midis = [n.midi for n in self.water.renderer.notes]
        text = f"{midi_name(lo)}–{midi_name(hi)} · {hi - lo + 1} teclas"
        if midis:
            outside = sum(1 for m in midis if m < lo or m > hi)
            text += f" · pieza: {midi_name(min(midis))}–{midi_name(max(midis))}"
            if outside:
                text += f" · {outside} nota(s) fuera de la vista"
        self.range_info.setText(text)

    def _update_names(self, on: bool) -> None:
        self.water.renderer.options.show_names = on
        self.water.update()

    def _update_status_marks(self, *_args) -> None:
        r = self.water.renderer
        r.show_status = self.ck_status.isChecked()
        project = self.c.project
        r.status = dict(project.options.get("status", {}).get("notes", {})) if project else {}
        self.water.update()

    def _sound_changed(self, idx: int) -> None:
        backend, timbre = self.cb_sound.itemData(idx)
        self.c.cfg.playback.backend = backend
        self.c.cfg.playback.timbre = timbre
        self.player.set_timbre(timbre)
        self.player.set_backend(backend)

    def _separation_changed(self, *_args) -> None:
        self.c.cfg.playback.separate_hands = self.cb_sep.currentData() or "no"
        self.c.save_config()
        self.c.rebuild_performance()
        perf = self.c.performance
        info = next((x for x in (perf.notes_info if perf else []) if x.startswith("Manos separadas")), "")
        if self.c.cfg.playback.separate_hands != "no" and not info:
            info = "Las manos no se juntan en ningún pasaje: no hace falta separarlas."
        self.status.setText(info)

    def _repeats_changed(self, on: bool) -> None:
        self.c.expand_repeats = on
        self.c.rebuild_performance()

    def _tick(self) -> None:
        try:
            self._tick_inner()
        except (IndexError, KeyError, AttributeError):
            pass  # estado transitorio mientras se recalcula la interpretación

    def _tick_inner(self) -> None:
        if not self.isVisible():
            return
        pos = self.player.position()
        dur = self.player.duration()
        self._watchdog(pos, dur)
        self.time_label.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")
        if not self.timeline.isSliderDown() and dur > 0:
            self.timeline.setValue(int(pos / dur * 1000))
        self.b_play.set_icon("pause" if self.player.is_playing else "play")
        perf = self.c.performance
        if perf is not None and perf.play_measures:
            idx = perf.play_index_at_s(pos + 1e-3)
            pm = perf.play_measures[idx]
            number = self.c.score.measures[pm.measure].number if self.c.score else str(pm.measure + 1)
            extra = f" (repetición {pm.pass_number})" if pm.pass_number > 1 else ""
            self.measure_label.setText(f"Compás {number}{extra}")
        msg = self.player.message
        if msg:
            self.status.setText(msg)
            self._shown_player_msg = msg
        elif getattr(self, "_shown_player_msg", "") and self.status.text() == self._shown_player_msg:
            self.status.setText("")
            self._shown_player_msg = ""
