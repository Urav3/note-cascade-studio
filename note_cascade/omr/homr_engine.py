"""Adaptador de HOMR (AGPL-3.0): OMR basado en transformers, pensado para fotos.

Se ejecuta en un subproceso (``python -m homr.main``) para poder cancelarlo,
limitar su memoria al proceso y aislar posibles fallos. Procesa una página
por ejecución; la aplicación une después las páginas y comprueba la
continuidad entre ellas.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import shutil
import sys
import time
from pathlib import Path

from note_cascade.app.errors import CancelledError
from note_cascade.omr.base import EnginePageResult, EngineRequest, EngineResult, EngineStatus, OMREngine
from note_cascade.omr.process import ProcessTimeout, run_process

log = logging.getLogger(__name__)

PROGRESS_HINTS = [
    ("Processing", 0.05, "cargando imagen"),
    ("Starting Inference", 0.10, "segmentación (red neuronal)"),
    ("Segnet Inference", 0.35, "segmentación terminada"),
    ("Creating bounds for noteheads", 0.40, "localizando cabezas de nota"),
    ("Found", 0.45, "localizando pentagramas"),
    ("Inference Time Tromr", 0.85, "reconocimiento de símbolos"),
    ("Writing XML", 0.95, "generando MusicXML"),
]

ERRORS_ES = {
    "No noteheads found": "HOMR no encontró cabezas de nota en la página.",
    "No staffs found": "HOMR no encontró pentagramas en la página.",
    "No staff found": "HOMR no encontró pentagramas en la página.",
}


class HomrEngine(OMREngine):
    id = "homr"
    name = "HOMR"
    license = "AGPL-3.0"
    description = "Reconocimiento con visión artificial (transformers) entrenado también con fotografías."
    strengths = "Fotografías de móvil, capturas pequeñas, imágenes con perspectiva; pentagramas dobles de piano."
    weaknesses = ("Ignora dinámicas, articulaciones, dobles alteraciones y textos; no da puntuación de confianza "
                  "por nota; los compases de una página se reconocen sin contexto de las demás.")

    def __init__(self, python_exe: str = "") -> None:
        self.python_exe = python_exe or sys.executable

    def _command(self, *args: str) -> list[str]:
        if getattr(sys, "frozen", False) and self.python_exe == sys.executable:
            # En la versión empaquetada se usa el ejecutable de consola (sin ventana visible).
            console = Path(sys.executable).with_name("NoteCascadeStudio-consola.exe")
            exe = str(console) if console.exists() else sys.executable
            return [exe, "--ejecutar-homr", *args]
        return [self.python_exe, "-W", "ignore", "-m", "homr.main", *args]

    def _package_dir(self) -> Path | None:
        if self.python_exe == sys.executable:
            spec = importlib.util.find_spec("homr")
            if spec and spec.origin:
                return Path(spec.origin).parent
        return None

    def models_present(self) -> bool:
        pkg = self._package_dir()
        if pkg is None:
            return True  # Python externo: se comprobará al ejecutar
        seg = list((pkg / "segmentation").glob("*.onnx"))
        tr = list((pkg / "transformer").glob("*.onnx"))
        return bool(seg) and len(tr) >= 2

    def status(self, refresh: bool = False) -> EngineStatus:
        st = EngineStatus(self.id, self.name, False, license=self.license)
        if self.python_exe == sys.executable:
            if importlib.util.find_spec("homr") is None:
                st.detail = "el paquete de Python «homr» no está instalado."
                st.suggestion = "Ejecuta install_windows.bat para instalarlo."
                return st
            try:
                st.version = importlib.metadata.version("homr")
            except importlib.metadata.PackageNotFoundError:
                st.version = "?"
            st.location = str(self._package_dir())
        else:
            if not Path(self.python_exe).exists():
                st.detail = f"no se encuentra el intérprete configurado ({self.python_exe})."
                return st
            st.location = self.python_exe
        if not self.models_present():
            st.models_ready = False
            st.detail = "instalado, pero faltan los modelos (≈ 300 MB, se descargan una vez desde GitHub)."
            st.suggestion = "Pulsa «Descargar modelos» en Diagnóstico o ejecuta install_windows.bat."
            return st
        st.available = True
        st.detail = "listo (CPU)."
        return st

    def download_models(self, progress=None, cancel=None) -> tuple[bool, str]:
        def on_line(line: str) -> None:
            if progress and "Downloaded" in line:
                progress(0.5, line.strip())
        try:
            code, tail = run_process(self._command("--init"),
                                     env={"PYTHONIOENCODING": "utf-8", "PYTHONWARNINGS": "ignore"},
                                     timeout_s=3600, cancel=cancel, on_line=on_line)
        except ProcessTimeout:
            return False, "La descarga de modelos tardó demasiado."
        ok = code == 0 and self.models_present()
        return ok, "Modelos descargados." if ok else "No se pudieron descargar los modelos: " + " ".join(tail[-3:])

    # ------------------------------------------------------------------
    def recognize(self, request: EngineRequest) -> EngineResult:
        start = time.monotonic()
        st = self.status()
        result = EngineResult(self.id, self.name, st.version, False, per_page_files=True)
        if not st.available:
            result.message = f"HOMR no está disponible: {st.detail}"
            return result
        work = request.work_dir / "homr"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        n = len(request.pages)
        log_lines: list[str] = []
        for k, page in enumerate(request.pages):
            request.cancel.check()
            img = work / f"pagina_{page.page_index + 1:03d}.png"
            shutil.copyfile(page.image_path, img)
            base = k / n

            def on_line(line: str, base=base, page=page) -> None:
                log_lines.append(line)
                for key, frac, msg in PROGRESS_HINTS:
                    if line.startswith(key):
                        request.report(base + frac / n, f"HOMR · página {page.page_index + 1}: {msg}")
                        break

            request.report(base, f"HOMR · página {page.page_index + 1} ({k + 1} de {n})")
            try:
                code, tail = run_process(
                    self._command("--write-staff-positions", img.name),
                    cwd=work, env={"PYTHONIOENCODING": "utf-8", "PYTHONWARNINGS": "ignore"},
                    timeout_s=max(120.0, request.timeout_s / max(1, n)), cancel=request.cancel, on_line=on_line)
            except ProcessTimeout as exc:
                result.page_results.append(EnginePageResult(page.page_index, False, None, f"tiempo agotado: {exc}"))
                continue
            except CancelledError:
                raise
            xml = img.with_suffix(".musicxml")
            if code == 0 and xml.exists() and xml.stat().st_size > 200:
                extra = {}
                staff_txt = img.with_suffix(".txt")
                if staff_txt.exists():
                    extra["staff_positions"] = str(staff_txt)
                extra["input_size"] = (page.width, page.height)
                result.page_results.append(EnginePageResult(page.page_index, True, xml, "", extra))
                result.musicxml_paths.append(xml)
            else:
                msg = _explain_failure(tail)
                result.page_results.append(EnginePageResult(page.page_index, False, None, msg))
        (work / "homr.log").write_text("\n".join(log_lines), encoding="utf-8")
        result.log_path = work / "homr.log"
        result.ok = any(p.ok for p in result.page_results)
        result.duration_s = time.monotonic() - start
        failed = [p for p in result.page_results if not p.ok]
        if failed:
            result.warnings.extend(f"Página {p.page_index + 1}: {p.message}" for p in failed)
        result.message = f"HOMR terminó en {result.duration_s:.0f} s." if result.ok else \
            "HOMR no pudo reconocer ninguna página."
        request.report(1.0, "HOMR · terminado")
        return result


def _explain_failure(tail: list[str]) -> str:
    text = "\n".join(tail)
    for key, msg in ERRORS_ES.items():
        if key in text:
            return msg
    last = next((l for l in reversed(tail) if l.strip()), "")
    return "HOMR falló" + (f": {last[-200:]}" if last else ".")
