"""Ejecución de tareas pesadas fuera del hilo de la interfaz."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from note_cascade.app.errors import CancelledError, NoteCascadeError
from note_cascade.omr.base import CancelToken

log = logging.getLogger(__name__)


class TaskSignals(QObject):
    progress = Signal(float, str)
    message = Signal(str)
    finished = Signal(object)
    failed = Signal(str, str)   # mensaje para el usuario, detalles técnicos
    cancelled = Signal()


class _Worker(QObject):
    def __init__(self, fn: Callable[..., Any], signals: TaskSignals, cancel: CancelToken) -> None:
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.cancel = cancel

    def run(self) -> None:
        try:
            result = self.fn(self.signals.progress.emit, self.signals.message.emit, self.cancel)
        except CancelledError:
            self.signals.cancelled.emit()
            return
        except NoteCascadeError as exc:
            log.warning("Tarea fallida: %s", exc.message)
            self.signals.failed.emit(exc.user_text(), exc.details or traceback.format_exc())
            return
        except Exception as exc:  # error inesperado: se informa sin cerrar la aplicación
            log.exception("Error inesperado en una tarea")
            self.signals.failed.emit(f"Ocurrió un error inesperado: {exc}",
                                     traceback.format_exc())
            return
        self.signals.finished.emit(result)


class Task(QObject):
    """Tarea cancelable en su propio QThread.

    ``fn(progress, message, cancel)`` se ejecuta en segundo plano; las señales
    llegan al hilo de la interfaz.
    """

    def __init__(self, fn: Callable[..., Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.signals = TaskSignals()
        self.cancel_token = CancelToken()
        self.thread = QThread()
        self.worker = _Worker(fn, self.signals, self.cancel_token)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        for sig in (self.signals.finished, self.signals.failed, self.signals.cancelled):
            sig.connect(self._cleanup)

    def start(self) -> "Task":
        self.thread.start()
        return self

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def is_running(self) -> bool:
        return self.thread.isRunning()

    def _cleanup(self, *args) -> None:
        self.thread.quit()

    def wait(self, ms: int = 5000) -> bool:
        return self.thread.wait(ms)
