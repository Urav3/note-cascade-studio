"""Pruebas con motores OMR reales (lentas). Ejecutar con:  pytest -m omr

Separan la *corrección del programa* (el flujo termina, los archivos son
válidos, el texto no genera notas) de la *precisión del motor*, que se mide
y se informa con umbrales mínimos de cordura.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import synthetic as S
from conftest import SAMPLES
from note_cascade.app.config import AppConfig
from note_cascade.export.exporter import ExportRequest, export_project
from note_cascade.importers.document import open_document
from note_cascade.omr.registry import create_engines, engine_statuses
from note_cascade.pipeline.conversion import ConversionOptions, ConversionPipeline

pytestmark = pytest.mark.omr


def _engines():
    cfg = AppConfig()
    engines = create_engines(cfg)
    st = engine_statuses(engines)
    return cfg, engines, [e for e, s in st.items() if s.available]


def _convert(path: Path, engine: str = "auto", cross: str = "nunca"):
    cfg, engines, available = _engines()
    if engine != "auto" and engine not in available:
        pytest.skip(f"Motor {engine} no disponible")
    cfg.processing.engine = engine
    cfg.processing.cross_check = cross
    project = ConversionPipeline(cfg, engines).convert(open_document(path), ConversionOptions.from_config(cfg))
    return cfg, project


@pytest.fixture(scope="module")
def cases(tmp_path_factory, qapp_offscreen):
    out = tmp_path_factory.mktemp("sinteticos")
    piano = S.piano_score(title="Estudio de prueba", lyrics=True)
    png = S.render_png(piano, out / "piano.png")
    return {
        "png": (png, piano),
        "vector": (S.render_pdf_vector(piano, out / "piano.pdf"), piano),
        "scan": (S.pdf_with_image(png, out / "escaneo.pdf"), piano),
        "photo": (S.photo_like(png, out / "foto.jpg"), piano),
        "rotated": (S.pdf_with_image(png, out / "girado.pdf", rotate=90), piano),
    }


@pytest.mark.parametrize("engine", ["audiveris", "homr"])
@pytest.mark.parametrize("case", ["png", "vector", "scan", "photo", "rotated"])
def test_synthetic_accuracy(cases, case, engine, tmp_path):
    path, truth = cases[case]
    cfg, project = _convert(path, engine)
    m = S.accuracy(truth, project.score)
    print(case, engine, m)
    assert len(project.score.measures) == len(truth.measures)
    assert m["exhaustividad_altura"] >= 0.85 and m["precision_altura"] >= 0.85
    # Letras, título, compositor y referencia editorial no generan notas adicionales
    assert m["notas_adicionales"] <= 2
    outcomes = export_project(project, ExportRequest(tmp_path, "r", project=False, report=False), cfg)
    assert all(o.ok for o in outcomes), [o.message for o in outcomes]


def test_cross_check_marks_agreement(cases):
    path, truth = cases["png"]
    cfg, engines, available = _engines()
    if not {"audiveris", "homr"} <= set(available):
        pytest.skip("Se necesitan los dos motores")
    cfg, project = _convert(path, "audiveris", "siempre")
    assert len(project.engine_runs) == 2
    status = project.options["status"]["measures"]
    assert sum(1 for v in status.values() if v == "ok") >= 6


def test_page_without_music_is_reported(tmp_path, qapp_offscreen):
    import pymupdf as fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Prólogo: esta página no tiene música, solo texto.", fontsize=14)
    path = tmp_path / "texto.pdf"
    doc.save(path)
    from note_cascade.app.errors import NoteCascadeError
    with pytest.raises(NoteCascadeError, match="No se encontró notación musical"):
        _convert(path)


REAL = {
    "mi foto.png": {"measures": 17, "min_notes": 230},
    "The Pool.pdf": {"measures": 69, "min_notes": 1200},
}


@pytest.mark.parametrize("name", list(REAL))
def test_real_samples(name, tmp_path, qapp_offscreen):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip("archivo de ejemplo no disponible")
    cfg, project = _convert(path, "auto", "nunca")
    exp = REAL[name]
    assert len(project.score.measures) == exp["measures"]
    assert len(project.score.pitched_notes()) >= exp["min_notes"]
    assert project.score.parts[0].is_piano
    hands = {n.hand for n in project.score.pitched_notes()}
    assert hands == {"R", "L"}
    # Ninguna nota localizada sobre la letra de la canción
    lyrics = [t for p in project.pages for t in p.text if t["category"] == "letra"]
    for n in project.score.pitched_notes():
        if n.position and not n.position.approximate:
            cx, cy = n.position.center()
            assert not any(t["x0"] < cx < t["x1"] and t["y0"] < cy < t["y1"] for t in lyrics
                           if t["page"] == n.position.page), f"nota {n.name()} sobre la letra"
    outcomes = export_project(project, ExportRequest(tmp_path, "real"), cfg)
    assert all(o.ok for o in outcomes), [o.message for o in outcomes]


def test_gui_full_flow(qapp_offscreen, tmp_path):
    """Flujo completo en la interfaz: importar → analizar (hilo) → revisar/corregir → exportar."""
    import time
    from PySide6.QtCore import Qt
    from note_cascade.ui import theme
    from note_cascade.ui.controller import AppController
    from note_cascade.ui.main_window import MainWindow
    path = SAMPLES / "mi foto.png"
    if not path.exists():
        pytest.skip("archivo de ejemplo no disponible")
    app = qapp_offscreen
    theme.apply_theme(app, "oscuro")
    cfg = AppConfig()
    cfg.export.output_dir = str(tmp_path)
    c = AppController(cfg)
    c.player._silent = True
    c.refresh_engines()
    w = MainWindow(c)
    w.setAttribute(Qt.WA_DontShowOnScreen)
    w.show()

    def wait(cond, timeout=600):
        end = time.time() + timeout
        while time.time() < end and not cond():
            app.processEvents()
            time.sleep(0.05)
        return cond()

    imp = w.page_import
    imp.add_items([str(path)])
    assert wait(lambda: len(imp.docs) == 1, 60)
    w.start_analysis(imp.docs[0])
    assert wait(lambda: c.project is not None, 600), "el análisis no terminó"
    assert len(c.project.score.measures) == 17
    w.go_step(2)
    app.processEvents()
    issue = next((i for i in c.project.issues if i.fix is not None and i.status == "abierta"), None)
    if issue is not None:
        c.accept_issue(issue)
        assert c.history.can_undo()
    w.go_step(4)
    ep = w.page_export
    ep.e_dir.setText(str(tmp_path))
    ep.e_name.setText("flujo")
    ep.export()
    assert wait(lambda: ep.results.count() > 0, 120)
    assert all(ep.results.item(i).text().startswith("✔") for i in range(ep.results.count()))
    assert (tmp_path / "flujo.mid").exists() and (tmp_path / "flujo.musicxml").exists()
    c.history.clear()
    c.project.dirty = False
    w.close()


def test_multipage_continuity_the_pool(qapp_offscreen):
    path = SAMPLES / "The Pool.pdf"
    if not path.exists():
        pytest.skip("archivo de ejemplo no disponible")
    cfg, project = _convert(path, "auto", "auto")  # como lo usa el programa: HOMR completa si hace falta
    pages = [m.page for m in project.score.measures if m.page is not None]
    assert pages == sorted(pages), "los compases siguen el orden de las páginas"
    assert len(set(pages)) == 8
    # El compás 69 (último) termina la pieza y la duración total es razonable (sin páginas duplicadas)
    from note_cascade.music_model.timeline import build_performance
    perf = build_performance(project.score)
    assert 120 < perf.total_seconds < 400
    tempos = [t for t in project.score.tempos if t.bpm_quarter]
    assert tempos, "el tempo se recupera del texto del PDF"
    by_number = {project.score.measures[t.measure].number: t.bpm_quarter for t in tempos}
    assert by_number.get("39") == pytest.approx(112.5), "«♩. = 75» va al compás 39, no al principio del sistema anterior"
    shifts = [d for d in project.score.directions if d.kind == "octave_shift" and d.source == "texto"]
    assert [project.score.measures[d.measure].number for d in shifts] == ["17", "21"], "la 8va de los c. 17–21"
    # Reglas generales: compás 3/8 escondido, redondas que Audiveris no ve, comprobación con la imagen
    ts = {m.number: m.time_sig for m in project.score.measures}
    assert ts["1"] == (12, 8) and ts["60"] == (3, 8) and ts["61"] == (12, 8)
    m7 = next(m for m in project.score.measures if m.number == "7")
    assert len([n for n in project.score.notes_in_measure(m7.index, include_rests=False) if n.staff == 2]) == 3
    assert sum(n.image_verified for n in project.score.notes) > 1000
    errors = [i for i in project.issues if i.severity == "error" and i.status == "abierta"]
    assert len(errors) <= 2, [i.title for i in errors]
    # Comparación completa con el PDF: calderón falso del c. 7, ritmo del c. 16 y ligadura 41→42 (cruza de página)
    by_num = {m.number: m.index for m in project.score.measures}
    assert not any("fermata" in n.articulations for n in project.score.notes_in_measure(by_num["7"]))
    lh16 = sorted({n.onset for n in project.score.notes_in_measure(by_num["16"], include_rests=False) if n.staff == 2})
    assert [float(x) for x in lh16] == [0, 1, 1.5, 2.5, 3, 4, 4.5, 5.5]
    end41 = [n for n in project.score.notes_in_measure(by_num["41"], include_rests=False) if n.staff == 1]
    last = max(n.onset for n in end41)
    assert all(n.tie_start for n in end41 if n.onset == last)


# ---------------------------------------------------------------------------
# Alturas reales: octavas, claves y líneas adicionales (test_samples/)
# ---------------------------------------------------------------------------

TEST_SAMPLES = Path(__file__).resolve().parents[1] / "test_samples"


def _sample(prefix: str) -> Path:
    found = sorted(TEST_SAMPLES.glob(prefix + "*.pdf")) if TEST_SAMPLES.exists() else []
    if not found:
        pytest.skip(f"falta test_samples/{prefix}*.pdf")
    return found[0]


def _measure_notes(score, number: str, staff: int):
    mi = next(m.index for m in score.measures if m.number == number)
    return [n for n in score.notes_in_measure(mi, include_rests=False) if n.staff == staff]


def _no_pitch_differences(project, measures: set[str] | None = None):
    from note_cascade.omr.pitch_trace import trace_notes
    traces = trace_notes(project.score, project)
    bad = [(t.measure, t.staff, t.expected_midi, t.model_midi, t.origin) for t in traces
           if t.status == "diferencia" and (measures is None or t.measure in measures)]
    assert not bad, bad
    return traces


def test_sinner_finale_octave_lines_clefs_and_ledger_lines(qapp_offscreen):
    cfg, project = _convert(_sample("Sinner"), "auto", "auto")
    s = project.score
    # 8va de la mano derecha (páginas 2–3): suena una octava sobre lo escrito
    assert max(n.midi for n in _measure_notes(s, "17", 1)) == 89                    # Fa5 escrito → Fa6
    assert {n.midi for n in _measure_notes(s, "37", 1)} == {84, 96, 93, 105}      # Do6 Do7 La6 La7
    assert {n.midi for n in _measure_notes(s, "38", 1)} == {89, 101}              # Fa6 Fa7
    # graves de la izquierda con 4 líneas adicionales: sin desplazar (c. 37–41)
    assert min(n.midi for n in _measure_notes(s, "37", 2)) == 29                  # Fa1
    assert min(n.midi for n in _measure_notes(s, "38", 2)) == 34                  # Si♭1
    # la 8va de la derecha nunca mueve la izquierda
    for mi in range(16, 47):
        for n in s.notes_in_measure(mi, include_rests=False):
            if n.staff == 2:
                assert not any("8va" in x for x in n.pitch_log), (s.measures[mi].number, n.name(), n.pitch_log)
    # la «8vb» que el motor se inventa en el c. 31 no se aplica (solo se avisa)
    assert all(n.midi == n.engine_midi for n in _measure_notes(s, "31", 2))
    assert any(i.code == "octava_motor_dudosa" and s.measures[i.measure].number == "31" for i in project.issues)
    # c. 58: la izquierda pasa a clave de sol (el motor no lo vio) y lleva su propia 8va; c. 59 vuelve a fa
    assert any(c.staff == 2 and c.sign == "G" and s.measures[c.measure].number == "58" for c in s.clefs)
    assert {n.midi for n in _measure_notes(s, "58", 2) if n.onset == 1 and n.position and not n.position.approximate} \
        == {93, 94, 98}                                                            # La6 Si♭6 Re7
    assert {n.midi for n in _measure_notes(s, "59", 2) if n.onset == 2} == {51, 55, 58, 62}   # Mi♭3 Sol3 Si♭3 Re4
    _no_pitch_differences(project)
    # el MIDI exportado conserva las mismas alturas que el modelo
    _check_export_pitches(project, cfg, tmp_dir=Path(project.workspace))


def test_the_pool_ledger_lines_and_arpeggio_keep_their_register(qapp_offscreen):
    cfg, project = _convert(_sample("The Pool"), "auto", "auto")
    s = project.score
    _no_pitch_differences(project, {"38", "39", "40", "41"})
    lh = sorted(_measure_notes(s, "39", 2), key=lambda n: (n.onset, n.midi))
    assert [n.midi for n in lh[:7]] == [33, 45, 45, 52, 57, 61, 64]            # La1/La2 y arpegio que sube
    assert {n.midi for n in _measure_notes(s, "39", 1) if n.onset == 0} == {80, 85, 92}   # Sol♯5 Do♯6 Sol♯6
    # en c. 38–41 no hay indicación de octava: ninguna nota se desplaza
    for num in ("38", "39", "40", "41"):
        for staff in (1, 2):
            assert all(not any("indicación" in x for x in n.pitch_log) for n in _measure_notes(s, num, staff))
    _check_export_pitches(project, cfg, tmp_dir=Path(project.workspace))


def test_the_pool_positions_accidentals_ties_and_tempo_figures(qapp_offscreen):
    """Lo que se vio en el informe de «The Pool»: c. 44 desplazado (se proponía quitar dos notas que están),
    el ♮ del c. 24 leído como otra nota, las tres ligaduras 41→42 y la figura «♩.» de cada tempo."""
    cfg, project = _convert(_sample("The Pool"), "auto", "auto")
    s = project.score
    by = {m.number: m for m in s.measures}
    # c. 44: la armadura de cortesía tras la doble barra no es un compás → posiciones exactas de Audiveris
    assert not by["44"].position.approximate
    g = [n for n in _measure_notes(s, "44", 1) if n.midi == 56]
    assert len(g) == 2 and all(n.image_verified and not n.position.approximate for n in g)
    lh = {n.onset: n for n in _measure_notes(s, "44", 2)}
    assert all(abs(n.position.x - lh[n.onset].position.x) < 0.005 for n in g), "cada Sol♯3 en la columna de su tiempo"
    assert not any(i.code in ("nota_sin_imagen", "barras_imagen") and i.measure == by["44"].index
                   for i in project.issues)
    # c. 24: el ♮ delante de Do5 no es otra nota; el ♮ sigue valiendo en el segundo acorde
    assert sorted(n.midi for n in _measure_notes(s, "24", 1) if n.onset == 0) == [68, 72, 80]
    assert sorted(n.midi for n in _measure_notes(s, "24", 1) if n.onset == 3) == [69, 72, 81]
    assert any(i.code == "alteracion_como_nota" and i.measure == by["24"].index for i in project.issues)
    assert not any(i.code == "alteracion_sin_justificar" and i.measure == by["24"].index for i in project.issues)
    # c. 41 → 42: tres ligaduras (Si5, Mi6, Si6) que cruzan de página
    end41 = [n for n in _measure_notes(s, "41", 1) if n.onset == max(x.onset for x in _measure_notes(s, "41", 1))]
    assert sorted(n.midi for n in end41 if n.tie_start) == [83, 88, 95]
    # tempos: la figura dibujada es negra con puntillo en las siete indicaciones
    texts = [t for t in s.tempos if t.source == "texto" and t.per_minute]
    assert len(texts) == 7
    assert all((t.beat_unit, t.beat_dots) == ("quarter", 1) and t.bpm_quarter == pytest.approx(1.5 * t.per_minute)
               for t in texts)
    assert all(i.evidence == "comprobado" for i in project.issues if i.code == "tempo_texto")
    # c. 14: acorde arpegiado (línea ondulada) que solo vio HOMR. Sus cinco notas están en la imagen: no se
    # marcan «revisar» y se tocan de abajo arriba
    from note_cascade.music_model.arpeggio import is_arpeggiated
    from note_cascade.music_model.timeline import build_performance
    status = project.options["status"]["notes"]
    c14 = [n for st in (1, 2) for n in _measure_notes(s, "14", st) if n.onset == 0]
    assert sorted(n.midi for n in c14) == [56, 59, 63, 64, 68]                  # Sol♯3 Si3 | Re♯4 Mi4 Sol♯4
    assert all(status[n.id] == "ok" and is_arpeggiated(s, n) for n in c14)
    rh = {n.id for n in c14 if n.staff == 1}
    starts = sorted(pn.start_s for pn in build_performance(s).notes if pn.note_ids[0] in rh)
    assert starts[0] < starts[1] < starts[2]
    # c. 17–21: la «8va» con rótulo y línea medidos en la imagen es información; no marca sus 46 notas
    octave = next(i for i in project.issues if i.code == "octava_texto")
    assert octave.severity == "info"
    assert not [nid for nid in octave.note_ids if status.get(nid) == "revisar"
                and not any(nid in i.note_ids for i in project.issues
                            if i.status == "abierta" and i.severity != "info")]


def _check_export_pitches(project, cfg, tmp_dir: Path):
    import mido
    from note_cascade.export.exporter import performance_options
    from note_cascade.midi.writer import write_midi
    from note_cascade.music_model.timeline import build_performance
    perf = build_performance(project.score, performance_options(cfg, True))
    path = tmp_dir / "alturas_verificacion.mid"
    write_midi(project.score, perf, path)
    mid = mido.MidiFile(str(path))
    exported = sorted(m.note for tr in mid.tracks for m in tr if m.type == "note_on" and m.velocity > 0)
    assert exported == sorted(p.midi for p in perf.notes)
