"""Análisis y rasterización de páginas PDF con PyMuPDF.

Cada página se examina por separado: un PDF puede mezclar notación vectorial,
imágenes escaneadas, fotografías y texto digital. La presencia de una capa de
texto **no** implica que las notas estén disponibles como datos: el texto se
usa solo para clasificar títulos, tempos, números de compás, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf as fitz

from note_cascade.app.errors import CorruptFileError

log = logging.getLogger(__name__)

MUSIC_FONT_HINTS = ("bravura", "leland", "emmentaler", "petaluma", "maestro", "opus", "sonata", "finale",
                    "musescore", "gonville", "feta", "november", "lilyjazz", "mtf-", "smufl")


@dataclass
class TextSpan:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float          # altura de fuente normalizada a la altura de página
    font: str = ""
    bold: bool = False
    italic: bool = False
    source: str = "pdf"  # pdf | ocr | audiveris
    confidence: float | None = None

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    def to_dict(self) -> dict:
        return {"text": self.text, "x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1, "size": self.size,
                "font": self.font, "bold": self.bold, "italic": self.italic, "source": self.source,
                "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "TextSpan":
        return cls(**{k: d.get(k) for k in ("text", "x0", "y0", "x1", "y1", "size", "font", "bold", "italic",
                                             "source", "confidence")})


@dataclass
class PdfPageInfo:
    index: int
    width_pt: float
    height_pt: float
    rotation: int
    words: list[TextSpan] = field(default_factory=list)
    image_coverage: float = 0.0
    image_dpi: float | None = None
    image_count: int = 0
    drawing_count: int = 0
    char_count: int = 0
    music_font: bool = False
    kind: str = "blank"   # vector | raster | mixed | blank

    @property
    def width_in(self) -> float:
        return self.width_pt / 72.0

    @property
    def height_in(self) -> float:
        return self.height_pt / 72.0


def open_pdf(path: Path | str) -> fitz.Document:
    path = Path(path)
    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # fitz lanza RuntimeError/FileDataError según la versión
        raise CorruptFileError(f"El PDF «{path.name}» está dañado y no se puede abrir.",
                               "Prueba a abrirlo con un visor de PDF y a «Imprimir como PDF» para repararlo.",
                               str(exc)) from exc
    if doc.needs_pass:
        doc.close()
        raise CorruptFileError(f"El PDF «{path.name}» está protegido con contraseña.",
                               "Ábrelo con la contraseña en un visor de PDF y guárdalo sin protección.")
    if doc.page_count == 0:
        doc.close()
        raise CorruptFileError(f"El PDF «{path.name}» no contiene páginas.")
    return doc


def analyze_page(doc: fitz.Document, index: int) -> PdfPageInfo:
    page = doc[index]
    rect = page.rect
    info = PdfPageInfo(index, rect.width, rect.height, page.rotation)
    page_area = max(1.0, rect.width * rect.height)
    try:
        img_infos = page.get_image_info()
    except Exception:
        img_infos = []
    covered = 0.0
    best_dpi = None
    for im in img_infos:
        bbox = fitz.Rect(im.get("bbox", (0, 0, 0, 0))) & rect
        if bbox.is_empty:
            continue
        area = bbox.width * bbox.height
        covered += area
        w_px, h_px = im.get("width", 0), im.get("height", 0)
        if bbox.width > 1 and w_px:
            dpi = w_px / (bbox.width / 72.0)
            if area / page_area > 0.02:
                best_dpi = max(best_dpi or 0, dpi)
    info.image_coverage = min(1.0, covered / page_area)
    info.image_dpi = best_dpi
    info.image_count = len(img_infos)
    try:
        info.drawing_count = len(page.get_cdrawings()) if hasattr(page, "get_cdrawings") else len(page.get_drawings())
    except Exception:
        info.drawing_count = 0
    info.words = _extract_words(page)
    info.char_count = sum(len(w.text) for w in info.words)
    try:
        fonts = page.get_fonts()
        info.music_font = any(any(h in (f[3] or "").lower() for h in MUSIC_FONT_HINTS) for f in fonts)
    except Exception:
        info.music_font = False
    if info.image_coverage > 0.5 and info.drawing_count < 200:
        info.kind = "raster"
    elif info.drawing_count >= 50 or info.music_font:
        info.kind = "mixed" if info.image_coverage > 0.2 else "vector"
    elif info.image_coverage > 0.05:
        info.kind = "raster"
    elif info.char_count > 0 or info.drawing_count > 0:
        info.kind = "vector"
    else:
        info.kind = "blank"
    return info


def _extract_words(page: fitz.Page) -> list[TextSpan]:
    words: list[TextSpan] = []
    rect = page.rect
    W, H = max(1.0, rect.width), max(1.0, rect.height)
    try:
        raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    except Exception:
        return words
    rot = page.rotation_matrix
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font = span.get("font", "")
                flags = span.get("flags", 0)
                size = float(span.get("size", 0))
                chars = span.get("chars", [])
                current: list[dict] = []

                def flush() -> None:
                    if not current:
                        return
                    text = "".join(c["c"] for c in current).strip()
                    if text:
                        r = fitz.Rect(current[0]["bbox"])
                        for c in current[1:]:
                            r |= fitz.Rect(c["bbox"])
                        r = r * rot
                        r.normalize()
                        words.append(TextSpan(
                            text=text, x0=r.x0 / W, y0=r.y0 / H, x1=r.x1 / W, y1=r.y1 / H,
                            size=size / H, font=font, bold=bool(flags & 16) or "bold" in font.lower(),
                            italic=bool(flags & 2) or "italic" in font.lower(), source="pdf"))
                    current.clear()

                for ch in chars:
                    c = ch.get("c", "")
                    if c.isspace():
                        flush()
                    else:
                        current.append(ch)
                flush()
    return words


def render_page(doc: fitz.Document, index: int, dpi: float, gray: bool = False) -> np.ndarray:
    page = doc[index]
    dpi = float(max(36, min(dpi, 1200)))
    # Limitar a ~70 megapíxeles para no agotar la memoria con páginas enormes.
    w_in, h_in = page.rect.width / 72.0, page.rect.height / 72.0
    max_pixels = 70_000_000
    if w_in * h_in * dpi * dpi > max_pixels:
        dpi = (max_pixels / max(1e-6, w_in * h_in)) ** 0.5
    pix = page.get_pixmap(dpi=int(round(dpi)), colorspace=fitz.csGRAY if gray else fitz.csRGB, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if gray:
        return arr[..., 0].copy()
    return arr.copy()
