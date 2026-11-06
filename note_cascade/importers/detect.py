"""Detección del tipo de archivo por su contenido (no solo por la extensión)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from note_cascade.app.errors import CorruptFileError, UnsupportedFileError

IMAGE_KINDS = {"png", "jpeg", "webp", "bmp", "tiff", "gif"}
SUPPORTED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
    ".musicxml", ".xml", ".mxl", ".mid", ".midi", ".ncsproj",
}
FILE_DIALOG_FILTER = (
    "Partituras y música (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.musicxml *.xml *.mxl *.mid *.midi);;"
    "PDF (*.pdf);;Imágenes (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;"
    "MusicXML (*.musicxml *.xml *.mxl);;MIDI (*.mid *.midi);;Todos los archivos (*.*)"
)


@dataclass
class FileKind:
    kind: str          # pdf | image | musicxml | mxl | midi | project
    subtype: str = ""  # png, jpeg, ... para imágenes
    note: str = ""     # aviso si la extensión no coincide con el contenido


def sniff_bytes(head: bytes) -> tuple[str, str] | None:
    if head.startswith(b"%PDF") or b"%PDF-" in head[:1024]:
        return "pdf", ""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "png"
    if head[:3] == b"\xff\xd8\xff":
        return "image", "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "webp"
    if head[:2] == b"BM":
        return "image", "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "image", "tiff"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "gif"
    if head[:4] == b"MThd":
        return "midi", ""
    if head[:4] == b"RIFF" and head[8:12] == b"RMID":
        return "midi", "rmid"
    if head[:2] == b"PK":
        return "zip", ""
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        text = head.decode("utf-8", "ignore")
        if "score-partwise" in text or "score-timewise" in text or "MusicXML" in text:
            return "musicxml", ""
        return "xml", ""
    return None


def detect_file(path: Path | str) -> FileKind:
    path = Path(path)
    if not path.exists():
        raise UnsupportedFileError(f"No se encuentra el archivo «{path.name}».",
                                   "Comprueba que no se haya movido o borrado.")
    if path.is_dir():
        raise UnsupportedFileError(f"«{path.name}» es una carpeta, no un archivo.")
    size = path.stat().st_size
    if size == 0:
        raise CorruptFileError(f"El archivo «{path.name}» está vacío (0 bytes).",
                               "Vuelve a descargarlo o a exportarlo.")
    with path.open("rb") as fh:
        head = fh.read(4096)
    sniffed = sniff_bytes(head)
    ext = path.suffix.lower()
    if sniffed is None:
        if ext in SUPPORTED_EXTENSIONS:
            raise CorruptFileError(
                f"El archivo «{path.name}» tiene extensión {ext} pero su contenido no corresponde a ese formato "
                "o está dañado.", "Ábrelo con otro programa para comprobarlo o vuelve a exportarlo.")
        raise UnsupportedFileError(
            f"El formato de «{path.name}» no es compatible.",
            "Formatos admitidos: PDF, PNG, JPG, WEBP, BMP, TIFF, MusicXML (.musicxml/.xml/.mxl) y MIDI.")
    kind, subtype = sniffed
    note = ""
    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
        except zipfile.BadZipFile as exc:
            raise CorruptFileError(f"El archivo comprimido «{path.name}» está dañado.", details=str(exc)) from exc
        if "project.json" in names:
            return FileKind("project")
        if "META-INF/container.xml" in names or any(n.lower().endswith((".xml", ".musicxml")) for n in names):
            return FileKind("mxl")
        raise UnsupportedFileError(f"«{path.name}» es un archivo comprimido que no contiene una partitura.")
    if kind == "xml":
        raise UnsupportedFileError(f"«{path.name}» es un XML que no parece MusicXML.",
                                   "Si es una partitura, expórtala de nuevo como MusicXML.")
    if kind == "image" and subtype == "gif":
        note = "Imagen GIF: se usará el primer fotograma."
    expected = {
        ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".bmp": "image",
        ".tif": "image", ".tiff": "image", ".musicxml": "musicxml", ".xml": "musicxml", ".mxl": "mxl",
        ".mid": "midi", ".midi": "midi",
    }.get(ext)
    if expected and expected != kind:
        note = f"La extensión {ext} no coincide con el contenido real ({kind}{'/' + subtype if subtype else ''}); se usa el contenido."
    return FileKind(kind, subtype, note)
