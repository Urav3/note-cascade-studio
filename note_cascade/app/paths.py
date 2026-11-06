"""Rutas de la aplicación.

La aplicación puede ejecutarse desde el código fuente (carpeta del proyecto) o
empaquetada con PyInstaller. Las herramientas externas (Audiveris portátil,
datos de Tesseract, uv) viven en ``tools/`` junto al proyecto o junto al
ejecutable. Los datos del usuario (configuración, trabajos, autoguardado y
registros) se guardan en ``%LOCALAPPDATA%\\NoteCascadeStudio``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from note_cascade import APP_ID


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    """Carpeta raíz de la instalación (la que contiene ``tools/``)."""
    override = os.environ.get("NCS_INSTALL_ROOT")
    if override:
        return Path(override)
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resources_dir() -> Path:
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "note_cascade" / "resources"  # type: ignore[attr-defined]
    return package_root() / "resources"


def tools_dir() -> Path:
    override = os.environ.get("NCS_TOOLS_DIR")
    if override:
        return Path(override)
    return install_root() / "tools"


def user_data_dir() -> Path:
    override = os.environ.get("NCS_USER_DIR")
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / APP_ID if local else Path.home() / f".{APP_ID.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def work_dir() -> Path:
    path = user_data_dir() / "trabajos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def autosave_dir() -> Path:
    path = user_data_dir() / "autoguardado"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = user_data_dir() / "registros"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return user_data_dir() / "configuracion.json"


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    return docs / "Note Cascade Studio"
