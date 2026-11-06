"""Interfaz común de los motores de reconocimiento óptico de música (OMR)."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from note_cascade.app.errors import CancelledError

ProgressFn = Callable[[float, str], None]


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError()


@dataclass
class EngineStatus:
    id: str
    name: str
    available: bool
    version: str = ""
    detail: str = ""
    location: str = ""
    license: str = ""
    suggestion: str = ""
    models_ready: bool = True

    def summary(self) -> str:
        if self.available:
            return f"{self.name} {self.version}".strip() + " — disponible"
        return f"{self.name} — no disponible: {self.detail}"


@dataclass
class EnginePageInput:
    page_index: int
    image_path: Path
    width: int
    height: int


@dataclass
class EngineRequest:
    pages: list[EnginePageInput]
    work_dir: Path
    book_name: str
    timeout_s: float = 600.0
    progress: ProgressFn | None = None
    cancel: CancelToken = field(default_factory=CancelToken)

    def report(self, fraction: float, message: str) -> None:
        if self.progress:
            self.progress(max(0.0, min(1.0, fraction)), message)


@dataclass
class EnginePageResult:
    page_index: int
    ok: bool
    musicxml_path: Path | None = None
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineResult:
    engine_id: str
    engine_name: str
    version: str
    ok: bool
    musicxml_paths: list[Path] = field(default_factory=list)
    page_results: list[EnginePageResult] = field(default_factory=list)
    per_page_files: bool = False         # True si hay un MusicXML por página
    log_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    duration_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def failed_pages(self) -> list[int]:
        return [p.page_index for p in self.page_results if not p.ok]


class OMREngine(ABC):
    id: str = ""
    name: str = ""
    license: str = ""
    description: str = ""
    strengths: str = ""
    weaknesses: str = ""

    @abstractmethod
    def status(self, refresh: bool = False) -> EngineStatus:
        ...

    @abstractmethod
    def recognize(self, request: EngineRequest) -> EngineResult:
        ...
