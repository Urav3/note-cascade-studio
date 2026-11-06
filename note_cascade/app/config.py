"""Configuración persistente de la aplicación (JSON en la carpeta del usuario)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from note_cascade.app import paths

log = logging.getLogger(__name__)


@dataclass
class ProcessingSettings:
    # "auto" elige 300 ppp y lo sube si la partitura es densa; o un valor fijo.
    dpi: str = "auto"
    # Nivel de corrección de imagen: auto | ninguna | suave | fuerte
    image_correction: str = "auto"
    # Motor OMR: auto | audiveris | homr | oemer
    engine: str = "auto"
    # Segundo motor para verificación cruzada: auto | siempre | nunca
    cross_check: str = "auto"
    detect_text: bool = True
    chord_symbols_to_notes: bool = False
    piano_two_hands: bool = True
    # Tiempo máximo por página para un motor (segundos)
    engine_timeout_per_page: int = 300


@dataclass
class MusicSettings:
    default_tempo_bpm: float = 100.0
    # Repeticiones: expandir | no_expandir | preguntar
    repeats: str = "expandir"
    dynamics_to_velocity: bool = True
    sustain_pedal: bool = True
    fermata_stretch: bool = True
    articulation_effects: bool = True
    range_low: int = 21   # A0
    range_high: int = 108  # C8


@dataclass
class PlaybackSettings:
    # internal = sintetizador interno; windows_midi = sintetizador MIDI de Windows
    backend: str = "internal"
    timbre: str = "piano"
    volume: float = 0.8
    lookahead_s: float = 2.5
    separate_hands: str = "no"   # no | auto | siempre: separar las manos al escuchar (no afecta a lo exportado)


@dataclass
class ExportSettings:
    midi_type1: bool = True
    midi_type0: bool = False
    musicxml: bool = True
    report: bool = True
    project: bool = True
    video: bool = False
    output_dir: str = ""
    auto_export_batch: bool = True
    open_folder_after: bool = False


@dataclass
class EngineSettings:
    audiveris_path: str = ""  # vacío = detección automática
    homr_python: str = ""     # vacío = el mismo Python de la aplicación
    oemer_command: str = ""   # vacío = buscar "oemer" en tools/oemer o en PATH
    tessdata_dir: str = ""    # vacío = tools/tessdata


@dataclass
class UiSettings:
    theme: str = "oscuro"
    recent_files: list[str] = field(default_factory=list)
    show_advanced: bool = False


@dataclass
class AppConfig:
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    music: MusicSettings = field(default_factory=MusicSettings)
    playback: PlaybackSettings = field(default_factory=PlaybackSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    engines: EngineSettings = field(default_factory=EngineSettings)
    ui: UiSettings = field(default_factory=UiSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        cfg = cls()
        _merge_into(cfg, data or {})
        return cfg

    def output_dir(self) -> Path:
        if self.export.output_dir:
            return Path(self.export.output_dir)
        return paths.default_output_dir()


def _merge_into(obj: Any, data: dict[str, Any]) -> None:
    """Copia los valores conocidos de ``data`` sobre el dataclass ``obj``.

    Las claves desconocidas se ignoran y los tipos incorrectos se descartan para
    que un archivo de configuración dañado no impida abrir el programa.
    """
    for f in fields(obj):
        if f.name not in data:
            continue
        current = getattr(obj, f.name)
        value = data[f.name]
        if is_dataclass(current):
            if isinstance(value, dict):
                _merge_into(current, value)
            continue
        if isinstance(current, bool):
            if isinstance(value, bool):
                setattr(obj, f.name, value)
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(obj, f.name, type(current)(value))
        elif isinstance(current, str):
            if isinstance(value, str):
                setattr(obj, f.name, value)
        elif isinstance(current, list):
            if isinstance(value, list):
                setattr(obj, f.name, [v for v in value if isinstance(v, str)])


def load_config(path: Path | None = None) -> AppConfig:
    path = path or paths.config_file()
    if not path.exists():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AppConfig.from_dict(data)
    except (OSError, ValueError) as exc:
        log.warning("No se pudo leer la configuración %s: %s. Se usan valores por defecto.", path, exc)
        return AppConfig()


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
