"""Proyecto de conversión: todo lo necesario para continuar más tarde.

Un proyecto (``.ncsproj``) es un ZIP con:

* ``project.json``: origen, opciones, páginas, ejecuciones de motores,
  partitura actual (con correcciones), partitura original reconocida,
  avisos y decisiones del usuario, opciones de exportación;
* ``paginas/``: imagen original y procesada de cada página (el proyecto no
  depende de que el archivo original siga en la misma ruta);
* ``motores/``: MusicXML y registros producidos por cada motor.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from note_cascade import __version__
from note_cascade.app import paths
from note_cascade.app.errors import CorruptFileError
from note_cascade.image_processing.staff_detection import PageGeometry
from note_cascade.layout.text_classifier import TextElement
from note_cascade.music_model.model import Score
from note_cascade.music_validation.issues import Issue

log = logging.getLogger(__name__)

FORMAT_VERSION = 1


@dataclass
class PageRecord:
    index: int
    label: str
    kind_hint: str = ""
    quality_kind: str = ""
    quality: dict = field(default_factory=dict)
    dpi: float | None = None
    original_file: str = ""
    processed_file: str = ""
    matrix: list[list[float]] = field(default_factory=lambda: np.eye(3).tolist())
    original_size: tuple[int, int] = (0, 0)
    processed_size: tuple[int, int] = (0, 0)
    geometry: dict = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    text: list[dict] = field(default_factory=list)
    text_origin: str = ""
    has_music: bool = True
    processing_level: str = "auto"

    def text_elements(self) -> list[TextElement]:
        return [TextElement.from_dict(d) for d in self.text]

    def geometry_obj(self) -> PageGeometry | None:
        return PageGeometry.from_dict(self.geometry) if self.geometry else None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["original_size"] = list(self.original_size)
        d["processed_size"] = list(self.processed_size)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PageRecord":
        rec = cls(int(d["index"]), d.get("label", ""))
        for k, v in d.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
        rec.original_size = tuple(d.get("original_size", (0, 0)))
        rec.processed_size = tuple(d.get("processed_size", (0, 0)))
        return rec


@dataclass
class EngineRunRecord:
    engine_id: str
    engine_name: str
    version: str
    role: str                 # principal | verificación | respaldo
    ok: bool
    message: str = ""
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    musicxml_files: list[str] = field(default_factory=list)
    log_file: str = ""
    pages_ok: list[int] = field(default_factory=list)
    pages_failed: list[int] = field(default_factory=list)
    positions: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "EngineRunRecord":
        rec = cls(d["engine_id"], d.get("engine_name", d["engine_id"]), d.get("version", ""), d.get("role", ""),
                  bool(d.get("ok")))
        for k, v in d.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
        return rec


@dataclass
class Project:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "partitura"
    created: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    modified: str = ""
    app_version: str = __version__
    source_path: str = ""
    source_kind: str = ""
    source_sha256: str = ""
    workspace: Path = field(default_factory=lambda: Path())
    options: dict[str, Any] = field(default_factory=dict)
    pages: list[PageRecord] = field(default_factory=list)
    engine_runs: list[EngineRunRecord] = field(default_factory=list)
    score: Score = field(default_factory=Score)
    original_score: Score | None = None
    alternative_scores: dict[str, Score] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    decisions: dict[str, str] = field(default_factory=dict)
    export_settings: dict[str, Any] = field(default_factory=dict)
    exports: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    project_file: str = ""
    dirty: bool = False

    # ------------------------------------------------------------------
    @classmethod
    def new_workspace(cls, name: str) -> "Project":
        p = cls(name=name)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "partitura"
        p.workspace = paths.work_dir() / f"{stamp}_{safe}_{p.id[:6]}"
        (p.workspace / "paginas").mkdir(parents=True, exist_ok=True)
        (p.workspace / "motores").mkdir(parents=True, exist_ok=True)
        return p

    def page_path(self, rel: str) -> Path:
        return self.workspace / rel

    def add_log(self, text: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{stamp}] {text}")

    def engine_versions(self) -> dict[str, str]:
        return {r.engine_name: r.version for r in self.engine_runs}

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "format": FORMAT_VERSION, "id": self.id, "name": self.name, "created": self.created,
            "modified": dt.datetime.now().isoformat(timespec="seconds"), "app_version": self.app_version,
            "source_path": self.source_path, "source_kind": self.source_kind, "source_sha256": self.source_sha256,
            "options": self.options, "pages": [p.to_dict() for p in self.pages],
            "engine_runs": [r.to_dict() for r in self.engine_runs], "score": self.score.to_dict(),
            "original_score": self.original_score.to_dict() if self.original_score else None,
            "alternative_scores": {k: v.to_dict() for k, v in self.alternative_scores.items()},
            "issues": [i.to_dict() for i in self.issues], "decisions": self.decisions,
            "export_settings": self.export_settings, "exports": self.exports, "log": self.log[-500:],
        }

    def save(self, path: Path | str) -> Path:
        """Guarda el proyecto completo (incluye imágenes y resultados de los motores)."""
        path = Path(path)
        if path.suffix.lower() != ".ncsproj":
            path = path.with_suffix(".ncsproj")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        data = json.dumps(self.to_dict(), ensure_ascii=False, indent=1)
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", data)
            if self.workspace and self.workspace.exists():
                for f in self.workspace.rglob("*"):
                    if f.is_file() and f.suffix.lower() not in (".tmp",) and f.stat().st_size < 200_000_000:
                        rel = f.relative_to(self.workspace).as_posix()
                        if rel.startswith("motores/") and f.suffix.lower() in (".tif",):
                            continue  # entradas temporales de los motores: se pueden regenerar
                        z.write(f, rel)
        tmp.replace(path)
        self.project_file = str(path)
        self.dirty = False
        return path

    def save_autosave(self) -> Path:
        target = paths.autosave_dir() / f"{self.id}.json"
        tmp = target.with_suffix(".tmp")
        d = self.to_dict()
        d["workspace"] = str(self.workspace)
        d["project_file"] = self.project_file
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        return target

    @classmethod
    def from_dict(cls, d: dict, workspace: Path) -> "Project":
        p = cls(id=d.get("id", uuid.uuid4().hex[:12]), name=d.get("name", "partitura"), created=d.get("created", ""))
        p.modified = d.get("modified", "")
        p.app_version = d.get("app_version", "")
        p.source_path = d.get("source_path", "")
        p.source_kind = d.get("source_kind", "")
        p.source_sha256 = d.get("source_sha256", "")
        p.workspace = workspace
        p.options = d.get("options", {})
        p.pages = [PageRecord.from_dict(x) for x in d.get("pages", [])]
        p.engine_runs = [EngineRunRecord.from_dict(x) for x in d.get("engine_runs", [])]
        p.score = Score.from_dict(d.get("score", {}))
        p.original_score = Score.from_dict(d["original_score"]) if d.get("original_score") else None
        p.alternative_scores = {k: Score.from_dict(v) for k, v in d.get("alternative_scores", {}).items()}
        p.issues = [Issue.from_dict(x) for x in d.get("issues", [])]
        p.decisions = dict(d.get("decisions", {}))
        p.export_settings = d.get("export_settings", {})
        p.exports = d.get("exports", [])
        p.log = d.get("log", [])
        return p

    @classmethod
    def load(cls, path: Path | str) -> "Project":
        path = Path(path)
        try:
            with zipfile.ZipFile(path) as z:
                data = json.loads(z.read("project.json").decode("utf-8"))
                p_id = data.get("id", uuid.uuid4().hex[:12])
                workspace = paths.work_dir() / f"proyecto_{p_id}"
                if workspace.exists():
                    shutil.rmtree(workspace, ignore_errors=True)
                workspace.mkdir(parents=True, exist_ok=True)
                for info in z.infolist():
                    if info.filename == "project.json" or info.is_dir():
                        continue
                    target = (workspace / info.filename).resolve()
                    if workspace.resolve() not in target.parents:
                        continue  # protección contra rutas maliciosas en el ZIP
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise CorruptFileError(f"El proyecto «{path.name}» está dañado o no es un proyecto de Note Cascade Studio.",
                                   details=str(exc)) from exc
        if data.get("format", 1) > FORMAT_VERSION:
            raise CorruptFileError("El proyecto se creó con una versión más nueva de la aplicación.",
                                   "Actualiza Note Cascade Studio para abrirlo.")
        p = cls.from_dict(data, workspace)
        p.project_file = str(path)
        return p


def list_autosaves() -> list[tuple[Path, dict]]:
    result = []
    for f in sorted(paths.autosave_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            result.append((f, d))
        except (OSError, ValueError):
            continue
    return result


def restore_autosave(path: Path) -> Project:
    d = json.loads(path.read_text(encoding="utf-8"))
    ws = Path(d.get("workspace", "")) if d.get("workspace") else Path(tempfile.mkdtemp())
    p = Project.from_dict(d, ws)
    p.project_file = d.get("project_file", "")
    p.dirty = True
    return p


def workspace_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def clean_work_dirs(keep: set[Path] | None = None) -> int:
    """Borra las carpetas de trabajo temporales (no los proyectos guardados). Devuelve bytes liberados."""
    keep = {k.resolve() for k in (keep or set())}
    freed = 0
    for d in paths.work_dir().iterdir():
        if d.is_dir() and d.resolve() not in keep:
            freed += workspace_size(d)
            shutil.rmtree(d, ignore_errors=True)
    return freed
