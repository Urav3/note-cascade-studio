"""Clasificación de textos (no generan notas), proyecto, exportación y reproductor."""

from __future__ import annotations

from fractions import Fraction as F

import numpy as np
import pytest

import synthetic as S
from note_cascade.app.config import AppConfig
from note_cascade.export.exporter import ExportRequest, export_project
from note_cascade.layout.integrate import integrate_text
from note_cascade.layout.text_classifier import NormStaff, NormSystem, TextElement, classify_page
from note_cascade.music_model.model import PagePosition
from note_cascade.pdf_processing.pdf_document import TextSpan
from note_cascade.project.project import Project


def _layout():
    il = 0.008
    staves = [NormStaff(0.08, 0.92, 0.20, 0.20 + 4 * il, il), NormStaff(0.08, 0.92, 0.28, 0.28 + 4 * il, il),
              NormStaff(0.08, 0.92, 0.50, 0.50 + 4 * il, il), NormStaff(0.08, 0.92, 0.58, 0.58 + 4 * il, il)]
    systems = [NormSystem([0, 1], 0.08, 0.92, 0.20, 0.28 + 4 * il, [0.3, 0.5, 0.7, 0.92]),
               NormSystem([2, 3], 0.08, 0.92, 0.50, 0.58 + 4 * il, [0.3, 0.5, 0.7, 0.92])]
    return staves, systems


def span(text, x0, y0, x1, y1, source="pdf", conf=None, bold=False):
    return TextSpan(text, x0, y0, x1, y1, size=(y1 - y0), source=source, confidence=conf, bold=bold)


def classify(spans):
    staves, systems = _layout()
    return {e.text: e for e in classify_page(spans, staves, systems, 0, piano_like=True)}


def test_contextual_text_classification():
    spans = [
        span("Nocturno en Mi menor", 0.30, 0.04, 0.70, 0.08),
        span("Frédéric Chopin", 0.72, 0.11, 0.92, 0.13),
        span("Allegro moderato", 0.08, 0.165, 0.25, 0.18),
        span("♩ = 120", 0.27, 0.165, 0.34, 0.18),
        span("12", 0.08, 0.475, 0.10, 0.49),
        span("Pno.", 0.02, 0.22, 0.06, 0.24),
        span("la", 0.20, 0.245, 0.22, 0.255), span("mú", 0.40, 0.245, 0.43, 0.255),
        span("si", 0.60, 0.245, 0.62, 0.255), span("ca", 0.80, 0.245, 0.82, 0.255),
        span("rit.", 0.70, 0.475, 0.74, 0.49),
        span("pp", 0.40, 0.255, 0.42, 0.265),
        span("Am7", 0.45, 0.48, 0.49, 0.493),
        span("D.C. al Fine", 0.80, 0.63, 0.92, 0.645),
        span("© 2026 Editorial de Prueba", 0.30, 0.95, 0.70, 0.965),
        span("3", 0.96, 0.02, 0.98, 0.04),
    ]
    c = classify(spans)
    assert c["Nocturno en Mi menor"].category == "titulo"
    assert c["Frédéric Chopin"].category == "compositor"
    assert c["Allegro moderato"].category == "tempo" and not c["Allegro moderato"].value  # sin inventar BPM
    assert c["♩ = 120"].category == "tempo" and c["♩ = 120"].value == "120"
    assert c["12"].category == "numero_compas"
    assert c["Pno."].category == "instrumento"
    assert {c[w].category for w in ("la", "mú", "si", "ca")} == {"letra"}
    assert c["rit."].category == "cambio_tempo"
    assert c["pp"].category == "dinamica"
    assert c["Am7"].category == "cifrado"
    assert c["D.C. al Fine"].category == "navegacion"
    assert c["© 2026 Editorial de Prueba"].category == "referencia"
    assert c["3"].category == "numero_pagina"


def test_ocr_numbers_without_equals_never_become_tempo():
    spans = [span("43", 0.40, 0.17, 0.43, 0.185, source="ocr", conf=0.9),
             span("25", 0.60, 0.17, 0.63, 0.185, source="ocr", conf=0.95)]
    c = classify(spans)
    assert c["43"].category != "tempo" and c["25"].category != "tempo"


def test_text_never_creates_notes():
    s = S.piano_score()
    for m in s.measures:
        row = m.index // 4
        m.position = PagePosition(0, 0.08 + (m.index % 4) * 0.21, 0.2 + row * 0.3, 0.21, 0.12)
    before = len(s.notes)
    elements = [TextElement(t, 0, 0.3, 0.25, 0.32, 0.26, "ocr", cat) for t, cat in
                (("C", "dudoso"), ("A", "ensayo"), ("la", "letra"), ("Sonata", "titulo"), ("1999", "referencia"))]
    integrate_text(s, elements)
    assert len(s.notes) == before


