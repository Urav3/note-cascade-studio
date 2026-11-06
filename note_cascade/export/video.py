"""Exportación opcional del visualizador en cascada a vídeo MP4.

Se dibuja cada fotograma con el mismo ``WaterfallRenderer`` que usa la
pantalla, se mezcla el audio del sintetizador interno y se codifica con el
FFmpeg que incluye el paquete «imageio-ffmpeg». Si ese paquete no está
instalado, la función lo indica y el resto de la exportación no se ve
afectado.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def export_video(project, perf, path: Path, cfg, options: dict | None = None, progress=None) -> tuple[bool, str]:
    exe = ffmpeg_exe()
    if exe is None:
        return False, ("La exportación de vídeo necesita el paquete opcional «imageio-ffmpeg» "
                       "(se instala con install_windows.bat).")
    options = options or {}
    width = int(options.get("width", 1280))
    height = int(options.get("height", 720))
    fps = int(options.get("fps", 30))
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from note_cascade.piano_roll.waterfall import WaterfallRenderer
    from note_cascade.player import synth

    if QApplication.instance() is None:  # uso desde la línea de órdenes
        # Plataforma normal de Windows (sin mostrar ventanas): la «offscreen» no tiene fuentes del sistema.
        QApplication(sys.argv[:1])
    renderer = WaterfallRenderer()
    renderer.set_performance(perf, [m.number for m in project.score.measures])
    renderer.title = project.score.title or project.name
    duration = perf.total_seconds + 1.5
    n_frames = int(duration * fps)
    stems, _ = synth.render_stems(perf, 1.0, cfg.playback.timbre if cfg.playback.timbre in synth.TIMBRES else "piano")
    audio = synth.mixdown(stems)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        synth.write_wav(wav, audio)
        cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
               "-r", str(fps), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
               "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)]
        flags = 0x08000000 if sys.platform == "win32" else 0
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        img = QImage(width, height, QImage.Format_RGBA8888)
        try:
            for i in range(n_frames):
                t = i / fps
                img.fill(0)
                painter = QPainter(img)
                renderer.paint(painter, QRectF(0, 0, width, height), t)
                painter.end()
                ptr = img.constBits()
                proc.stdin.write(bytes(ptr)[: width * height * 4])
                if progress and i % fps == 0:
                    progress(i / max(1, n_frames), f"Vídeo: {int(t)} s de {int(duration)} s")
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "replace")
            code = proc.wait(timeout=600)
        except (BrokenPipeError, OSError) as exc:
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else str(exc)
            proc.kill()
            return False, f"FFmpeg no pudo crear el vídeo: {err[-300:]}"
    if code != 0 or not path.exists() or path.stat().st_size < 1000:
        return False, f"FFmpeg no pudo crear el vídeo: {err[-300:]}"
    return True, f"Vídeo creado ({width}×{height}, {fps} fps, {duration / 60:.1f} min)."
