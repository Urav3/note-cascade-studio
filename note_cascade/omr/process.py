"""Ejecución de procesos externos con lectura en vivo, tiempo límite y cancelación."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from note_cascade.app.errors import CancelledError
from note_cascade.omr.base import CancelToken

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class ProcessTimeout(Exception):
    pass


def run_process(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None,
                timeout_s: float = 600.0, cancel: CancelToken | None = None,
                on_line: Callable[[str], None] | None = None, log_file: Path | None = None,
                tail_lines: int = 80) -> tuple[int, list[str]]:
    """Ejecuta ``cmd`` y devuelve (código de salida, últimas líneas de salida)."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    log.info("Ejecutando: %s", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None, env=full_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            lines.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        lines.put(None)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    tail: deque[str] = deque(maxlen=tail_lines)
    fh = log_file.open("w", encoding="utf-8") if log_file else None
    start = time.monotonic()
    finished_reading = False
    try:
        while True:
            if cancel is not None and cancel.cancelled:
                _kill(proc)
                raise CancelledError()
            if time.monotonic() - start > timeout_s:
                _kill(proc)
                raise ProcessTimeout(f"El proceso superó el tiempo límite de {timeout_s:.0f} s.")
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                if finished_reading and proc.poll() is not None:
                    break
                continue
            if line is None:
                finished_reading = True
                if proc.poll() is not None:
                    break
                continue
            tail.append(line)
            if fh:
                fh.write(line + "\n")
            if on_line:
                try:
                    on_line(line)
                except Exception:  # un fallo del callback no debe matar el proceso
                    log.exception("Error en el procesado de la salida")
        code = proc.wait(timeout=30)
    finally:
        if fh:
            fh.close()
        if proc.poll() is None:
            _kill(proc)
    return code, list(tail)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=15)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
