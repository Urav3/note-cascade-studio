"""Pruebas de la interfaz (sin mostrar ventanas)."""

from __future__ import annotations

from fractions import Fraction as F

import pytest

import synthetic as S

pytestmark = pytest.mark.gui


@pytest.fixture()
def window(qapp_offscreen):
    from PySide6.QtCore import Qt
    from note_cascade.app.config import AppConfig
    from note_cascade.ui import theme
    from note_cascade.ui.controller import AppController
    from note_cascade.ui.main_window import MainWindow
    theme.apply_theme(qapp_offscreen, "oscuro")
    cfg = AppConfig()
    c = AppController(cfg)
    c.player._silent = True
    w = MainWindow(c)
    w.setAttribute(Qt.WA_DontShowOnScreen)
    w.resize(1400, 900)
    w.show()
    yield w, c
    # Evitar el diálogo modal «¿Guardar las correcciones?» al cerrar en las pruebas.
    c.history.clear()
    if c.project is not None:
        c.project.dirty = False
    w.close()


def _project():
    from note_cascade.pipeline.conversion import revalidate_project
    from note_cascade.app.config import AppConfig
    from note_cascade.project.project import Project
    p = Project.new_workspace("gui")
    p.score = S.piano_score()
    p.original_score = p.score.clone()
    revalidate_project(p, AppConfig())
    return p


def test_about_and_report_problem_point_to_the_repository(window, qapp_offscreen, monkeypatch):
    """«Licencias y ayuda» enlaza al código fuente (AGPL) y «Informar de un problema» copia el diagnóstico sin
    la carpeta de usuario y abre la página de avisos solo si se acepta."""
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    from PySide6.QtWidgets import QMessageBox, QTextBrowser
    from note_cascade import ISSUES_URL, REPO_URL, VERSION_LABEL
    from note_cascade.app.diagnostics import Check, DiagnosticReport
    from note_cascade.ui import dialogs
    w, c = window
    about = dialogs.AboutDialog(w)
    html = about.findChild(QTextBrowser).toHtml()
    assert REPO_URL in html and ISSUES_URL in html and VERSION_LABEL in html
    about.close()

    monkeypatch.setattr(dialogs.DiagnosticsDialog, "run", lambda self: None)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    dlg = dialogs.DiagnosticsDialog(c, w)
    assert not dlg.b_report.isEnabled()                     # hasta que termina el diagnóstico
    from pathlib import Path
    rep = DiagnosticReport([Check("Python", True, f"3.12 ({Path.home() / 'app' / 'python.exe'})")])
    dlg._show_report(rep)
    assert dlg.b_report.isEnabled()
    for answer, expected in ((QMessageBox.Cancel, []), (QMessageBox.Ok, [ISSUES_URL])):
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: answer))
        opened.clear()
        dlg.report_problem()
        copied = QGuiApplication.clipboard().text()
        assert "%USERPROFILE%" in copied and str(Path.home()) not in copied
        assert opened == expected
    dlg.close()


def test_steps_and_pages(window, qapp_offscreen):
    w, c = window
    assert not w.step_buttons["review"].isEnabled()
    c.set_project(_project())
    w._update_steps()
    for i in range(5):
        w.go_step(i)
        qapp_offscreen.processEvents()
        assert w.stack.currentIndex() == i
    img = w.grab()
    assert img.width() > 1000


def test_edit_from_piano_roll_and_undo(window, qapp_offscreen):
    w, c = window
    c.set_project(_project())
    w._update_steps()
    w.go_step(2)
    review = w.page_review
    note = next(n for n in c.score.notes if not n.rest and n.measure == 0)
    old = note.midi
    review._edit_move([note.id], 2, F(0))
    assert c.score.note_by_id(note.id).midi == old + 2
    assert c.history.can_undo()
    c.undo()
    assert c.score.note_by_id(note.id).midi == old
    c.redo()
    assert c.score.note_by_id(note.id).midi == old + 2
    review._edit_add(1, F(1, 2), 72, F(1))
    assert any(n.midi == 72 and n.measure == 1 and n.source == "usuario" for n in c.score.notes)


