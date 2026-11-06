"""Adaptador opcional de oemer (MIT).

oemer no se instala junto a la aplicación: su última versión publicada es de
2023 y sus dependencias pueden entrar en conflicto con las de HOMR (su propio
autor recomienda HOMR como sucesor). Si el usuario lo instala aparte (por
ejemplo en ``tools/oemer``), la aplicación lo detecta y lo usa como motor
adicional. Ver README_ES.md, sección «Motores opcionales».
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from note_cascade.app import paths
from note_cascade.app.errors import CancelledError
from note_cascade.omr.base import EnginePageResult, EngineRequest, EngineResult, EngineStatus, OMREngine
from note_cascade.omr.process import ProcessTimeout, run_process


class OemerEngine(OMREngine):
    id = "oemer"
    name = "oemer"
    license = "MIT (instalación independiente)"
    description = "OMR de extremo a extremo para fotos (predecesor de HOMR)."
    strengths = "Fotografías y partituras inclinadas."
    weaknesses = "Lento (minutos por página en CPU), sin mantenimiento activo, sin confianza por nota."

    def __init__(self, command: str = "") -> None:
        self.command = command

    def find_command(self) -> Path | None:
        candidates = []
        if self.command:
            candidates.append(Path(self.command))
        tools = paths.tools_dir() / "oemer"
        candidates += [tools / "Scripts" / "oemer.exe", tools / ".venv" / "Scripts" / "oemer.exe",
                       tools / "venv" / "Scripts" / "oemer.exe"]
        which = shutil.which("oemer")
        if which:
            candidates.append(Path(which))
        for c in candidates:
            if c.is_file():
                return c
        return None

    def status(self, refresh: bool = False) -> EngineStatus:
        st = EngineStatus(self.id, self.name, False, license=self.license)
        cmd = self.find_command()
        if cmd is None:
            st.detail = "no está instalado (motor opcional)."
            st.suggestion = "Consulta README_ES.md → «Motores opcionales» para instalarlo en tools\\oemer."
            return st
        st.location = str(cmd)
        st.available = True
        st.detail = "listo (instalación independiente)."
        return st

    def recognize(self, request: EngineRequest) -> EngineResult:
        start = time.monotonic()
        result = EngineResult(self.id, self.name, "", False, per_page_files=True)
        cmd = self.find_command()
        if cmd is None:
            result.message = "oemer no está instalado."
            return result
        work = request.work_dir / "oemer"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        n = len(request.pages)
        for k, page in enumerate(request.pages):
            request.cancel.check()
            img = work / f"pagina_{page.page_index + 1:03d}.png"
            shutil.copyfile(page.image_path, img)
            request.report(k / n, f"oemer · página {page.page_index + 1} ({k + 1} de {n}); puede tardar varios minutos")
            try:
                code, tail = run_process([str(cmd), "-o", str(work), str(img)], cwd=work,
                                         timeout_s=max(600.0, request.timeout_s / max(1, n)),
                                         cancel=request.cancel, env={"PYTHONIOENCODING": "utf-8"})
            except ProcessTimeout as exc:
                result.page_results.append(EnginePageResult(page.page_index, False, None, str(exc)))
                continue
            except CancelledError:
                raise
            xml = work / (img.stem + ".musicxml")
            if code == 0 and xml.exists():
                result.page_results.append(EnginePageResult(page.page_index, True, xml))
                result.musicxml_paths.append(xml)
            else:
                lines = [l.split("\r")[-1].strip() for l in tail if l.strip()]
                err = next((l for l in reversed(lines) if "Error" in l or "Exception" in l), lines[-1] if lines else "")
                hint = ""
                if "numpy" in err.lower() or "_ARRAY_API" in err or "pads must not contain negative" in err:
                    hint = (" (incompatibilidad de versiones: instala en su entorno numpy<2, onnxruntime==1.16.3 y "
                            "opencv-python-headless<4.11; ver README_ES.md)")
                result.page_results.append(EnginePageResult(page.page_index, False, None,
                                                            f"oemer falló: {err[-200:]}{hint}"))
        result.ok = any(p.ok for p in result.page_results)
        result.duration_s = time.monotonic() - start
        result.message = f"oemer terminó en {result.duration_s:.0f} s." if result.ok else "oemer no reconoció ninguna página."
        return result
