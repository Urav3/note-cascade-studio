# -*- mode: python ; coding: utf-8 -*-
# Especificación de PyInstaller para Note Cascade Studio (carpeta, no un único .exe).
# Genera dos ejecutables que comparten las mismas bibliotecas:
#   NoteCascadeStudio.exe          aplicación con ventana
#   NoteCascadeStudio-consola.exe  línea de órdenes (convertir, --diagnostico) y subproceso de HOMR
# Audiveris y los datos de OCR NO se incluyen aquí: build_windows.bat los copia en dist\...\tools.

from PyInstaller.utils.hooks import (collect_all, collect_data_files, collect_dynamic_libs, collect_submodules,
                                     copy_metadata)

datas = []
binaries = []
hiddenimports = []
for pkg in ("pymupdf",):                         # PyMuPDF 2.x: DLL de MuPDF y recursos
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for pkg in ("homr", "pymupdf", "numpy", "opencv-python-headless", "Pillow", "mido", "sounddevice", "rapidocr",
            "onnxruntime", "python-rtmidi", "imageio-ffmpeg", "PySide6-Essentials"):
    try:
        datas += copy_metadata(pkg)               # versiones visibles en Diagnóstico
    except Exception:
        pass
datas += collect_data_files("homr")            # modelos ONNX de HOMR (deben estar descargados)
datas += collect_data_files("rapidocr")        # modelos y configuración del OCR
datas += collect_data_files("_sounddevice_data")
datas += collect_data_files("imageio_ffmpeg")
datas += collect_data_files("musicxml")
datas += [("note_cascade/resources", "note_cascade/resources")]
binaries += collect_dynamic_libs("onnxruntime") + collect_dynamic_libs("rtmidi")
hiddenimports += (collect_submodules("homr") + collect_submodules("note_cascade") +
                  ["mido.backends.rtmidi", "rtmidi", "sounddevice", "pymupdf"])
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf",
            "IPython", "pytest", "music21", "verovio"]

a = Analysis(["scripts/launcher.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], runtime_hooks=[], excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)

exe_gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NoteCascadeStudio", debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False, console=False,
              icon="note_cascade/resources/icon.ico")
exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NoteCascadeStudio-consola", debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False, console=True,
              icon="note_cascade/resources/icon.ico")
coll = COLLECT(exe_gui, exe_cli, a.binaries, a.datas, strip=False, upx=False, name="NoteCascadeStudio")
