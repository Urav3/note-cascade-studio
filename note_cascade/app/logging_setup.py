"""Configuración del registro de eventos (archivo rotativo + consola)."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from note_cascade.app import paths

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            paths.logs_dir() / "note_cascade.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.setLevel(logging.WARNING)
        root.addHandler(console)
    for noisy in ("PIL", "matplotlib", "urllib3", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