def test_tempo_from_text_is_reported_and_proposed():
    s = S.piano_score()
    s.tempos = []
    for m in s.measures:
        m.position = PagePosition(0, 0.08 + (m.index % 4) * 0.21, 0.2 + (m.index // 4) * 0.3, 0.21, 0.12)
    el = TextElement("90", 0, 0.12, 0.17, 0.15, 0.19, "pdf", "tempo", value="90")
    issues = integrate_text(s, [el])
    assert s.tempos and s.tempos[0].bpm_quarter == 90 and s.tempos[0].source == "texto"
    tempo_issue = next(i for i in issues if i.code == "tempo_texto")
    assert tempo_issue.fix is not None and tempo_issue.evidence == "sospecha"
    assert s.title == "Estudio de prueba", "un título legible del motor no se descarta"
    s.title = "E f f f f e"
    integrate_text(s, [el, TextElement("Sonata", 0, 0.4, 0.02, 0.6, 0.05, "ocr", "dudoso")])
    assert s.title == "", "un título ilegible que no aparece en la página se descarta"


def test_project_save_load_and_export(tmp_path, qapp_offscreen):
    s = S.piano_score()
    p = Project.new_workspace("prueba")
    p.score = s
    p.original_score = s.clone()
    from note_cascade.pipeline.conversion import revalidate_project
    cfg = AppConfig()
    revalidate_project(p, cfg)
    path = p.save(tmp_path / "prueba.ncsproj")
    q = Project.load(path)
    assert S.note_events(q.score) == S.note_events(s)
    assert len(q.issues) == len(p.issues)
    req = ExportRequest(tmp_path / "salida", "prueba", midi_type0=True)
    outcomes = export_project(q, req, cfg)
    assert all(o.ok for o in outcomes), [o.message for o in outcomes]
    kinds = {o.kind for o in outcomes}
    assert {"midi1", "midi0", "musicxml", "report", "project"} <= kinds
    assert (tmp_path / "salida" / "prueba_informe.html").read_text(encoding="utf-8").count("Informe") >= 1


def test_export_refuses_empty_score(tmp_path):
    from note_cascade.app.errors import ExportError
    p = Project.new_workspace("vacia")
    with pytest.raises(ExportError):
        export_project(p, ExportRequest(tmp_path, "x"), AppConfig())


def test_synth_stems_and_silent_player():
    from note_cascade.music_model.timeline import build_performance
    from note_cascade.player import synth
    from note_cascade.player.audio_engine import PlaybackEngine
    perf = build_performance(S.piano_score(measures=4))
    stems, total = synth.render_stems(perf, 1.0, "piano")
    assert set(stems) == {"R", "L"} and total > perf.total_seconds
    assert np.abs(stems["R"]).max() > 0.05 and np.abs(stems["L"]).max() > 0.05
    fast, _ = synth.render_stems(perf, 2.0, "piano")
    assert len(fast["R"]) < len(stems["R"])
    eng = PlaybackEngine("internal")
    eng._silent = True  # sin dispositivo: reloj silencioso
    eng.set_performance(perf, keep_position=False)
    assert eng.wait_ready(30)
    eng.seek(3.0)
    assert abs(eng.position() - 3.0) < 0.05
    eng.set_loop(1.0, 2.0)
    eng.close()


def test_diagnostic_text_can_hide_the_user_folder():
    """El diagnóstico para pegar en un aviso público no lleva el nombre de Windows del usuario."""
    from pathlib import Path
    from note_cascade.app.diagnostics import without_user_folder
    home = Path(r"C:\Users\Ana")
    text = (r"Python: 3.12 (C:\Users\Ana\Descargas\NCS\NoteCascadeStudio.exe)" "\n"
            r"Audiveris: c:/users/ana/AppData/tools" "\n"
            r"Otro usuario: C:\Users\Anabel\x" "\n"
            r"Final: C:\Users\Ana")
    out = without_user_folder(text, home)
    assert r"%USERPROFILE%\Descargas\NCS" in out
    assert "%USERPROFILE%/AppData/tools" in out
    assert r"C:\Users\Anabel\x" in out                # otra carpeta que empieza igual no se toca
    assert out.endswith("Final: %USERPROFILE%")
    assert without_user_folder(text, Path("C:\\")) == text
