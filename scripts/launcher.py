"""Punto de entrada para el ejecutable empaquetado (PyInstaller)."""

import multiprocessing
import sys

from note_cascade.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
