"""Escucha breve de una nota o acorde (para identificarla al revisar)."""

from __future__ import annotations

import logging

import numpy as np

from note_cascade.player import synth

log = logging.getLogger(__name__)


def play_notes(midis: list[int], duration_s: float = 0.9, velocity: int = 90, timbre: str = "piano") -> bool:
    if not midis:
        return False
    try:
        import sounddevice as sd
    except Exception:
        return False
    n = int(duration_s * synth.SAMPLE_RATE)
    release = int(0.15 * synth.SAMPLE_RATE)
    out = np.zeros(n + release, dtype=np.float32)
    for m in midis[:8]:
        s = synth.get_sample(int(m), velocity, timbre)[: n + release].copy()
        if len(s) > n:
            s[n:] *= np.linspace(1, 0, len(s) - n, dtype=np.float32)
        out[: len(s)] += s
    peak = float(np.abs(out).max()) or 1.0
    out = out / max(peak, 1.0) * 0.8
    try:
        sd.play(out, synth.SAMPLE_RATE, blocking=False)
        return True
    except Exception as exc:
        log.info("No se pudo reproducir la nota: %s", exc)
        return False


def play_segment(score, m_from: int, m_to: int, timbre: str = "piano", max_seconds: float = 20.0) -> bool:
    """Toca un fragmento de una partitura (compases ``m_from``–``m_to``) sin pasar por el reproductor
    principal: sirve para escuchar el antes y el después de una corrección."""
    from note_cascade.music_model.timeline import PerformanceOptions, build_performance
    try:
        import sounddevice as sd
    except Exception:
        return False
    perf = build_performance(score, PerformanceOptions(expand_repeats=False))
    idx = [i for i, pm in enumerate(perf.play_measures) if m_from <= pm.measure <= m_to]
    if not idx:
        return False
    t0 = perf.measure_start_s(idx[0])
    t1 = min(perf.measure_end_s(idx[-1]), t0 + max_seconds)
    release = 0.25
    out = np.zeros(int((t1 - t0 + release) * synth.SAMPLE_RATE) + 1, dtype=np.float32)
    for n in perf.notes:
        if n.silent or n.end_s <= t0 or n.start_s >= t1:
            continue
        a = int(max(0.0, n.start_s - t0) * synth.SAMPLE_RATE)
        hold = int((min(n.end_s, t1) - max(n.start_s, t0)) * synth.SAMPLE_RATE)
        tail = int(release * synth.SAMPLE_RATE)
        s = synth.get_sample(int(n.midi), n.velocity, timbre)[: hold + tail].copy()
        if len(s) > hold:
            s[hold:] *= np.linspace(1, 0, len(s) - hold, dtype=np.float32)
        s *= (n.velocity / 127.0) ** 1.4
        end = min(len(out), a + len(s))
        out[a:end] += s[: end - a]
    peak = float(np.abs(out).max()) or 1.0
    out = out / max(peak, 1.0) * 0.8
    try:
        sd.stop()
        sd.play(out, synth.SAMPLE_RATE, blocking=False)
        return True
    except Exception as exc:
        log.info("No se pudo reproducir el fragmento: %s", exc)
        return False
