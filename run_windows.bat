@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\NoteCascadeStudio\NoteCascadeStudio.exe" if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0dist\NoteCascadeStudio\NoteCascadeStudio.exe" %*
  exit /b 0
)
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo No se encuentra la instalacion. Ejecuta primero install_windows.bat
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m note_cascade %*
