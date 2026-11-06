"""Escribe config/configuracion_ejemplo.json con los valores por defecto documentados."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from note_cascade.app.config import AppConfig  # noqa: E402

data = AppConfig().to_dict()
data["_ayuda"] = {
    "ubicacion": "La aplicación guarda su configuración en %LOCALAPPDATA%\\NoteCascadeStudio\\configuracion.json. "
                 "Este archivo es un ejemplo con los valores por defecto; se puede copiar allí y editar.",
    "processing.dpi": "auto | 200 | 300 | 400 | 600",
    "processing.image_correction": "auto | ninguna | suave | fuerte",
    "processing.engine": "auto | audiveris | homr | oemer",
    "processing.cross_check": "auto (solo si hace falta) | siempre | nunca",
    "processing.engine_timeout_per_page": "segundos máximos por página para un motor",
    "music.repeats": "expandir | no_expandir",
    "music.range_low / range_high": "rango MIDI considerado válido (21 = La0, 108 = Do8)",
    "playback.backend": "internal (sintetizador interno) | windows_midi",
    "playback.timbre": "piano | piano_suave | electrico | organo",
    "export.output_dir": "vacío = Documentos\\Note Cascade Studio",
    "engines.*": "vacío = detección automática",
    "ui.theme": "oscuro | claro",
}
out = ROOT / "config" / "configuracion_ejemplo.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print("Escrito", out)
