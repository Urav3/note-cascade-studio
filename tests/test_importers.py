"""Importación: detección por contenido, imágenes problemáticas, PDF de todos los tipos."""

from __future__ import annotations

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image

from note_cascade.app.errors import CorruptFileError, UnsupportedFileError
from note_cascade.importers import image_io
from note_cascade.importers.detect import detect_file
from note_cascade.importers.document import document_from_image, open_document


def _staff_image(w=1200, h=900, color=False) -> np.ndarray:
    img = np.full((h, w), 255, np.uint8)
    for s in range(3):
        top = 120 + s * 250
        for i in range(5):
            img[top + i * 20: top + i * 20 + 2, 80:w - 80] = 0
        img[top:top + 82, 80:83] = 0
        img[top:top + 82, w - 83:w - 80] = 0
    return np.dstack([img] * 3) if color else img


def test_detects_by_content_not_extension(tmp_path):
    png = tmp_path / "partitura.pdf"  # extensión engañosa
    Image.fromarray(_staff_image()).save(png, format="PNG")
    fk = detect_file(png)
    assert fk.kind == "image" and fk.subtype == "png"
    assert "no coincide" in fk.note


def test_empty_and_unsupported_files(tmp_path):
    empty = tmp_path / "vacio.pdf"
    empty.write_bytes(b"")
    with pytest.raises(CorruptFileError):
        detect_file(empty)
    txt = tmp_path / "notas.txt"
    txt.write_text("esto no es una partitura")
    with pytest.raises(UnsupportedFileError):
        detect_file(txt)
    bad = tmp_path / "roto.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
    with pytest.raises(CorruptFileError):
        open_document(bad)


def test_damaged_pdf_reports_error(tmp_path):
    bad = tmp_path / "danado.pdf"
    bad.write_bytes(b"%PDF-1.4\n%garbage without objects\n")
    with pytest.raises(CorruptFileError):
        open_document(bad)


def test_png_with_ink_only_in_alpha_is_composited_on_white(tmp_path):
    """Caso real («mi foto.png»): RGB = 0 y la tinta solo en el canal alfa."""
    ink = 255 - _staff_image()
    rgba = np.zeros((*ink.shape, 4), np.uint8)
    rgba[..., 3] = ink
    path = tmp_path / "captura.png"
    Image.fromarray(rgba, "RGBA").save(path)
    with Image.open(path) as im:
        assert image_io.alpha_only_ink(im)
    frames = image_io.load_image_frames(path)
    gray = frames[0].mean(axis=2)
    assert gray.mean() > 200, "el fondo transparente debe quedar blanco, no negro"
    assert gray.min() < 50, "la tinta debe conservarse"


def test_16bit_cmyk_and_multipage_tiff(tmp_path):
    arr16 = (_staff_image().astype(np.uint16) * 257)
    p16 = tmp_path / "gris16.png"
    Image.fromarray(arr16, "I;16").save(p16)
    assert image_io.load_image_frames(p16)[0].shape[2] == 3
    cmyk = tmp_path / "cmyk.jpg"
    Image.fromarray(_staff_image(color=True)).convert("CMYK").save(cmyk)
    assert image_io.load_image_frames(cmyk)[0].mean() > 150
    tif = tmp_path / "varias.tif"
    pages = [Image.fromarray(_staff_image()) for _ in range(3)]
    pages[0].save(tif, save_all=True, append_images=pages[1:])
    doc = open_document(tif)
    assert len(doc.pages) == 3


def test_exif_orientation_is_applied(tmp_path):
    img = Image.fromarray(_staff_image(color=True))
    exif = img.getexif()
    exif[0x0112] = 6  # girada 90° (foto de móvil)
    path = tmp_path / "movil.jpg"
    img.save(path, exif=exif)
    arr = image_io.load_image_frames(path)[0]
    assert arr.shape[0] > arr.shape[1]


def _pdf(tmp_path, kind: str):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if kind in ("vector", "mixed"):
        for s in range(4):
            top = 120 + s * 150
            for i in range(5):
                page.draw_line((50, top + i * 7), (545, top + i * 7), width=0.6)
            for x in range(80, 540, 30):  # cabezas de nota vectoriales
                page.draw_oval(fitz.Rect(x, top + 10, x + 8, top + 16), fill=(0, 0, 0))
        page.insert_text((200, 60), "Sonata de prueba", fontsize=20)
        page.insert_text((60, 112), "Allegro", fontsize=11)
    if kind in ("raster", "mixed"):
        png = tmp_path / "img.png"
        Image.fromarray(_staff_image()).save(png)
        r = fitz.Rect(40, 400, 555, 800) if kind == "mixed" else fitz.Rect(30, 30, 565, 812)
        page.insert_image(r, filename=str(png))
    path = tmp_path / f"{kind}.pdf"
    doc.save(path)
    return path


@pytest.mark.parametrize("kind,expected", [("vector", "vector"), ("raster", "raster"), ("mixed", {"mixed", "vector"})])
def test_pdf_page_kinds(tmp_path, kind, expected):
    doc = open_document(_pdf(tmp_path, kind))
    info = doc.pages[0].pdf_info
    assert (info.kind in expected) if isinstance(expected, set) else info.kind == expected
    img = doc.pages[0].render(150)
    assert img.shape[2] == 3 and img.shape[0] > 1000


def test_text_layer_is_extracted_with_positions(tmp_path):
    doc = open_document(_pdf(tmp_path, "vector"))
    words = [w.text for w in doc.pages[0].pdf_info.words]
    assert "Sonata" in words and "Allegro" in words
    w = next(w for w in doc.pages[0].pdf_info.words if w.text == "Sonata")
    assert 0 < w.x0 < w.x1 < 1 and 0 < w.y0 < w.y1 < 0.2


def test_rotated_pdf_page_renders_upright(tmp_path):
    path = _pdf(tmp_path, "raster")
    doc = fitz.open(path)
    doc[0].set_rotation(90)
    rotated = tmp_path / "girado.pdf"
    doc.save(rotated)
    page = open_document(rotated).pages[0]
    img = page.render(72)
    assert img.shape[1] > img.shape[0], "la rotación de la página debe aplicarse al rasterizar"


def test_multipage_pdf_and_password(tmp_path):
    doc = fitz.open()
    for _ in range(5):
        doc.new_page().insert_text((72, 72), "pagina")
    path = tmp_path / "multi.pdf"
    doc.save(path)
    assert len(open_document(path).pages) == 5
    enc = tmp_path / "clave.pdf"
    doc.save(enc, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secreto", owner_pw="x")
    with pytest.raises(CorruptFileError, match="contraseña"):
        open_document(enc)


def test_clipboard_document():
    doc = document_from_image(_staff_image(color=True), "Captura 1")
    assert doc.kind == "clipboard" and doc.needs_omr and len(doc.pages) == 1


def test_real_samples_open(conftest_sample=None):
    from conftest import SAMPLES
    for name in ("The Pool.pdf", "beethoven.pdf", "claro de luna.pdf", "mi foto.png"):
        p = SAMPLES / name
        if not p.exists():
            continue
        doc = open_document(p)
        assert doc.pages, name
