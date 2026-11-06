"""Carga robusta de imágenes.

Detalles que suelen romper otros conversores y que aquí se tratan:

* PNG con transparencia: muchas capturas exportadas guardan la tinta solo en el
  canal alfa (RGB = 0). Si se descarta el alfa la imagen queda totalmente negra.
  Se compone siempre sobre fondo blanco.
* Orientación EXIF de fotos de móvil.
* Imágenes de 16 bits, CMYK, paleta, escala de grises con alfa.
* TIFF de varias páginas.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from note_cascade.app.errors import CorruptFileError

Image.MAX_IMAGE_PIXELS = 400_000_000  # partituras escaneadas grandes son legítimas


def _to_rgb_on_white(img: Image.Image) -> Image.Image:
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(img, dtype=np.float64)
        hi = arr.max() or 1.0
        img = Image.fromarray(np.clip(arr / hi * 255.0, 0, 255).astype(np.uint8), mode="L")
    if img.mode == "F":
        arr = np.asarray(img, dtype=np.float64)
        lo, hi = float(arr.min()), float(arr.max())
        img = Image.fromarray(((arr - lo) / max(1e-9, hi - lo) * 255).astype(np.uint8), mode="L")
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("LA", "PA"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        background.alpha_composite(img)
        return background.convert("RGB")
    if img.mode == "CMYK":
        return img.convert("RGB")
    if img.mode == "1":
        return img.convert("L").convert("RGB")
    if img.mode not in ("RGB", "L"):
        return img.convert("RGB")
    return img.convert("RGB") if img.mode == "L" else img


def alpha_only_ink(img: Image.Image) -> bool:
    """True si la imagen guarda la tinta exclusivamente en el canal alfa."""
    if img.mode != "RGBA":
        return False
    arr = np.asarray(img)
    rgb_var = arr[..., :3].std()
    alpha = arr[..., 3]
    return rgb_var < 1.0 and alpha.std() > 10


def load_image_frames(path: Path | str) -> list[np.ndarray]:
    """Devuelve una lista de imágenes RGB (uint8) en orientación correcta."""
    path = Path(path)
    try:
        img = Image.open(path)
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise CorruptFileError(f"No se puede abrir la imagen «{path.name}»: el archivo está dañado o incompleto.",
                               "Prueba a abrirla con otro programa y guardarla de nuevo como PNG.",
                               str(exc)) from exc
    frames: list[np.ndarray] = []
    n_frames = getattr(img, "n_frames", 1) if (img.format or "").upper() == "TIFF" else 1
    for i in range(n_frames):
        try:
            img.seek(i)
        except EOFError:
            break
        frame = ImageOps.exif_transpose(img.copy()) if i == 0 else img.copy()
        frames.append(np.asarray(_to_rgb_on_white(frame)))
    if not frames:
        raise CorruptFileError(f"La imagen «{path.name}» no contiene datos legibles.")
    return frames


def image_from_bytes(data: bytes) -> np.ndarray:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise CorruptFileError("Los datos del portapapeles no son una imagen válida.", details=str(exc)) from exc
    return np.asarray(_to_rgb_on_white(ImageOps.exif_transpose(img)))


def image_dpi(path: Path | str) -> float | None:
    try:
        with Image.open(path) as img:
            dpi = img.info.get("dpi")
            if dpi and dpi[0] and dpi[0] > 30:
                return float(dpi[0])
    except Exception:
        return None
    return None


def save_png(arr: np.ndarray, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path, format="PNG", optimize=False, compress_level=3)


def to_gray(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        return arr
    import cv2
    return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
