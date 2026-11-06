"""Documento de origen: PDF, imagen(es), captura del portapapeles o música estructurada."""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from note_cascade.app.errors import CorruptFileError, UnsupportedFileError
from note_cascade.importers import image_io
from note_cascade.importers.detect import FileKind, detect_file
from note_cascade.pdf_processing import pdf_document as pdfdoc

log = logging.getLogger(__name__)


@dataclass
class SourcePage:
    index: int
    label: str
    origin: str                      # pdf | image | clipboard
    kind_hint: str = "image"         # vector | raster | mixed | blank | image
    width_in: float | None = None
    height_in: float | None = None
    native_dpi: float | None = None
    pdf_info: pdfdoc.PdfPageInfo | None = None
    _doc: "SourceDocument | None" = field(default=None, repr=False)
    _image: np.ndarray | None = field(default=None, repr=False)

    def render(self, dpi: float | None = None) -> np.ndarray:
        """Imagen RGB de la página. Para PDF se rasteriza a ``dpi``."""
        if self.origin == "pdf":
            assert self._doc is not None
            return self._doc.render_pdf_page(self.index, dpi or 300)
        if self._image is None:
            raise CorruptFileError("La imagen de la página ya no está disponible.")
        return self._image

    def thumbnail(self, max_side: int = 360) -> np.ndarray:
        import cv2
        if self.origin == "pdf":
            w_in = self.width_in or 8.5
            h_in = self.height_in or 11
            dpi = max(20, min(150, max_side / max(w_in, h_in)))
            img = self.render(dpi)
        else:
            img = self.render()
        h, w = img.shape[:2]
        scale = max_side / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        return img


@dataclass
class SourceDocument:
    path: Path | None
    kind: str                        # pdf | image | clipboard | musicxml | mxl | midi | project
    name: str
    pages: list[SourcePage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_kind: FileKind | None = None
    sha256: str = ""
    _pdf = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def is_structured(self) -> bool:
        return self.kind in ("musicxml", "mxl", "midi")

    @property
    def needs_omr(self) -> bool:
        return self.kind in ("pdf", "image", "clipboard")

    # ------------------------------------------------------------------
    def render_pdf_page(self, index: int, dpi: float) -> np.ndarray:
        with self._lock:
            if self._pdf is None:
                assert self.path is not None
                self._pdf = pdfdoc.open_pdf(self.path)
            return pdfdoc.render_page(self._pdf, index, dpi)

    def close(self) -> None:
        with self._lock:
            if self._pdf is not None:
                try:
                    self._pdf.close()
                except Exception:
                    pass
                self._pdf = None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_document(path: Path | str) -> SourceDocument:
    path = Path(path)
    fk = detect_file(path)
    doc = SourceDocument(path=path, kind=fk.kind, name=path.stem, file_kind=fk)
    if fk.note:
        doc.warnings.append(fk.note)
    try:
        doc.sha256 = file_sha256(path)
    except OSError:
        doc.sha256 = ""
    if fk.kind == "pdf":
        pdf = pdfdoc.open_pdf(path)
        try:
            for i in range(pdf.page_count):
                try:
                    info = pdfdoc.analyze_page(pdf, i)
                except Exception as exc:
                    log.warning("No se pudo analizar la página %s: %s", i + 1, exc)
                    info = pdfdoc.PdfPageInfo(i, pdf[i].rect.width, pdf[i].rect.height, pdf[i].rotation)
                    doc.warnings.append(f"La página {i + 1} tiene contenido que no se pudo analizar.")
                page = SourcePage(index=i, label=f"Página {i + 1}", origin="pdf", kind_hint=info.kind,
                                  width_in=info.width_in, height_in=info.height_in,
                                  native_dpi=info.image_dpi, pdf_info=info)
                page._doc = doc
                doc.pages.append(page)
        finally:
            pdf.close()
    elif fk.kind == "image":
        frames = image_io.load_image_frames(path)
        dpi = image_io.image_dpi(path)
        for i, arr in enumerate(frames):
            label = "Imagen" if len(frames) == 1 else f"Página {i + 1}"
            page = SourcePage(index=i, label=label, origin="image", kind_hint="image", native_dpi=dpi)
            if dpi:
                page.width_in = arr.shape[1] / dpi
                page.height_in = arr.shape[0] / dpi
            page._image = arr
            page._doc = doc
            doc.pages.append(page)
    elif fk.kind in ("musicxml", "mxl", "midi", "project"):
        pass
    else:  # pragma: no cover - detect_file ya filtra
        raise UnsupportedFileError(f"Formato no compatible: {path.name}")
    return doc


def document_from_image(arr: np.ndarray, name: str = "Captura") -> SourceDocument:
    doc = SourceDocument(path=None, kind="clipboard", name=name)
    page = SourcePage(index=0, label="Captura", origin="clipboard", kind_hint="image")
    page._image = arr
    page._doc = doc
    doc.pages.append(page)
    doc.sha256 = hashlib.sha256(arr.tobytes()).hexdigest()
    return doc
