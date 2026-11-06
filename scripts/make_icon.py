"""Genera el icono de la aplicación (PNG e ICO) sin recursos externos."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "note_cascade" / "resources"


def draw(size: int) -> Image.Image:
    s = 4 * size  # sobremuestreo para bordes suaves
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s))
    px = grad.load()
    a, b = (124, 108, 255), (33, 199, 168)
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    bw = int(s * 0.12)
    for i, (x, h) in enumerate(((0.2, 0.42), (0.44, 0.6), (0.68, 0.32))):
        x0 = int(s * x)
        y0 = int(s * 0.14 + i * s * 0.06)
        d.rounded_rectangle((x0, y0, x0 + bw, y0 + int(s * h)), radius=bw // 3, fill=(255, 255, 255, 235))
    keys = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    kd = ImageDraw.Draw(keys)
    kd.rectangle((0, int(s * 0.8), s, s), fill=(20, 22, 30, 210))
    for k in range(1, 7):
        x = int(s * k / 7)
        kd.line((x, int(s * 0.8), x, s), fill=(255, 255, 255, 60), width=max(1, s // 128))
    keys_masked = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    keys_masked.paste(keys, (0, 0), mask)
    img = Image.alpha_composite(img, keys_masked)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw(256)
    big.save(OUT / "icon.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icono creado en", OUT)


if __name__ == "__main__":
    main()
