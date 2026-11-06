"""Motor de reproducción: sintetizador interno (sounddevice) o sintetizador MIDI de Windows.

La posición se expresa siempre en *segundos de la interpretación original*
(independiente de la velocidad elegida), de modo que el visualizador, la
revisión y los bucles usan la misma escala de tiempo.
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

from note_cascade.music_model.timeline import Performance
from note_cascade.player import synth

log = logging.getLogger(__name__)

BACKENDS = {"internal": "Sintetizador interno", "windows_midi": "Sintetizador MIDI de Windows"}


@dataclass
class PlayerState:
    playing: bool = False
    position: float = 0.0
    duration: float = 0.0
    speed: float = 1.0
    message: str = ""


class _Clock:
    """Reloj de reproducción silenciosa (sin dispositivo de audio)."""

    def __init__(self) -> None:
        self.start_wall = 0.0
        self.start_pos = 0.0
        self.running = False

    def start(self, pos: float) -> None:
        self.start_pos = pos
        self.start_wall = time.monotonic()
        self.running = True

    def stop(self) -> float:
        pos = self.now()
        self.running = False
        self.start_pos = pos
        return pos

    def now(self) -> float:
        if not self.running:
            return self.start_pos
        return self.start_pos + (time.monotonic() - self.start_wall)


class PlaybackEngine:
    def __init__(self, backend: str = "internal", timbre: str = "piano", volume: float = 0.8) -> None:
        self.backend = backend if backend in BACKENDS else "internal"
        self.timbre = timbre if timbre in synth.TIMBRES else "piano"
        self.volume = float(volume)
        self.perf: Performance | None = None
        self.speed = 1.0
        self.muted: set[str] = set()
        self.loop: tuple[float, float] | None = None
        self.message = ""
        self._lock = threading.RLock()
        self._stream_lock = threading.Lock()
        self._stems: dict[str, np.ndarray] = {}
        self._pos = 0                  # muestra actual (audio generado a la velocidad elegida)
        self._playing = False
        self._finished = False
        self._stream = None
        self._latency = 0.0
        self._clock = _Clock()
        self._silent = False
        self._render_thread: threading.Thread | None = None
        self._render_token = 0
        self._ready = threading.Event()
        self._midi = None
        self._midi_thread: threading.Thread | None = None
        self._midi_events: list[tuple[float, int, int, int, int, str]] = []
        self._midi_times: list[float] = []
        self._midi_active: set[tuple[int, int]] = set()
        self._midi_stop = threading.Event()
        self._midi_pos_ref = (0.0, 0.0)  # (wall, pos)

    # ------------------------------------------------------------------ carga
    def set_performance(self, perf: Performance, keep_position: bool = True) -> None:
        pos = self.position() if keep_position and self.perf is not None else 0.0
        was_playing = self._playing
        self.pause()
        self.perf = perf
        self._prepare(pos, was_playing)

    def set_speed(self, speed: float) -> None:
        speed = max(0.25, min(2.0, float(speed)))
        if abs(speed - self.speed) < 1e-3:
            return
        pos = self.position()
        was_playing = self._playing
        self.pause()
        self.speed = speed
        self._prepare(pos, was_playing)

    def set_backend(self, backend: str) -> None:
        if backend == self.backend:
            return
        pos = self.position()
        was = self._playing
        self._render_token += 1  # un audio interno que se esté generando ya no debe sonar
        self.pause()
        self._close_outputs()
        self.backend = backend if backend in BACKENDS else "internal"
        if self.perf is not None:
            self._prepare(pos, was)

    def set_timbre(self, timbre: str) -> None:
        if timbre == self.timbre or timbre not in synth.TIMBRES:
            return
        pos = self.position()
        was = self._playing
        self.pause()
        self.timbre = timbre
        if self.perf is not None:
            self._prepare(pos, was)

    def _prepare(self, pos: float, resume: bool) -> None:
        if self.perf is None:
            return
        self._ready.clear()
        if self.backend == "windows_midi":
            self._prepare_midi()
            self.seek(pos)
            self._ready.set()
            if resume:
                self.play()
            return
        self._render_token += 1
        token = self._render_token
        perf, speed, timbre = self.perf, self.speed, self.timbre
        self.message = "Preparando el sonido…"

        def work() -> None:
            try:
                stems, _total = synth.render_stems(perf, speed, timbre, cancel=lambda: token != self._render_token)
            except Exception as exc:  # pragma: no cover - defensivo
                log.exception("Error generando audio")
                self.message = f"No se pudo generar el audio: {exc}"
                return
            if token != self._render_token or self.backend != "internal":
                return
            with self._lock:
                self._stems = stems
                self._pos = int(pos / speed * synth.SAMPLE_RATE)
                self._finished = False
            self.message = ""
            self._ensure_stream()
            if token != self._render_token:
                return
            self._ready.set()
            if resume:
                with self._lock:
                    if token == self._render_token:
                        if self._silent:
                            self._clock.start(pos / speed)
                        self._playing = True

        self._render_thread = threading.Thread(target=work, daemon=True)
        self._render_thread.start()

    def wait_ready(self, timeout: float = 30.0) -> bool:
        return self._ready.wait(timeout)

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    # ------------------------------------------------------------------ salida de audio interna
    def _ensure_stream(self) -> None:
        with self._stream_lock:  # el hilo de generación y la interfaz pueden llegar a la vez
            self._open_stream()

    def _open_stream(self) -> None:
        if self._stream is not None or self._silent:
            return
        try:
            import sounddevice as sd
            self._stream = sd.OutputStream(samplerate=synth.SAMPLE_RATE, channels=1, dtype="float32",
                                           callback=self._callback, blocksize=1024, latency="low")
            self._stream.start()
            self._latency = float(self._stream.latency or 0.0)
        except Exception as exc:
            log.warning("Sin salida de audio: %s", exc)
            self._silent = True
            self._stream = None
            self.message = "No hay dispositivo de audio disponible: la reproducción será silenciosa."

    def _callback(self, outdata, frames, time_info, status) -> None:  # hilo de audio
        with self._lock:
            if not self._playing or not self._stems:
                outdata.fill(0)
                return
            pos = self._pos
            end = pos + frames
            out = np.zeros(frames, dtype=np.float32)
            loop_end = int(self.loop[1] / self.speed * synth.SAMPLE_RATE) if self.loop else None
            loop_start = int(self.loop[0] / self.speed * synth.SAMPLE_RATE) if self.loop else None
            written = 0
            while written < frames:
                limit = len(next(iter(self._stems.values())))
                chunk_end = min(pos + (frames - written), limit)
                if loop_end is not None and pos < loop_end:
                    chunk_end = min(chunk_end, loop_end)
                n = chunk_end - pos
                if n > 0:
                    for key, data in self._stems.items():
                        if key in self.muted:
                            continue
                        out[written:written + n] += data[pos:chunk_end]
                    written += n
                    pos = chunk_end
                if loop_end is not None and pos >= loop_end:
                    pos = loop_start
                    continue
                if pos >= limit:
                    self._playing = False
                    self._finished = True
                    break
                if n <= 0:
                    break
            self._pos = pos
        outdata[:, 0] = out * self.volume

    # ------------------------------------------------------------------ MIDI de Windows
    def _prepare_midi(self) -> None:
        self._stop_midi_thread()
        if self._midi is None:
            try:
                import rtmidi
                out = rtmidi.MidiOut()
                ports = out.get_ports()
                if not ports:
                    raise RuntimeError("no hay puertos MIDI de salida")
                idx = next((i for i, p in enumerate(ports) if "GS Wavetable" in p), 0)
                out.open_port(idx)
                self._midi = out
                self.message = ""
            except Exception as exc:
                self.message = f"Sintetizador MIDI no disponible ({exc}); se usa el sintetizador interno."
                self.backend = "internal"
                self._prepare(self.position(), False)
                return
        events = []
        perf = self.perf
        channels = {"R": 0, "L": 1, "otros": 2, "acordes": 3}
        for ch in channels.values():
            self._midi.send_message([0xC0 | ch, 0])
        for n in perf.notes:
            if n.silent:
                continue
            key = synth.stem_key(n.hand, n.part, n.track)
            ch = channels.get(key, 2)
            events.append((n.start_s, 1, ch, n.midi, n.velocity, key))
            events.append((max(n.start_s + 0.01, n.end_s - 0.002), 0, ch, n.midi, 0, key))
        for q, value, part in perf.pedal_events:
            s = perf.q_to_s(q)
            for ch in (0, 1, 2):
                events.append((s, 2, ch, 64, value, ""))
        events.sort(key=lambda e: (e[0], e[1]))
        self._midi_events = events
        self._midi_times = [e[0] for e in events]

    def _midi_loop(self, start_pos: float) -> None:
        stop = self._midi_stop  # cada hilo conserva su propio aviso de parada
        idx = bisect.bisect_left(self._midi_times, start_pos)
        wall0 = time.monotonic()
        self._midi_pos_ref = (wall0, start_pos)
        while not stop.is_set():
            now = start_pos + (time.monotonic() - wall0) * self.speed
            if self.loop and now >= self.loop[1]:
                self._all_notes_off()
                start_pos = self.loop[0]
                wall0 = time.monotonic()
                self._midi_pos_ref = (wall0, start_pos)
                idx = bisect.bisect_left(self._midi_times, start_pos)
                continue
            while idx < len(self._midi_events) and self._midi_events[idx][0] <= now:
                t, kind, ch, note, vel, key = self._midi_events[idx]
                idx += 1
                try:
                    if kind == 1:
                        if key in self.muted:
                            continue
                        v = max(1, min(127, int(vel * self.volume)))
                        self._midi.send_message([0x90 | ch, note, v])
                        self._midi_active.add((ch, note))
                    elif kind == 0:
                        if (ch, note) in self._midi_active:
                            self._midi.send_message([0x80 | ch, note, 0])
                            self._midi_active.discard((ch, note))
                    else:
                        self._midi.send_message([0xB0 | ch, 64, vel])
                except Exception:
                    pass
            if idx >= len(self._midi_events) and now > (self.perf.total_seconds if self.perf else 0) + 0.5:
                self._playing = False
                self._finished = True
                self._clock.start_pos = self.perf.total_seconds if self.perf else 0.0
                break
            time.sleep(0.002)
        self._all_notes_off()

    def _all_notes_off(self) -> None:
        if self._midi is None:
            return
        try:
            for ch, note in list(self._midi_active):
                self._midi.send_message([0x80 | ch, note, 0])
            for ch in range(4):
                self._midi.send_message([0xB0 | ch, 64, 0])
                self._midi.send_message([0xB0 | ch, 123, 0])
        except Exception:
            pass
        self._midi_active.clear()

    def _stop_midi_thread(self) -> None:
        if self._midi_thread is not None:
            self._midi_stop.set()
            self._midi_thread.join(timeout=1.0)
            self._midi_thread = None
            # nuevo aviso: si el hilo anterior tardó en salir, el suyo sigue activado y terminará
            self._midi_stop = threading.Event()

    # ------------------------------------------------------------------ control
    def play(self) -> None:
        if self.perf is None:
            return
        if self._finished or self.position() >= self.duration() - 0.05:
            self.seek(self.loop[0] if self.loop else 0.0)
        self._finished = False
        if self.backend == "windows_midi" and self._midi is not None:
            pos = self._clock.start_pos
            self._stop_midi_thread()
            self._playing = True
            self._midi_thread = threading.Thread(target=self._midi_loop, args=(pos,), daemon=True)
            self._midi_thread.start()
            return
        if not self.ready:
            return
        self._ensure_stream()
        if self._silent:
            self._clock.start(self.position() / self.speed)
        with self._lock:
            self._playing = True

    def pause(self) -> None:
        if not self.ready and self.backend == "internal" and self.perf is not None:
            # se está generando el audio: que no arranque solo al terminar
            self._render_token += 1
            self._prepare(self.position(), False)
        if self.backend == "windows_midi" and self._midi_thread is not None:
            pos = self.position()
            self._stop_midi_thread()
            self._clock.start_pos = pos
            self._playing = False
            return
        if self._silent and self._clock.running:
            self._clock.stop()
        with self._lock:
            self._playing = False

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self.pause()
        self.seek(self.loop[0] if self.loop else 0.0)

    def seek(self, perf_seconds: float) -> None:
        perf_seconds = max(0.0, min(perf_seconds, self.duration()))
        if self.backend == "windows_midi":
            playing = self._playing
            if playing:
                self._stop_midi_thread()
            self._clock.start_pos = perf_seconds
            self._finished = False
            if playing:
                self._playing = True
                self._midi_thread = threading.Thread(target=self._midi_loop, args=(perf_seconds,), daemon=True)
                self._midi_thread.start()
            return
        with self._lock:
            self._pos = int(perf_seconds / self.speed * synth.SAMPLE_RATE)
            self._finished = False
        if self._silent:
            running = self._clock.running
            self._clock.start_pos = perf_seconds / self.speed
            if running:
                self._clock.start(perf_seconds / self.speed)

    def set_loop(self, start_s: float | None, end_s: float | None) -> None:
        if start_s is None or end_s is None or end_s - start_s < 0.05:
            self.loop = None
        else:
            self.loop = (start_s, end_s)

    def set_muted(self, stem: str, muted: bool) -> None:
        if muted:
            self.muted.add(stem)
        else:
            self.muted.discard(stem)

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.5, float(volume)))

    @property
    def is_playing(self) -> bool:
        return self._playing

    def duration(self) -> float:
        return self.perf.total_seconds if self.perf else 0.0

    def position(self) -> float:
        if self.backend == "windows_midi":
            if self._playing and self._midi_thread is not None:
                wall0, pos0 = self._midi_pos_ref
                return min(self.duration(), pos0 + (time.monotonic() - wall0) * self.speed)
            return self._clock.start_pos
        if self._silent:
            pos = self._clock.now() * self.speed
            if self.loop and pos >= self.loop[1]:
                self._clock.start(self.loop[0] / self.speed)
                pos = self.loop[0]
            if pos >= self.duration():
                self._clock.stop()
                self._playing = False
                self._finished = True
                pos = self.duration()
            return pos
        with self._lock:
            pos = self._pos / synth.SAMPLE_RATE
        if self._playing:
            pos = max(0.0, pos - self._latency)
        return min(self.duration(), pos * self.speed)

    def recover(self) -> None:
        """Reabre la salida de sonido conservando la posición (si la reproducción se atascó)."""
        pos = self.position()
        log.warning("La reproducción no avanzaba en %.2f s: se reabre la salida de sonido.", pos)
        self._render_token += 1
        with self._lock:
            self._playing = False
        self._close_outputs()
        self._silent = False
        self._prepare(pos, True)

    def state(self) -> PlayerState:
        return PlayerState(self._playing, self.position(), self.duration(), self.speed, self.message)

    def _close_outputs(self) -> None:
        self._stop_midi_thread()
        with self._stream_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        if self._midi is not None:
            self._all_notes_off()
            try:
                self._midi.close_port()
            except Exception:
                pass
            self._midi = None

    def close(self) -> None:
        self._render_token += 1
        self.pause()
        self._close_outputs()
