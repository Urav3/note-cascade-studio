"""Adaptador de Audiveris (AGPL-3.0), ejecutado como programa independiente.

Audiveris se invoca por línea de comandos (``-batch -transcribe -export``).
Todas las páginas se entregan juntas como un TIFF multipágina para
que Audiveris las trate como un único libro y mantenga la continuidad entre
páginas (ligaduras, armaduras, numeración de compases).

Además del MusicXML se conserva el proyecto ``.omr``, que contiene la
posición de cada cabeza de nota y su puntuación de calidad (``grade``): esa es
la única fuente de «confianza» real que se usa para Audiveris.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

from PIL import Image

from note_cascade.app import paths
from note_cascade.app.errors import CancelledError
from note_cascade.omr.base import EnginePageResult, EngineRequest, EngineResult, EngineStatus, OMREngine
from note_cascade.omr.process import ProcessTimeout, run_process

log = logging.getLogger(__name__)

STEPS = ["LOAD", "BINARY", "SCALE", "GRID", "HEADERS", "STEM_SEEDS", "BEAMS", "LEDGERS", "HEADS", "STEMS",
         "REDUCTION", "CUE_BEAMS", "TEXTS", "MEASURES", "CHORDS", "CURVES", "SYMBOLS", "LINKS", "RHYTHMS", "PAGE"]
STEP_ES = {
    "LOAD": "cargando imagen", "BINARY": "binarizando", "SCALE": "midiendo pentagramas", "GRID": "detectando pentagramas",
    "HEADERS": "claves y armaduras", "STEM_SEEDS": "plicas", "BEAMS": "barras de unión", "LEDGERS": "líneas adicionales",
    "HEADS": "cabezas de nota", "STEMS": "plicas", "REDUCTION": "depurando símbolos", "CUE_BEAMS": "notas pequeñas",
    "TEXTS": "texto (OCR)", "MEASURES": "compases", "CHORDS": "acordes", "CURVES": "ligaduras",
    "SYMBOLS": "símbolos", "LINKS": "relaciones", "RHYTHMS": "ritmo", "PAGE": "página",
}
STEP_RE = re.compile(r"\[[^\]#]*#(\d+)\]\s+StepMonitoring\s+\d+\s*\|\s*([A-Z_]+)")
_version_cache: dict[str, str] = {}


class AudiverisEngine(OMREngine):
    id = "audiveris"
    name = "Audiveris"
    license = "AGPL-3.0 (programa externo)"
    description = "Motor clásico muy completo para partituras impresas y PDF vectoriales."
    strengths = ("Partituras impresas, PDF exportados desde programas de notación y escaneos limpios. "
                 "Reconstruye compases, voces, ligaduras, tresillos, dinámicas, textos y letras.")
    weaknesses = "Fotografías con perspectiva o iluminación irregular; compases poco comunes (p. ej. 12/8) a veces no se leen."

    def __init__(self, configured_path: str = "", tessdata_dir: str = "") -> None:
        self.configured_path = configured_path
        self.tessdata_dir = tessdata_dir

    # ------------------------------------------------------------------
    def find_executable(self) -> Path | None:
        candidates: list[Path] = []
        if self.configured_path:
            candidates.append(Path(self.configured_path))
        tools = paths.tools_dir()
        candidates += [tools / "audiveris" / "Audiveris" / "Audiveris.exe",
                       tools / "audiveris" / "Audiveris.exe"]
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "Audiveris" / "Audiveris.exe")
                candidates.append(Path(base) / "Programs" / "Audiveris" / "Audiveris.exe")
        which = shutil.which("Audiveris") or shutil.which("audiveris")
        if which:
            candidates.append(Path(which))
        for c in candidates:
            if c.is_file():
                return c
        return None

    def tessdata(self) -> Path | None:
        candidates = [Path(self.tessdata_dir)] if self.tessdata_dir else []
        candidates.append(paths.tools_dir() / "tessdata")
        for c in candidates:
            if c.is_dir() and any(c.glob("*.traineddata")):
                return c
        return None

    def _env(self) -> dict[str, str]:
        env = {"JAVA_TOOL_OPTIONS": "-Dfile.encoding=UTF-8"}
        td = self.tessdata()
        if td:
            env["TESSDATA_PREFIX"] = str(td)
        return env

    def status(self, refresh: bool = False) -> EngineStatus:
        exe = self.find_executable()
        st = EngineStatus(self.id, self.name, False, license=self.license)
        if exe is None:
            st.detail = "no está instalado."
            st.suggestion = ("Ejecuta install_windows.bat (descarga Audiveris desde su repositorio oficial de "
                             "GitHub) o instala Audiveris desde https://github.com/Audiveris/audiveris/releases.")
            return st
        st.location = str(exe)
        key = f"{exe}:{exe.stat().st_mtime}"
        if refresh or key not in _version_cache:
            try:
                code, out = run_process([str(exe), "-batch", "-version"], env=self._env(), timeout_s=120)
                version = ""
                for line in out:
                    m = re.search(r"Version:\s*([\w.\-]+)", line)
                    if m:
                        version = m.group(1)
                _version_cache[key] = version or "?"
            except Exception as exc:
                st.detail = f"está instalado pero no se pudo ejecutar ({exc})."
                st.suggestion = "Reinstálalo con install_windows.bat."
                return st
        st.version = _version_cache[key]
        st.available = True
        st.detail = "listo."
        if self.tessdata() is None:
            st.detail = "listo, pero sin datos de OCR de Tesseract: no reconocerá textos (tempos, letras)."
            st.suggestion = "Ejecuta install_windows.bat para descargar los datos de idioma (tessdata)."
        return st

    # ------------------------------------------------------------------
    def recognize(self, request: EngineRequest) -> EngineResult:
        start = time.monotonic()
        exe = self.find_executable()
        result = EngineResult(self.id, self.name, "", False)
        if exe is None:
            result.message = "Audiveris no está instalado."
            return result
        st = self.status()
        result.version = st.version
        work = request.work_dir / "audiveris"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        (work / "out").mkdir(parents=True, exist_ok=True)
        book = re.sub(r"[^A-Za-z0-9_\-]+", "_", request.book_name)[:40] or "partitura"
        input_path = work / f"{book}.tif"
        frames = [Image.open(p.image_path).convert("L") for p in request.pages]
        if not frames:
            result.message = "No hay páginas para reconocer."
            return result
        frames[0].save(input_path, save_all=True, append_images=frames[1:], compression="tiff_deflate",
                       dpi=(300, 300))
        for f in frames:
            f.close()

        n_sheets = len(request.pages)
        total_steps = n_sheets * len(STEPS)
        done: dict[int, int] = {}

        def on_line(line: str) -> None:
            m = STEP_RE.search(line)
            if m:
                sheet = int(m.group(1))
                step = m.group(2)
                if step in STEPS:
                    done[sheet] = STEPS.index(step)
                    progress = sum(done.values()) / max(1, total_steps)
                    request.report(progress, f"Audiveris · página {sheet} de {n_sheets}: {STEP_ES.get(step, step.lower())}")

        request.report(0.0, "Audiveris · iniciando (Java)…")
        log_path = work / "audiveris.log"
        # Nota: no se usa «-save»; en Audiveris 5.11 provoca fallos intermitentes al guardar
        # hojas procesadas en paralelo (FileSystemAlreadyExistsException). El proyecto .omr con
        # las posiciones y las puntuaciones se escribe igualmente al exportar.
        cmd = [str(exe), "-batch", "-transcribe", "-export", "-output", str(work / "out"), "--",
               str(input_path)]
        try:
            code, tail = run_process(cmd, cwd=work, env=self._env(), timeout_s=request.timeout_s,
                                     cancel=request.cancel, on_line=on_line, log_file=log_path)
        except ProcessTimeout as exc:
            result.message = f"Audiveris tardó demasiado: {exc}"
            result.log_path = log_path
            return result
        except CancelledError:
            raise
        result.log_path = log_path
        mxls = sorted((work / "out").glob("*.mxl"), key=_movement_key)
        omr = next(iter((work / "out").glob("*.omr")), None)
        result.extra["omr_path"] = str(omr) if omr else ""
        result.extra["input_path"] = str(input_path)
        errors = [l for l in tail if " ERROR " in l or l.startswith("ERROR")]
        if not mxls:
            detail = errors[-1] if errors else (tail[-1] if tail else "")
            result.message = ("Audiveris no produjo ninguna partitura. Es posible que no haya encontrado "
                              "pentagramas legibles.")
            if detail:
                result.warnings.append("Detalle técnico: " + detail[-300:])
            for p in request.pages:
                result.page_results.append(EnginePageResult(p.page_index, False, None, "sin resultado"))
            result.duration_s = time.monotonic() - start
            return result
        result.musicxml_paths = mxls
        result.ok = True
        invalid = _invalid_sheets(log_path)
        for idx, p in enumerate(request.pages, start=1):
            ok = idx not in invalid
            result.page_results.append(EnginePageResult(
                p.page_index, ok, mxls[0], "" if ok else "Audiveris no encontró música válida en esta página"))
        for line in _relevant_warnings(log_path):
            result.warnings.append(line)
        result.duration_s = time.monotonic() - start
        result.message = f"Audiveris terminó en {result.duration_s:.0f} s."
        request.report(1.0, "Audiveris · terminado")
        return result


def _movement_key(p: Path) -> tuple[int, str]:
    m = re.search(r"\.mvt(\d+)\.mxl$", p.name)
    return (int(m.group(1)) if m else 0, p.name)


def _invalid_sheets(log_path: Path) -> set[int]:
    invalid: set[int] = set()
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return invalid
    for m in re.finditer(r"\[[^\]#]*#(\d+)\][^\n]*(invalid|no staves|Sheet flagged as invalid|No system found)",
                         text, re.I):
        invalid.add(int(m.group(1)))
    return invalid


def _relevant_warnings(log_path: Path, limit: int = 40) -> list[str]:
    out = []
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("WARN"):
                msg = line.split("|", 1)[-1].strip()
                if "No target duration" in msg:
                    continue  # se detecta con la validación propia (compás no reconocido)
                out.append(msg)
                if len(out) >= limit:
                    break
    except OSError:
        pass
    return out
