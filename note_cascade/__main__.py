"""Punto de entrada.

    python -m note_cascade                  → abre la aplicación
    python -m note_cascade --diagnostico    → comprueba dependencias y motores
    python -m note_cascade convertir ARCHIVO [--salida CARPETA] [--motor auto|audiveris|homr|oemer]
                                     [--verificacion auto|siempre|nunca] [--paginas 1-3,5]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _parse_pages(text: str | None) -> list[int] | None:
    if not text:
        return None
    pages: list[int] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            pages.extend(range(int(a) - 1, int(b)))
        elif chunk:
            pages.append(int(chunk) - 1)
    return pages


def cli_convert(args: argparse.Namespace) -> int:
    from note_cascade.app.config import load_config
    from note_cascade.app.errors import NoteCascadeError
    from note_cascade.export.exporter import ExportRequest, export_project
    from note_cascade.importers.document import open_document
    from note_cascade.omr.registry import create_engines
    from note_cascade.pipeline.conversion import ConversionOptions, ConversionPipeline

    cfg = load_config()
    if args.motor:
        cfg.processing.engine = args.motor
    if args.verificacion:
        cfg.processing.cross_check = args.verificacion
    if args.correccion:
        cfg.processing.image_correction = args.correccion
    last = {"pct": -1}

    def progress(frac: float, msg: str) -> None:
        pct = int(frac * 100)
        if pct != last["pct"]:
            last["pct"] = pct
            print(f"  {pct:3d} %  {msg}", flush=True)

    try:
        doc = open_document(args.archivo)
        pipeline = ConversionPipeline(cfg, create_engines(cfg), progress, log_fn=lambda t: print("  · " + t))
        options = ConversionOptions.from_config(cfg)
        options.pages = _parse_pages(args.paginas)
        options.use_cache = not args.sin_cache
        project = pipeline.convert(doc, options)
        out_dir = Path(args.salida) if args.salida else cfg.output_dir()
        req = ExportRequest(out_dir, Path(args.archivo).stem, midi_type0=args.tipo0, video=False)
        outcomes = export_project(project, req, cfg)
    except NoteCascadeError as exc:
        print("ERROR: " + exc.user_text(), file=sys.stderr)
        return 2
    counts = {"error": 0, "aviso": 0, "info": 0}
    for i in project.issues:
        if i.status == "abierta":
            counts[i.severity] += 1
    print(f"\nResultado: {len(project.score.measures)} compases, {len(project.score.pitched_notes())} notas; "
          f"{counts['error']} errores probables, {counts['aviso']} avisos, {counts['info']} notas informativas.")
    ok = True
    for o in outcomes:
        print(f"  [{'OK' if o.ok else 'FALLO'}] {o.kind}: {o.path}\n         {o.message}")
        ok = ok and o.ok
    return 0 if ok else 1


def _run_embedded_homr(args: list[str]) -> int:
    """Permite que el ejecutable empaquetado lance HOMR en un subproceso (no hay python.exe aparte)."""
    import os
    import warnings
    warnings.filterwarnings("ignore")
    # En un ejecutable sin consola, stdout puede ser None aunque el proceso padre haya
    # redirigido la salida a una tubería: se reabre desde el descriptor del sistema.
    for fd, name in ((1, "stdout"), (2, "stderr")):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(fd, "w", encoding="utf-8", errors="replace", closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, "w"))
    sys.argv = ["homr"] + args
    from homr.main import main as homr_main
    homr_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "--ejecutar-homr":
        return _run_embedded_homr(raw[1:])
    parser = argparse.ArgumentParser(prog="note_cascade", description="Note Cascade Studio")
    parser.add_argument("--diagnostico", action="store_true", help="comprueba dependencias y motores")
    parser.add_argument("--depurar", action="store_true", help="registro detallado")
    sub = parser.add_subparsers(dest="cmd")
    conv = sub.add_parser("convertir", help="convierte un archivo sin abrir la interfaz")
    conv.add_argument("archivo")
    conv.add_argument("--salida")
    conv.add_argument("--motor", choices=["auto", "audiveris", "homr", "oemer"])
    conv.add_argument("--verificacion", choices=["auto", "siempre", "nunca"])
    conv.add_argument("--correccion", choices=["auto", "ninguna", "suave", "fuerte"])
    conv.add_argument("--paginas")
    conv.add_argument("--tipo0", action="store_true", help="exporta también MIDI tipo 0")
    conv.add_argument("--sin-cache", action="store_true", dest="sin_cache")
    parser.add_argument("archivos", nargs="*", help="archivos para abrir en la aplicación")
    args, unknown = parser.parse_known_args(argv)

    from note_cascade.app.logging_setup import setup_logging
    setup_logging(logging.DEBUG if args.depurar else logging.INFO)

    if args.diagnostico:
        from note_cascade.app.config import load_config
        from note_cascade.app.diagnostics import run_diagnostics
        rep = run_diagnostics(load_config())
        print(rep.text())
        return 0 if rep.ok else 1
    if args.cmd == "convertir":
        return cli_convert(args)
    from note_cascade.ui.main_window import run_gui
    return run_gui(args.archivos)


if __name__ == "__main__":
    sys.exit(main())
