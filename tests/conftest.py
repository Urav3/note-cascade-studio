"""Configuración común de las pruebas."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

SAMPLES = ROOT / "fotos"


@pytest.fixture(scope="session", autouse=True)
def isolated_user_dir(tmp_path_factory):
    """Las pruebas no tocan la configuración ni los trabajos reales del usuario."""
    d = tmp_path_factory.mktemp("usuario")
    os.environ["NCS_USER_DIR"] = str(d)
    yield d


@pytest.fixture(scope="session")
def qapp_offscreen():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["pruebas", "-platform", "offscreen"])
    yield app


def sample(name: str) -> Path:
    p = SAMPLES / name
    if not p.exists():
        pytest.skip(f"No está el archivo de ejemplo {name}")
    return p