def test_accepting_a_proposed_fix_updates_score_and_status(window):
    w, c = window
    p = _project()
    p.score.notes = [x for x in p.score.notes if not (x.measure == 2 and x.staff == 1)]
    for k, name in enumerate(("E5", "F5", "F6", "E5")):  # Fa6 aislado: posible error de octava
        S._note(p.score, 2, k, 1, name, 1, 1, fifths=1)
    n = next(x for x in p.score.notes if x.measure == 2 and x.midi == 89)
    from note_cascade.pipeline.conversion import revalidate_project
    from note_cascade.app.config import AppConfig
    revalidate_project(p, AppConfig())
    c.set_project(p)
    issue = next(i for i in p.issues if i.code == "octava")
    c.accept_issue(issue)
    assert c.score.note_by_id(n.id).midi == 77 and c.score.note_by_id(n.id).name() == "Fa5"
    assert c.history.can_undo()
    assert not [i for i in c.project.issues if i.code == "octava" and i.status == "abierta" and n.id in i.note_ids]


def test_note_panel_says_why_a_note_is_marked_and_opens_the_warning(window, qapp_offscreen):
    """«No sé por qué me salen por revisar, no me dice qué pasa»: el panel «Nota» da el motivo y abre el aviso."""
    w, c = window
    p = _project()
    p.score.notes = [x for x in p.score.notes if not (x.measure == 2 and x.staff == 1)]
    for k, name in enumerate(("E5", "F5", "F6", "E5")):  # Fa6 aislado: posible error de octava
        S._note(p.score, 2, k, 1, name, 1, 1, fifths=1)
    from note_cascade.app.config import AppConfig
    from note_cascade.pipeline.conversion import revalidate_project
    revalidate_project(p, AppConfig())
    c.set_project(p)
    w._update_steps()
    w.go_step(2)
    review = w.page_review
    issue = next(i for i in p.issues if i.code == "octava")
    n = c.score.note_by_id(issue.note_ids[0])
    assert c.note_status(n.id) in ("revisar", "error")
    c.select([n.id])
    qapp_offscreen.processEvents()
    text = review.inspector.why.text()
    assert "Por qué" in text and issue.title in text and f'href="{issue.id}"' in text
    review.inspector._open_issue(issue.id)
    assert review.tabs.currentWidget() is review.issues and review.issues.current() is issue
    # varias notas: sin valores sobrantes de la nota anterior
    chord = [x for x in c.score.notes if x.measure == 1 and x.staff == 2 and x.onset == 0]
    c.select([x.id for x in chord])
    qapp_offscreen.processEvents()
    assert review.inspector.f_acc.currentIndex() == -1 and review.inspector.f_midi.text() == "—"
    assert "verificad" in review.inspector.why.text() or "revisar" in review.inspector.why.text() \
        or "sin datos" in review.inspector.why.text()
    # arpegiar el acorde desde el panel (y deshacerlo)
    assert review.inspector.b_arp.isEnabled() and review.inspector.b_arp.text() == "Arpegiar acorde"
    review.inspector._toggle_arpeggio()
    assert all("arpeggiate" in c.score.note_by_id(x.id).articulations for x in chord)
    assert review.inspector.b_arp.text() == "Quitar arpegio"
    c.undo()
    assert not any("arpeggiate" in c.score.note_by_id(x.id).articulations for x in chord)


def test_clipboard_image_with_transparency_is_composited(qapp_offscreen):
    """Ctrl+V de una captura con fondo transparente: la tinta no debe desaparecer."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage
    from note_cascade.ui.pages.import_page import qimage_to_numpy
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    for x in range(20, 180):
        img.setPixelColor(x, 50, QColor(0, 0, 0, 255))
    arr = qimage_to_numpy(img)
    assert arr.shape == (100, 200, 3)
    assert arr[10, 10].tolist() == [255, 255, 255]
    assert arr[50, 100].tolist() == [0, 0, 0]


def test_waterfall_renders_frames(qapp_offscreen):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from note_cascade.music_model.timeline import build_performance
    from note_cascade.piano_roll.waterfall import WaterfallRenderer
    r = WaterfallRenderer()
    perf = build_performance(S.piano_score())
    r.set_performance(perf)
    img = QImage(640, 360, QImage.Format_RGBA8888)
    p = QPainter(img)
    r.paint(p, QRectF(0, 0, 640, 360), 2.0)
    p.end()
    assert r.current_measure(2.0) == "1"
    assert img.pixelColor(320, 100).alpha() == 255
