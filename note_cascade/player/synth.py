"""Sintetizador interno (sin archivos externos ni conexión).

Genera un timbre de piano aproximado por síntesis aditiva: parciales
ligeramente inarmónicos con decaimiento exponencial dependiente de la altura y
de la intensidad, ataque corto y apagado amortiguado al soltar la tecla (o al
levantar el pedal). Cada nota usa una muestra cacheada por (altura, capa de
intensidad), así que volver a generar el audio al cambiar la velocidad de
reproducción es rápido.

Se generan pistas separadas (mano derecha, mano izquierda, resto) para poder
silenciar cada mano al instante sin regenerar el audio.
"""

from __future__ import annotations

import bisect

import math
import threading
from collections import defaultdict

import numpy as np

from note_cascade.music_model.timeline import Performance

SAMPLE_RATE = 44100
TIMBRES = {
    "piano": "Piano (interno)",
    "piano_suave": "Piano suave (interno)",
    "electrico": "Piano eléctrico (interno)",
    "organo": "Órgano (interno)",
}
_cache: dict[tuple, np.ndarray] = {}
_cache_lock = threading.Lock()


def _sample_length(midi: int, timbre: str) -> float:
    if timbre == "organo":
        return 4.0
    return float(np.interp(midi, [21, 60, 108], [7.0, 4.5, 1.6]))


def _make_sample(midi: int, layer: int, timbre: str) -> np.ndarray:
    f0 = 440.0 * 2 ** ((midi - 69) / 12)
    vel = (layer + 0.5) / 8.0
    length = _sample_length(midi, timbre)
    n = int(length * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    out = np.zeros(n, dtype=np.float32)
    nyq = SAMPLE_RATE / 2 - 1000
    if timbre == "organo":
        for k, amp in ((1, 1.0), (2, 0.5), (3, 0.25), (4, 0.18), (6, 0.08), (8, 0.05)):
            fk = f0 * k
            if fk < nyq:
                out += amp * np.sin(2 * np.pi * fk * t, dtype=np.float32)
        env = np.minimum(1.0, t / 0.02)
        out *= env.astype(np.float32) * 0.25
        return out
    if timbre == "electrico":
        mod = np.sin(2 * np.pi * f0 * 1.0 * t) * (2.2 * vel) * np.exp(-t * 2.5)
        out = np.sin(2 * np.pi * f0 * t + mod).astype(np.float32)
        out += 0.25 * np.sin(2 * np.pi * f0 * 2 * t).astype(np.float32) * np.exp(-t * 1.5).astype(np.float32)
        decay = float(np.interp(midi, [21, 108], [0.6, 2.4]))
        env = np.minimum(1.0, t / 0.003) * np.exp(-t * decay)
        return (out * env.astype(np.float32) * 0.45).astype(np.float32)
    # Piano
    brightness = 0.35 + 0.65 * vel if timbre == "piano" else 0.2 + 0.4 * vel
    B = 0.00008 * (1 + max(0, midi - 60) / 30)  # inarmonicidad
    base_decay = float(np.interp(midi, [21, 48, 72, 108], [0.45, 0.7, 1.1, 2.6]))
    n_partials = 14 if timbre == "piano" else 8
    for k in range(1, n_partials + 1):
        fk = k * f0 * math.sqrt(1 + B * k * k)
        if fk >= nyq:
            break
        amp = (1.0 / k ** (1.6 - 0.7 * brightness)) * (1.0 if k == 1 else brightness)
        if k == 2:
            amp *= 0.9
        decay = base_decay * (1 + 0.35 * (k - 1))
        phase = (k * 1.7) % (2 * np.pi)
        out += (amp * np.exp(-decay * t) * np.sin(2 * np.pi * fk * t + phase)).astype(np.float32)
    # Ataque del macillo: ruido breve filtrado
    hammer_n = int(0.012 * SAMPLE_RATE)
    rng = np.random.default_rng(midi * 31 + layer)
    noise = rng.standard_normal(hammer_n).astype(np.float32)
    noise = np.convolve(noise, np.ones(6, dtype=np.float32) / 6, mode="same")
    out[:hammer_n] += noise * np.linspace(0.08 * vel, 0, hammer_n, dtype=np.float32)
    attack = np.minimum(1.0, t / 0.004).astype(np.float32)
    out *= attack
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.5).astype(np.float32)


