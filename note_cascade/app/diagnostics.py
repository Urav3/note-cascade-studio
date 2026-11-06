"""Diagnóstico del sistema: dependencias, motores, modelos y audio."""

from __future__ import annotations

import importlib
import importlib.metadata
import platform
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from note_cascade import APP_NAME, VERSION_LABEL
from note_cascade.app import paths
from note_cascade.app.config import AppConfig


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True
    suggestion: str = ""


@dataclass
class DiagnosticReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    def text(self, anonymous: bool = False) -> str:
        """Texto del diagnóstico. Con ``anonymous`` la carpeta del usuario (que incluye su nombre de
        Windows) se sustituye por %USERPROFILE%, para pegarlo en un informe público."""
        text = self._text()
        return without_user_folder(text) if anonymous else text

    def _text(self) -> str:
        lines = [f"{APP_NAME} {VERSION_LABEL} — diagnóstico", "=" * 60]
        for c in self.checks:
            mark = "OK " if c.ok else ("FALTA" if c.required else "opcional")
            lines.append(f"[{mark:>8}] {c.name}: {c.detail}")
            if not c.ok and c.suggestion:
                lines.append(f"{'':11}→ {c.suggestion}")
        lines.append("=" * 60)
        lines.append("Resultado: " + ("todo lo esencial está disponible." if self.ok else
                                      "faltan componentes esenciales (ver arriba)."))
        return "\n".join(lines)


def without_user_folder(text: str, home: Path | None = None) -> str:
    """Sustituye la carpeta personal (con barras de cualquier tipo, sin distinguir mayúsculas) por
    %USERPROFILE%."""
    home_s = str(home or Path.home()).rstrip("\\/")
    if len(home_s) < 4:                      # «C:\» o vacío: no hay nada personal que ocultar
        return text
    parts = [re.escape(p) for p in re.split(r"[\\/]+", home_s) if p]
    pattern = r"[\\/]+".join(parts) + r"(?=[\\/]|\b|$)"
    return re.sub(pattern, "%USERPROFILE%", text, flags=re.IGNORECASE)


def _pkg(name: str, module: str, required: bool, purpose: str) -> Check:
    try:
        importlib.import_module(module)
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "?"
        return Check(f"{name}", True, f"{version} ({purpose})", required)
    except Exception as exc:
        return Check(name, False, f"no disponible ({purpose}): {exc.__class__.__name__}", required,
                     "Ejecuta install_windows.bat.")


def run_diagnostics(cfg: AppConfig | None = None, check_audio: bool = True) -> DiagnosticReport:
    from note_cascade.omr.registry import create_engines, engine_statuses

    cfg = cfg or AppConfig()
    rep = DiagnosticReport()
    py_ok = sys.version_info[:2] in ((3, 11), (3, 12))
    rep.checks.append(Check("Python", py_ok, f"{platform.python_version()} ({sys.executable})", True,
                            "Se necesita Python 3.11 o 3.12 (lo instala install_windows.bat)."))
    rep.checks.append(Check("Sistema", True, f"{platform.system()} {platform.release()} ({platform.machine()})", False))
    for name, module, required, purpose in (
        ("PySide6-Essentials", "PySide6.QtWidgets", True, "interfaz gráfica"),
        ("numpy", "numpy", True, "cálculo numérico"),
        ("opencv-python-headless", "cv2", True, "procesamiento de imagen"),
        ("Pillow", "PIL", True, "lectura de imágenes"),
        ("PyMuPDF", "pymupdf", True, "lectura y renderizado de PDF"),
        ("mido", "mido", True, "archivos MIDI"),
        ("sounddevice", "sounddevice", False, "salida de audio del sintetizador interno"),
        ("rapidocr", "rapidocr", False, "OCR de textos de la partitura"),
        ("onnxruntime", "onnxruntime", False, "modelos de HOMR y OCR"),
        ("python-rtmidi", "rtmidi", False, "sintetizador MIDI de Windows"),
        ("imageio-ffmpeg", "imageio_ffmpeg", False, "exportación de vídeo"),
    ):
        rep.checks.append(_pkg(name, module, required, purpose))
    engines = create_engines(cfg)
    statuses = engine_statuses(engines, refresh=True)
    any_engine = False
    for eid, st in statuses.items():
        any_engine = any_engine or st.available
        detail = f"{st.version} — {st.detail}" if st.version else st.detail
        if st.location:
            detail += f" [{st.location}]"
        rep.checks.append(Check(f"Motor OMR {st.name}", st.available, detail, False, st.suggestion))
    rep.checks.append(Check("Al menos un motor OMR", any_engine,
                            "hay motor disponible" if any_engine else "ninguno instalado", True,
                            "Ejecuta install_windows.bat para instalar Audiveris y HOMR."))
    td = paths.tools_dir() / "tessdata"
    langs = sorted(p.stem for p in td.glob("*.traineddata")) if td.exists() else []
    rep.checks.append(Check("Datos de OCR para Audiveris (tessdata)", bool(langs),
                            ", ".join(langs) if langs else "no encontrados", False,
                            "install_windows.bat los descarga desde github.com/tesseract-ocr/tessdata."))
    if check_audio:
        try:
            import sounddevice as sd
            dev = sd.query_devices(kind="output")
            rep.checks.append(Check("Salida de audio", True, f"{dev['name']}", False))
        except Exception as exc:
            rep.checks.append(Check("Salida de audio", False, f"sin dispositivo de salida ({exc.__class__.__name__})",
                                    False, "Conecta altavoces o auriculares; la conversión funciona igualmente."))
        try:
            import rtmidi
            ports = rtmidi.MidiOut().get_ports()
            rep.checks.append(Check("Puertos MIDI de salida", bool(ports), ", ".join(ports) or "ninguno", False))
        except Exception as exc:
            rep.checks.append(Check("Puertos MIDI de salida", False, str(exc), False))
    try:
        free = shutil.disk_usage(paths.user_data_dir()).free / 1e9
        rep.checks.append(Check("Espacio libre", free > 1.0, f"{free:.1f} GB en la carpeta de datos", True,
                                "Libera espacio en disco (se necesitan al menos 1 GB)."))
    except OSError as exc:
        rep.checks.append(Check("Carpeta de datos", False, str(exc), True))
    rep.checks.append(Check("Carpeta de datos del usuario", True, str(paths.user_data_dir()), False))
    rep.checks.append(Check("Carpeta de herramientas", paths.tools_dir().exists(), str(paths.tools_dir()), False,
                            "Ejecuta install_windows.bat."))
    return rep
