"""Preparación de imagen y detección de pentagramas."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from note_cascade.image_processing.pipeline import PreprocessOptions, preprocess
from note_cascade.image_processing.staff_detection import detect_staves


def synthetic_page(systems=3, staves_per_system=2, il=20, width=1700, height=2200, measures=4) -> np.ndarray:
    img = np.full((height, width), 255, np.uint8)
    y = 200
    for _s in range(systems):
        tops = []
        for k in range(staves_per_system):
            top = y + k * il * 9
            tops.append(top)
            for i in range(5):
                img[top + i * il: top + i * il + 2, 150:width - 100] = 0
            # cabezas de nota
            for x in range(260, width - 160, 90):
                cv2.ellipse(img, (x, top + il * 2 + (x // 90 % 4 - 2) * il // 2), (11, 8), -20, 0, 360, 0, -1)
                cv2.line(img, (x + 10, top + il * 2), (x + 10, top - il), 0, 2)
        bottom = tops[-1] + 4 * il + 2
        for b in np.linspace(150, width - 102, measures + 1):
            img[tops[0]:bottom, int(b):int(b) + 3] = 0
        y = bottom + il * 8
    return img


def test_detects_staves_systems_and_barlines():
    g = detect_staves(synthetic_page())
    assert len(g.staves) == 6
    assert [len(s.staves) for s in g.systems] == [2, 2, 2]
    assert abs(g.interline - 20) < 1.5
    for sys in g.systems:
        assert len(sys.barlines) == 4  # 4 compases por sistema
        assert len(sys.measure_spans(20)) == 4


def test_single_staff_systems():
    g = detect_staves(synthetic_page(systems=4, staves_per_system=1))
    assert len(g.staves) == 4 and len(g.systems) == 4


def test_blank_page_has_no_music():
    blank = np.full((1500, 1100), 255, np.uint8)
    cv2.putText(blank, "Prefacio", (200, 300), cv2.FONT_HERSHEY_SIMPLEX, 3, 0, 5)
    pp = preprocess(np.dstack([blank] * 3), 0, None, PreprocessOptions())
    assert not pp.has_music
    assert pp.warnings


def test_clean_image_is_not_modified():
    page = np.dstack([synthetic_page()] * 3)
    pp = preprocess(page, 0, None, PreprocessOptions())
    assert any("no se aplicaron correcciones" in s for s in pp.steps)
    assert np.allclose(pp.matrix, np.eye(3))


@pytest.mark.parametrize("angle", [1.5, -2.5])
def test_skew_is_corrected(angle):
    page = synthetic_page()
    h, w = page.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rot = cv2.warpAffine(page, m, (w, h), borderValue=255)
    pp = preprocess(np.dstack([rot] * 3), 0, None, PreprocessOptions())
    assert len(pp.geometry.staves) == 6
    assert any("inclinación" in s for s in pp.steps)
    assert abs(pp.geometry.skew_deg) < 0.3


def test_upside_down_and_rotated_pages():
    page = synthetic_page()
    # Clave ancha al comienzo de cada pentagrama para que la orientación sea detectable
    for sys_top in (200, 200 + 20 * 9):
        pass
    img = page.copy()
    for g in detect_staves(img).staves:
        cv2.rectangle(img, (155, int(g.top) - 20), (200, int(g.bottom) + 20), 0, -1)
    up = preprocess(np.dstack([np.rot90(img, 2).copy()] * 3), 0, None, PreprocessOptions())
    assert any("al revés" in s for s in up.steps)
    side = preprocess(np.dstack([np.rot90(img, 1).copy()] * 3), 0, None, PreprocessOptions())
    assert any("90°" in s for s in side.steps)
    assert len(side.geometry.staves) == 6


def test_photo_perspective_shadow_and_small_image_upscaling():
    page = np.dstack([synthetic_page()] * 3)
    h, w = page.shape[:2]
    canvas_h, canvas_w = int(h * 1.25), int(w * 1.25)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[w * 0.08, h * 0.06], [w * 1.15, h * 0.10], [w * 1.10, h * 1.20], [w * 0.12, h * 1.17]])
    m = cv2.getPerspectiveTransform(src, dst)
    canvas = np.full((canvas_h, canvas_w, 3), 60, np.uint8)
    warped = cv2.warpPerspective(page, m, (canvas_w, canvas_h), dst=canvas, borderMode=cv2.BORDER_TRANSPARENT)
    shade = (0.6 + 0.4 * (np.arange(canvas_w) / canvas_w)).astype(np.float32)
    photo = np.clip(warped * shade[None, :, None], 0, 255).astype(np.uint8)
    pp = preprocess(photo, 0, None, PreprocessOptions())
    assert pp.quality.kind == "foto"
    assert any("perspectiva" in s for s in pp.steps)
    assert len(pp.geometry.staves) == 6
    small = cv2.resize(page, None, fx=0.33, fy=0.33, interpolation=cv2.INTER_AREA)
    ps = preprocess(small, 0, None, PreprocessOptions())
    assert any("Ampliación" in s for s in ps.steps)
    assert ps.geometry.interline >= 15


def test_coordinates_round_trip_through_matrix():
    page = synthetic_page()
    h, w = page.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 2.0, 1.0)
    rot = np.dstack([cv2.warpAffine(page, m, (w, h), borderValue=255)] * 3)
    pp = preprocess(rot, 0, None, PreprocessOptions())
    for nx, ny in ((0.2, 0.3), (0.7, 0.8)):
        px, py = pp.normalized_to_processed(nx, ny)
        bx, by = pp.processed_to_normalized(px, py)
        assert abs(bx - nx) < 1e-6 and abs(by - ny) < 1e-6