def get_sample(midi: int, velocity: int, timbre: str) -> np.ndarray:
    layer = max(0, min(7, velocity * 8 // 128))
    key = (midi, layer, timbre)
    with _cache_lock:
        s = _cache.get(key)
    if s is None:
        s = _make_sample(midi, layer, timbre)
        with _cache_lock:
            _cache[key] = s
    return s


def stem_key(hand: str | None, part: int, track: str = "") -> str:
    if track == "acordes":
        return "acordes"
    if hand in ("R", "L"):
        return hand
    return "otros"


def render_stems(perf: Performance, speed: float = 1.0, timbre: str = "piano",
                 progress=None, cancel=None) -> tuple[dict[str, np.ndarray], float]:
    """Genera el audio de cada pista. Devuelve (pistas, duración en segundos reproducidos)."""
    speed = max(0.1, float(speed))
    total_s = perf.total_seconds / speed + 2.5
    n_total = int(total_s * SAMPLE_RATE)
    stems: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(n_total, dtype=np.float32))
    pedal_ups = _pedal_release_times(perf)
    release = int(0.12 * SAMPLE_RATE)
    count = len(perf.notes)
    # cada tecla deja de sonar cuando se vuelve a pulsar (el pedal no la prolonga por encima del nuevo golpe)
    strikes: dict[tuple[int, int], list[float]] = defaultdict(list)
    for n in perf.notes:
        if not n.silent:
            strikes[(n.part, n.midi)].append(n.start_s)
    for v in strikes.values():
        v.sort()
    for i, n in enumerate(perf.notes):
        if cancel is not None and cancel():
            break
        if progress and i % 200 == 0:
            progress(i / max(1, count))
        if n.silent:
            continue  # unísono con la otra mano: la tecla ya suena
        start_s = n.start_s / speed
        end_s = n.end_s / speed
        up = _pedal_up_after(pedal_ups.get(n.part, []), n.end_s)
        if up is not None:
            end_s = max(end_s, up / speed)
        times = strikes[(n.part, n.midi)]
        k = bisect.bisect_right(times, n.start_s)
        if k < len(times):
            end_s = min(end_s, times[k] / speed)
        a = int(start_s * SAMPLE_RATE)
        if a >= n_total:
            continue
        hold = max(1, int((end_s - start_s) * SAMPLE_RATE))
        sample = get_sample(n.midi, n.velocity, timbre)
        gain = (n.velocity / 127.0) ** 1.4
        seg_len = min(len(sample), hold + release, n_total - a)
        seg = sample[:seg_len] * gain
        if hold < seg_len:
            r = seg_len - hold
            seg = seg.copy()
            seg[hold:] *= np.linspace(1.0, 0.0, r, dtype=np.float32)
        stems[stem_key(n.hand, n.part, n.track)][a:a + seg_len] += seg
    result = dict(stems)
    if not result:
        result["R"] = np.zeros(n_total, dtype=np.float32)
    # Normalización conjunta para evitar saturación
    mix_peak = float(np.max(np.abs(sum(result.values())))) if result else 1.0
    if mix_peak > 0.95:
        k = 0.95 / mix_peak
        for key in result:
            result[key] *= k
    return result, total_s


def _pedal_release_times(perf: Performance) -> dict[int, list[tuple[float, float]]]:
    """Intervalos (inicio, fin) en segundos de interpretación con el pedal pisado, por parte."""
    spans: dict[int, list[tuple[float, float]]] = defaultdict(list)
    down: dict[int, float] = {}
    for q, value, part in perf.pedal_events:
        s = perf.q_to_s(q)
        if value >= 64 and part not in down:
            down[part] = s
        elif value < 64 and part in down:
            spans[part].append((down.pop(part), s))
    for part, s in down.items():
        spans[part].append((s, perf.total_seconds + 5))
    return spans


def _pedal_up_after(spans: list[tuple[float, float]], t: float) -> float | None:
    for a, b in spans:
        if a <= t < b:
            return b
    return None


def mixdown(stems: dict[str, np.ndarray], gains: dict[str, float] | None = None) -> np.ndarray:
    gains = gains or {}
    out = None
    for k, v in stems.items():
        g = gains.get(k, 1.0)
        if g == 0:
            continue
        out = v * g if out is None else out + v * g
    if out is None:
        out = np.zeros(1, dtype=np.float32)
    return out


def write_wav(path, audio: np.ndarray) -> None:
    import wave
    data = np.clip(audio, -1.0, 1.0)
    pcm = (data * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
