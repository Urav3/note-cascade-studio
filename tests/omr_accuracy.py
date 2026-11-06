"""Evaluación de la precisión del reconocimiento con partituras de referencia.

Uso:  python -m tests.omr_accuracy  [carpeta_de_salida]

Genera partituras sintéticas con resultado conocido en varios formatos (PDF
vectorial, PDF con imagen escaneada, PNG, foto simulada, PDF girado), las
convierte con cada motor disponible y compara el resultado con la referencia.
Separa la calidad del motor (métricas) de la corrección del programa (que el
flujo termine y los archivos sean válidos).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import synthetic as S  # noqa: E402

from note_cascade.app.config import AppConfig  # noqa: E402
from note_cascade.importers.document import open_document  # noqa: E402
from note_cascade.omr.registry import create_engines, engine_statuses  # noqa: E402
from note_cascade.pipeline.conversion import ConversionOptions, ConversionPipeline  # noqa: E402


def make_cases(out: Path) -> list[tuple[str, Path, object]]:
    out.mkdir(parents=True, exist_ok=True)
    piano = S.piano_score(title="Estudio de prueba", lyrics=True)
    melody = S.melody_score()
    cases = []
    png = S.render_png(piano, out / "piano.png", 2480)
    cases.append(("Imagen PNG (piano, con letra y títulos)", png, piano))
    cases.append(("PDF vectorial (piano)", S.render_pdf_vector(piano, out / "piano_vectorial.pdf"), piano))
    cases.append(("PDF con imagen escaneada (piano)", S.pdf_with_image(png, out / "piano_escaneado.pdf"), piano))
    cases.append(("Foto simulada JPG (perspectiva, sombra)", S.photo_like(png, out / "piano_foto.jpg"), piano))
    cases.append(("PDF con página girada 90°", S.pdf_with_image(png, out / "piano_girado.pdf", rotate=90), piano))
    small = S.render_png(piano, out / "piano_captura_pequena.png", 900)
    cases.append(("Captura pequeña (900 px de ancho)", small, piano))
    mel = S.render_png(melody, out / "melodia.png", 2480)
    cases.append(("Melodía en un pentagrama (3/4, bemoles)", mel, melody))
    return cases


def run(out_dir: Path, engines_to_test: list[str] | None = None) -> list[dict]:
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication(["x", "-platform", "offscreen"])
    cfg = AppConfig()
    engines = create_engines(cfg)
    statuses = engine_statuses(engines)
    available = [e for e, st in statuses.items() if st.available]
    engines_to_test = [e for e in (engines_to_test or available) if e in available]
    results = []
    for name, path, truth in make_cases(out_dir / "entradas"):
        for eid in engines_to_test:
            cfg.processing.engine = eid
            cfg.processing.cross_check = "nunca"
            t0 = time.monotonic()
            row = {"caso": name, "archivo": path.name, "motor": statuses[eid].name}
            try:
                doc = open_document(path)
                project = ConversionPipeline(cfg, engines).convert(doc, ConversionOptions.from_config(cfg))
                metrics = S.accuracy(truth, project.score)
                row.update(metrics)
                row["compases"] = f"{len(project.score.measures)}/{len(truth.measures)}"
                lyric_hits = _notes_on_text(project)
                row["notas_sobre_textos"] = lyric_hits
                row["avisos"] = sum(1 for i in project.issues if i.status == "abierta" and i.severity != "info")
                row["ok"] = True
            except Exception as exc:
                row.update({"ok": False, "error": str(exc)[:200]})
            row["segundos"] = round(time.monotonic() - t0, 1)
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    (out_dir / "resultados_precision.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    return results


def _notes_on_text(project) -> int:
    """Notas reconocidas cuya posición cae sobre un texto que no es música (letra, título…)."""
    hits = 0
    texts = {}
    for p in project.pages:
        texts[p.index] = [t for t in p.text if t.get("category") in ("letra", "titulo", "subtitulo", "compositor",
                                                                     "referencia", "credito", "numero_pagina")]
    for n in project.score.notes:
        if n.rest or n.position is None or n.position.approximate:
            continue
        cx, cy = n.position.center()
        for t in texts.get(n.position.page, []):
            if t["x0"] <= cx <= t["x1"] and t["y0"] <= cy <= t["y1"]:
                hits += 1
                break
    return hits


def to_markdown(results: list[dict]) -> str:
    lines = ["| Caso | Motor | Compases | Notas ref./rec. | Precisión altura | Exhaustividad | Notas exactas (altura+ritmo) | Omitidas | Adicionales | Notas sobre textos | Tiempo |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if not r.get("ok"):
            lines.append(f"| {r['caso']} | {r['motor']} | — | — | — | — | — | — | — | — | falló: {r.get('error', '')[:80]} |")
            continue
        lines.append(f"| {r['caso']} | {r['motor']} | {r['compases']} | {r['notas_referencia']}/{r['notas_reconocidas']} | "
                     f"{100 * r['precision_altura']:.0f} % | {100 * r['exhaustividad_altura']:.0f} % | "
                     f"{100 * r['notas_exactas']:.0f} % | {r['notas_omitidas']} | {r['notas_adicionales']} | "
                     f"{r['notas_sobre_textos']} | {r['segundos']} s |")
    return "\n".join(lines)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "_salida_precision"
    res = run(out)
    md = to_markdown(res)
    (out / "resultados_precision.md").write_text(md, encoding="utf-8")
    print(md)
